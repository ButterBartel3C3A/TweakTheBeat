"""Profile loader: TOML -> validated Profile object (design.md section 2).

Validation failures raise BleCliError(profile_invalid) with a path-prefixed
message; a broken/missing hook module raises profile_hook_error.
"""

from __future__ import annotations

import importlib.util
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..core.frames import FrameTable, make_frame_table, make_frame_type
from ..errors import BleCliError, PROFILE_HOOK_ERROR, PROFILE_INVALID, PROFILE_NOT_FOUND
from ..util import Pattern, parse_hex


@dataclass
class ExpectItem:
    name: str
    pattern: Pattern


@dataclass
class Handshake:
    deadline_ms: int
    sequence: list[bytes]
    expect: list[ExpectItem]


@dataclass
class Profile:
    path: Path
    name: str
    description: str
    version: str
    # device discovery
    device_name_filters: list[str]
    device_address: str | None
    device_wake_hint: str | None
    scan_timeout_s: float
    connect_timeout_s: float
    # gatt layout
    service_uuids: list[str]
    write_char_uuid: str
    write_with_response: bool
    write_max_packet: int
    notify_char_uuid: str | None
    notify_max_packet: int | None
    # handshake + frames
    handshake: Handshake | None
    frame_table: FrameTable
    hooks: dict[str, Callable] = field(default_factory=dict, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def char_uuids(self) -> list[str]:
        uuids = [self.write_char_uuid]
        if self.notify_char_uuid:
            uuids.append(self.notify_char_uuid)
        return uuids


def load_profile(path: str | Path) -> Profile:
    p = Path(path)
    if not p.exists():
        raise BleCliError(PROFILE_NOT_FOUND, f"profile file not found: {p}")
    try:
        raw = tomllib.loads(p.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise BleCliError(PROFILE_INVALID, f"{p}: TOML syntax error: {exc}") from exc
    return _build(p, raw)

# ---------------------------------------------------------------- validation

def _fail(p: Path, msg: str) -> None:
    raise BleCliError(PROFILE_INVALID, f"{p}: {msg}")


def _check_keys(p: Path, table: dict, allowed: set[str], section: str) -> None:
    for key in table:
        if key not in allowed:
            _fail(p, f"unknown key '{key}' in [{section}] (allowed: {sorted(allowed)})")


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def _parse_hex_field(p: Path, table: dict, key: str, where: str) -> bytes:
    try:
        return parse_hex(str(table[key]))
    except ValueError as exc:
        _fail(p, f"{where}.{key}: {exc}")


def _build(p: Path, raw: dict[str, Any]) -> Profile:
    _check_keys(p, raw, {"meta", "device", "gatt", "handshake", "frames", "hooks"}, "<root>")

    # [meta]
    meta = raw.get("meta") or {}
    _check_keys(p, meta, {"name", "version", "description"}, "meta")
    name = meta.get("name")
    if not name:
        _fail(p, "[meta].name is required")

    # [device]
    device = raw.get("device") or {}
    _check_keys(p, device, {"name_filter", "name_filters", "address", "wake_hint",
                            "scan_timeout_s", "connect_timeout_s"}, "device")
    filters = []
    for key in ("name_filter", "name_filters"):
        if key in device:
            filters.extend(_as_list(device[key]))
    if not filters and not device.get("address"):
        _fail(p, "[device] needs name_filter or address")

    # [gatt]
    gatt = raw.get("gatt") or {}
    _check_keys(p, gatt, {"services", "chars"}, "gatt")
    services_raw = gatt.get("services") or []
    service_uuids = []
    for i, svc in enumerate(services_raw):
        _check_keys(p, svc, {"service", "uuid"}, f"gatt.services[{i}]")
        svc_uuid = svc.get("service") or svc.get("uuid")
        if not svc_uuid:
            _fail(p, f"[gatt.services[{i}]] needs a service uuid")
        service_uuids.append(str(svc_uuid))

    chars = gatt.get("chars") or {}
    _check_keys(p, chars, {"write", "notify"}, "gatt.chars")
    write = chars.get("write") or {}
    _check_keys(p, write, {"uuid", "write_type", "max_packet"}, "gatt.chars.write")
    if not write.get("uuid"):
        _fail(p, "[gatt.chars.write].uuid is required")
    write_type = write.get("write_type", "write_without_response")
    if write_type not in ("write_without_response", "write_with_response"):
        _fail(p, f"[gatt.chars.write].write_type {write_type!r} not in "
                 "{write_without_response, write_with_response}")

    notify_uuid = None
    notify_max = None
    if chars.get("notify"):
        notify = chars["notify"]
        _check_keys(p, notify, {"uuid", "cccd", "max_packet"}, "gatt.chars.notify")
        if not notify.get("uuid"):
            _fail(p, "[gatt.chars.notify].uuid is required")
        notify_uuid = str(notify["uuid"])
        if "max_packet" in notify:
            notify_max = int(notify["max_packet"])

    # [handshake]
    handshake = None
    if raw.get("handshake"):
        hs = raw["handshake"]
        _check_keys(p, hs, {"deadline_ms", "sequence", "expect"}, "handshake")
        seq = []
        for i, step in enumerate(hs.get("sequence") or []):
            _check_keys(p, step, {"write"}, f"handshake.sequence[{i}]")
            seq.append(_parse_hex_field(p, step, "write", f"handshake.sequence[{i}]"))
        expect = []
        for i, spec in enumerate(hs.get("expect") or []):
            _check_keys(p, spec, {"pattern", "prefix", "len", "name"}, f"handshake.expect[{i}]")
            ft = make_frame_type(spec, default_name=f"expect{i}")
            expect.append(ExpectItem(name=ft.name, pattern=ft.pattern))
        if not seq and not expect:
            _fail(p, "[handshake] needs sequence or expect")
        handshake = Handshake(deadline_ms=int(hs.get("deadline_ms", 5000)),
                              sequence=seq, expect=expect)

    # [hooks] (loaded before frames so decode hooks are wired into the table)
    hooks = {}
    if raw.get("hooks"):
        hk = raw["hooks"]
        _check_keys(p, hk, {"module"}, "hooks")
        hooks = _load_hooks(p, str(hk["module"]))

    # [frames]
    frame_table = FrameTable([])
    if raw.get("frames"):
        frames = raw["frames"]
        _check_keys(p, frames, {"byteorder", "types"}, "frames")
        byteorder = frames.get("byteorder", "big")
        if byteorder not in ("big", "little"):
            _fail(p, f"[frames].byteorder {byteorder!r} not in {{big, little}}")
        frame_table = make_frame_table(frames.get("types") or [], byteorder=byteorder,
                                       hooks=hooks)

    profile = Profile(
        path=p,
        name=str(name),
        description=str(meta.get("description", "")),
        version=str(meta.get("version", "")),
        device_name_filters=filters,
        device_address=device.get("address"),
        device_wake_hint=device.get("wake_hint"),
        scan_timeout_s=float(device.get("scan_timeout_s", 5.0)),
        connect_timeout_s=float(device.get("connect_timeout_s", 10.0)),
        service_uuids=service_uuids,
        write_char_uuid=str(write["uuid"]),
        write_with_response=(write_type == "write_with_response"),
        write_max_packet=int(write.get("max_packet", 20)),
        notify_char_uuid=notify_uuid,
        notify_max_packet=notify_max,
        handshake=handshake,
        frame_table=frame_table,
        hooks=hooks,
        raw=raw,
    )

    # every referenced hook must exist and be callable
    for ft in frame_table.types:
        if ft.hook and ft.hook not in hooks:
            raise BleCliError(PROFILE_HOOK_ERROR,
                              f"{p}: frame type '{ft.name}' references hook '{ft.hook}' "
                              "but [hooks].module does not provide it")
    return profile


def _load_hooks(p: Path, module_name: str) -> dict[str, Callable]:
    hook_file = p.parent / f"{module_name}.py"
    if not hook_file.exists():
        raise BleCliError(PROFILE_HOOK_ERROR, f"{p}: hook module {hook_file} not found")
    try:
        spec = importlib.util.spec_from_file_location(f"blecli_hooks_{module_name}", hook_file)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except BleCliError:
        raise
    except Exception as exc:
        raise BleCliError(PROFILE_HOOK_ERROR, f"{p}: hook module failed to import: {exc}") from exc
    return {n: getattr(mod, n) for n in dir(mod)
            if not n.startswith("_") and callable(getattr(mod, n))}
