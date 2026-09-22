"""Human-mode REPL: interactive shell over the same core layer.

Keeps ONE persistent session across commands (unlike AI mode, which
re-connects per command).  Uplinks are recorded continuously; ``sub N``
prints them live for N seconds.  Colors are plain ANSI, only emitted when
stdout is a TTY.
"""

from __future__ import annotations

import asyncio
import sys

from .core.connection import Session, close_session, open_session
from .core.discovery import scan as core_scan
from .errors import BleCliError
from .profiles.loader import load_profile
from .transport.bleak_backend import BleakBackend
from .util import fmt_hex, parse_hex

_USE_COLOR = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if _USE_COLOR else text


def red(text: str) -> str:
    return _c("31", text)


def green(text: str) -> str:
    return _c("32", text)


def bold(text: str) -> str:
    return _c("1", text)


def yellow(text: str) -> str:
    return _c("33", text)


HELP = """\
命令（REPL 保持一条连接，与 --json 模式逐条命令重连不同）:
  scan [秒]           扫描并列出设备（默认 5s）
  connect [MAC]       连接设备（默认按 profile 名称过滤扫描）
  init                使能通知 + 执行 profile 握手序列
  gatt                打印 GATT 树
  write HEX [N]       写一帧；N 秒内打印上行
  sub N               订阅收 N 秒上行（实时打印）
  disconnect          断开连接
  help / quit         帮助 / 退出
"""


async def run_repl(args) -> None:
    print(bold("ble-cli 交互模式"), "（输入 help 查看命令，quit 退出）")
    profile = None
    if args.profile:
        profile = load_profile(args.profile)
    else:
        print(yellow("提示: 未指定 --profile，connect/init/write/sub 不可用；scan 可用。"))
    backend = BleakBackend()
    session: Session | None = None

    async def disconnect():
        nonlocal session
        if session is not None:
            await close_session(session)
            session = None
            print(green("已断开"))

    while True:
        try:
            line = input("ble> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        parts = line.split()
        if not parts:
            continue
        cmd = parts[0].lower()

        try:
            if cmd in ("quit", "exit", "q"):
                break
            if cmd == "help":
                print(HELP)
            elif cmd == "scan":
                timeout = float(parts[1]) if len(parts) > 1 else 5.0
                devices = await core_scan(backend, timeout)
                if not devices:
                    print(yellow("（无设备）"))
                for d in devices:
                    print(f"  {d.address}  {d.rssi or '':>4}  {d.name or ''}")
            elif cmd == "connect":
                if profile is None:
                    print(red("需要 --profile"))
                    continue
                await disconnect()
                session = await open_session(backend, profile, address=parts[1] if len(parts) > 1 else None,
                                             state_address=None, run_handshake=False)
                print(green(f"已连接 {session.address}"), f"({session.device_name or '?'}) MTU={session.mtu}")
            elif cmd == "init":
                if session is None:
                    print(red("先 connect"))
                    continue
                # init = full session with handshake; easiest correct path is reopen
                addr = session.address
                await disconnect()
                session = await open_session(backend, profile, address=addr,
                                             state_address=None, run_handshake=True)
                print(green(f"握手完成 ({len(session.handshake)} 条上行):"))
                for u in session.handshake:
                    _print_uplink(u)
            elif cmd == "gatt":
                if session is None:
                    print(red("先 connect"))
                    continue
                for svc in session.services:
                    print(bold(f"服务 {svc.uuid}"))
                    for ch in svc.characteristics:
                        print(f"  {ch.uuid}  [{', '.join(ch.properties)}]")
            elif cmd == "write":
                if session is None:
                    print(red("先 connect"))
                    continue
                if len(parts) < 2:
                    print(red("用法: write HEX [N]"))
                    continue
                try:
                    frame = parse_hex(parts[1])
                except ValueError as exc:
                    print(red(f"hex 错误: {exc}"))
                    continue
                session.recorder.clear()
                await session.backend.write(profile.write_char_uuid, frame, profile.write_with_response)
                print(green(f"已写 {fmt_hex(frame)}"))
                if len(parts) > 2:
                    await _listen(session, float(parts[2]))
            elif cmd == "sub":
                if session is None:
                    print(red("先 connect"))
                    continue
                if len(parts) < 2:
                    print(red("用法: sub N"))
                    continue
                await _listen(session, float(parts[1]))
            elif cmd == "disconnect":
                await disconnect()
            else:
                print(red(f"未知命令 {cmd!r}（help 查看）"))
        except BleCliError as exc:
            print(red(f"[{exc.code}] {exc.message}"))
        except Exception as exc:  # keep the shell alive on surprises
            print(red(f"内部错误: {type(exc).__name__}: {exc}"))

    await disconnect()


async def _listen(session: Session, seconds: float) -> None:
    session.recorder.clear()
    seen = 0

    def on_frame(payload: bytes):
        nonlocal seen
        seen += 1
        rec = session.recorder.frame_table.record(payload) if session.recorder.frame_table else {
            "type": "unknown", "hex": fmt_hex(payload), "fields": {}}
        _print_uplink(rec)

    # bleak captures the callback at subscribe time, so re-subscribe with a
    # live printer for the duration, then restore the recorder handler
    try:
        await session.backend.stop_notify(session.profile.notify_char_uuid)
        await session.backend.start_notify(session.profile.notify_char_uuid, on_frame)
        await asyncio.sleep(seconds)
    finally:
        await session.backend.stop_notify(session.profile.notify_char_uuid)
        await session.backend.start_notify(session.profile.notify_char_uuid,
                                           session.recorder.on_notify)
    if seen == 0:
        print(yellow(f"（{seconds:g}s 内无上行）"))


def _print_uplink(rec: dict) -> None:
    fields = " ".join(f"{k}={v}" for k, v in rec.get("fields", {}).items())
    line = f"  {rec.get('ts', '').split('T')[1] if rec.get('ts') else ''}  {bold(rec.get('type', '?')):>12}  {rec.get('hex', '')}"
    if fields:
        line += f"  | {fields}"
    print(line)
