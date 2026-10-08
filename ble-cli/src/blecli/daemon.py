"""Daemon (D11): a background process holds ONE BLE connection so that AI
mode commands route through a state-file channel instead of re-connecting
per command.

Channel protocol (all files live in the state-file's directory):

- ``daemon.json``          daemon record: pid / profile / started_at / idle
                           (separate file on purpose — state.json is
                           read-modify-write and would race the daemon)
- ``daemon-cmd.json``      command: {"id": "<uuid>", "command": ..., "args": {...}}
- ``daemon-result-<id>.json``  result: {"data": ..., "error": {code,message} | null}

The daemon polls the command file (single writer, single reader — commands
are naturally serialized), executes, writes the result file, removes the
command file.  AI-side commands detect a live daemon and route through the
channel; on any channel failure they fall back to a direct connection.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from .core.connection import Session, close_session, open_session
from .core.gatt import gatt_tree
from .errors import BleCliError, DEVICE_BUSY, DEVICE_NOT_FOUND, DISCONNECTED

CMD_FILE = "daemon-cmd.json"
RECORD_FILE = "daemon.json"
RESULT_TMPL = "daemon-result-{id}.json"
DEFAULT_IDLE_TIMEOUT_S = 600.0
CMD_POLL_S = 0.1
RESULT_TIMEOUT_S = 15.0
PING_TIMEOUT_S = 2.0


# ---------------------------------------------------------------- channels

def _dir(state_path: Path) -> Path:
    return state_path.parent


def _record_path(state_path: Path) -> Path:
    return _dir(state_path) / RECORD_FILE


def read_record(state_path: Path) -> dict[str, Any] | None:
    p = _record_path(state_path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_record(state_path: Path, rec: dict[str, Any]) -> None:
    p = _record_path(state_path)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def clear_record(state_path: Path) -> None:
    try:
        _record_path(state_path).unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------- request side

async def route_or_none(state_path: Path, command: str, args: dict[str, Any],
                        timeout_s: float = RESULT_TIMEOUT_S) -> dict[str, Any] | None:
    """Transparent routing: if a live daemon holds the connection, execute
    through the channel and return its result dict {"data", "error"}.
    Returns None when no daemon is running (caller falls back to direct)."""
    rec = read_record(state_path)
    if rec is None:
        return None
    req_id = uuid.uuid4().hex
    cmd_path = _dir(state_path) / CMD_FILE
    if cmd_path.exists():  # stale command from a crashed daemon
        cmd_path.unlink(missing_ok=True)
    tmp = cmd_path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"id": req_id, "command": command, "args": args}),
                   encoding="utf-8")
    os.replace(tmp, cmd_path)

    result_path = _dir(state_path) / RESULT_TMPL.format(id=req_id)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if result_path.exists():
            try:
                out = json.loads(result_path.read_text(encoding="utf-8"))
                result_path.unlink(missing_ok=True)
                return out
            except (OSError, json.JSONDecodeError):
                pass  # partial write; daemon will rewrite
        await asyncio.sleep(0.05)
    # channel unresponsive: clean up and let the caller fall back
    cmd_path.unlink(missing_ok=True)
    return None


def is_alive_sync(state_path: Path, timeout_s: float = PING_TIMEOUT_S) -> bool:
    """Synchronous ping probe (used by start/stop/status and mutex checks)."""
    rec = read_record(state_path)
    if rec is None:
        return False
    req_id = uuid.uuid4().hex
    d = _dir(state_path)
    cmd_path = d / CMD_FILE
    tmp = cmd_path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"id": req_id, "command": "ping", "args": {}}),
                   encoding="utf-8")
    os.replace(tmp, cmd_path)
    result_path = d / RESULT_TMPL.format(id=req_id)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if result_path.exists():
            result_path.unlink(missing_ok=True)
            return True
        time.sleep(0.05)
    cmd_path.unlink(missing_ok=True)
    return False


# ---------------------------------------------------------------- daemon side

async def serve(args) -> None:
    """Foreground service loop (``daemon serve``, launched in background by
    ``daemon start``).  Holds the connection until stop / idle timeout."""
    from .profiles.loader import load_profile

    state_path = Path(args.state_file)
    profile = load_profile(args.profile)
    idle_timeout = float(getattr(args, "idle_timeout", DEFAULT_IDLE_TIMEOUT_S))

    backend = None
    session: Session | None = None

    async def ensure_session() -> Session:
        nonlocal backend, session
        if session is None:
            from .transport.bleak_backend import BleakBackend
            backend = BleakBackend()
            session = await open_session(backend, profile, address=args.address,
                                         state_address=None)
        return session

    _write_record(state_path, {
        "pid": os.getpid(), "profile": str(args.profile),
        "started_at": time.time(), "idle_timeout_s": idle_timeout,
    })
    try:
        await ensure_session()  # eager connect; device asleep -> lazy retry
    except BleCliError as exc:
        rec = read_record(state_path) or {}
        rec["connect_error"] = exc.code
        _write_record(state_path, rec)

    async def handle(cmd: dict[str, Any]) -> dict[str, Any]:
        nonlocal backend, session
        command = cmd["command"]
        a = cmd.get("args") or {}
        if command == "ping":
            return {"data": {"pong": True}}
        if command == "stop":
            return {"data": {"stopped": True}, "__stop__": True}
        if command == "disconnect":
            await close_session(session) if session else None
            session = None
            return {"data": {"disconnected": True}}
        # device commands: reconnect lazily if the link dropped
        for attempt in (1, 2):
            try:
                s = await ensure_session()
                if command == "connect":
                    return {"data": {
                        "connected": True, "address": s.address,
                        "device_name": s.device_name, "profile": s.profile.name,
                        "mtu": s.mtu,
                        "services_found": {
                            svc: [c.uuid for c in by_uuid(s, svc)]
                            for svc in s.profile.service_uuids},
                        "handshake": s.handshake,
                    }}
                if command == "init":
                    return {"data": {"handshake": s.handshake,
                                     "address": s.address}}
                if command == "gatt":
                    return {"data": gatt_tree(s.services)}
                if command == "write":
                    from .util import parse_hex
                    frame = parse_hex(str(a["hex"]))
                    s.recorder.clear()
                    await s.backend.write(s.profile.write_char_uuid, frame,
                                          s.profile.write_with_response)
                    out: dict[str, Any] = {
                        "written": [" ".join(f"{b:02X}" for b in frame)]}
                    listen = float(a.get("listen") or 0.0)
                    if listen > 0:
                        await asyncio.sleep(listen)
                        out["uplinks"] = s.recorder.snapshot()
                    return {"data": out}
                if command == "sub":
                    s.recorder.clear()
                    await asyncio.sleep(float(a["timeout"]))
                    return {"data": {"uplinks": s.recorder.snapshot()}}
                return {"error": {"code": "usage_error",
                                  "message": f"daemon 不支持的命令 {command!r}"}}
            except BleCliError as exc:
                if exc.code == DISCONNECTED and attempt == 1:
                    await close_session(session) if session else None
                    session = None
                    continue
                return {"error": {"code": exc.code, "message": exc.message}}

    try:
        await run_channel(state_path, idle_timeout, handle)
    finally:
        if session is not None:
            await close_session(session)
        clear_record(state_path)


async def run_channel(state_path: Path, idle_timeout_s: float,
                      handler) -> None:
    """The channel loop, isolated for testability: poll the command file,
    hand each command to ``handler(cmd) -> dict``, write the result file,
    remove the command file.  Exits on ``__stop__`` or idle timeout."""
    last_active = time.monotonic()
    d = _dir(state_path)
    cmd_path = d / CMD_FILE
    while True:
        if cmd_path.exists():
            try:
                cmd = json.loads(cmd_path.read_text(encoding="utf-8"))
                cmd_path.unlink(missing_ok=True)
            except (OSError, json.JSONDecodeError):
                cmd = None  # partial write; requester will retry
            if cmd:
                out = await handler(cmd)
                result_path = d / RESULT_TMPL.format(id=cmd["id"])
                tmp = result_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(out, ensure_ascii=False),
                               encoding="utf-8")
                os.replace(tmp, result_path)
                last_active = time.monotonic()
                if out.get("__stop__"):
                    break
        else:
            await asyncio.sleep(CMD_POLL_S)
        if time.monotonic() - last_active > idle_timeout_s:
            break


def by_uuid(session: Session, uuid_str: str) -> Any:
    for svc in session.services:
        if svc.uuid == uuid_str:
            return svc
    raise BleCliError("service_not_found", f"service {uuid_str} not in session")


# ---------------------------------------------------------------- lifecycle

def start_daemon(args, state_path: Path) -> dict[str, Any]:
    """Launch ``daemon serve`` in the background (no console window)."""
    if is_alive_sync(state_path):
        return {"started": False, "already_running": True}
    log_path = _dir(state_path) / "daemon.log"
    profile = str(args.profile)
    cmd = [sys.executable, "-m", "blecli", "daemon", "serve",
           "--profile", profile, "--state-file", str(state_path)]
    if getattr(args, "address", None):
        cmd += ["--address", str(args.address)]
    idle = float(getattr(args, "idle_timeout", DEFAULT_IDLE_TIMEOUT_S))
    cmd += ["--idle-timeout", str(idle)]
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    with open(log_path, "a", encoding="utf-8") as log:
        subprocess.Popen(cmd, stdout=log, stderr=log, close_fds=True,
                         creationflags=flags)
    # wait for the daemon record to appear (serve writes it on startup)
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        rec = read_record(state_path)
        if rec and rec.get("pid") != os.getpid():
            return {"started": True, "pid": rec["pid"],
                    "log": str(log_path), "idle_timeout_s": idle}
        time.sleep(0.2)
    return {"started": False, "error": "daemon 未就绪（见 daemon.log）"}


def stop_daemon(state_path: Path) -> dict[str, Any]:
    """Ask the daemon to stop; fall back to terminating the recorded pid."""
    rec = read_record(state_path)
    if rec is None:
        return {"stopped": False, "reason": "no daemon record"}
    req_id = uuid.uuid4().hex
    d = _dir(state_path)
    cmd_path = d / CMD_FILE
    if cmd_path.exists():
        cmd_path.unlink(missing_ok=True)
    tmp = cmd_path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"id": req_id, "command": "stop", "args": {}}),
                   encoding="utf-8")
    os.replace(tmp, cmd_path)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if read_record(state_path) is None:
            return {"stopped": True, "pid": rec["pid"]}
        time.sleep(0.1)
    # graceful stop timed out: hard-kill the recorded pid
    try:
        os.kill(int(rec["pid"]), 9)
    except OSError:
        pass
    clear_record(state_path)
    return {"stopped": True, "pid": rec["pid"], "killed": True}


def assert_not_busy(state_path: Path) -> None:
    """Single-connection mutex (D11/B4): cases run and repl must not start
    while a daemon holds the device."""
    if is_alive_sync(state_path):
        raise BleCliError(DEVICE_BUSY,
                          "daemon 正持有设备连接，请先执行 daemon stop")
