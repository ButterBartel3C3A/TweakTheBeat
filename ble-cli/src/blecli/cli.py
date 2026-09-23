"""CLI entry point: argparse tree + JSON envelope dispatch (D8).

Usage errors are JSON-ified too (exit 2, error.code=usage_error); execution
failures exit 1 with a specific error.code; success exits 0.

Every device command opens a fresh session (connect -> GATT verify ->
notify subscribe -> profile handshake replay) and closes it before
returning — BLE links cannot cross process boundaries (design.md section 4).
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .core.connection import Session, close_session, open_session
from .core.discovery import scan
from .core.gatt import gatt_tree
from .core.state import State
from .errors import BleCliError, EXIT_OK, INTERNAL_ERROR
from .output import ArgParser, Envelope, internal_error
from .profiles.loader import Profile, load_profile
from .transport.bleak_backend import BleakBackend
from .util import parse_hex

DEFAULT_STATE_FILE = ".local/runs/state.json"


# ---------------------------------------------------------------- argparse

def build_parser() -> ArgParser:
    p = ArgParser(
        prog="ble-cli",
        description="Protocol-agnostic BLE debugging CLI (humans + AI agents).",
    )
    p.add_argument("--json", action="store_true", help="structured JSON envelope output")
    p.add_argument("--profile", metavar="PATH", default=os.environ.get("BLE_CLI_PROFILE"),
                   help="profile.toml path (or env BLE_CLI_PROFILE)")
    p.add_argument("--state-file", metavar="PATH",
                   default=os.environ.get("BLE_CLI_STATE_FILE", DEFAULT_STATE_FILE),
                   help="state file path (default .local/runs/state.json)")
    p.add_argument("--address", metavar="MAC", help="device address override")

    sub = p.add_subparsers(dest="command", parser_class=ArgParser)

    s = sub.add_parser("scan", help="scan for BLE devices")
    s.add_argument("--timeout", type=float, default=5.0)
    s.add_argument("--filter", dest="filters", nargs="*", metavar="NAME",
                   help="glob name patterns to keep")

    for name, help_text in [("connect", "connect and verify the profile GATT layout"),
                            ("init", "connect, subscribe, run the profile handshake")]:
        c = sub.add_parser(name, help=help_text)
        c.add_argument("--no-handshake", action="store_true",
                       help="skip the handshake sequence (connect only)")

    sub.add_parser("gatt", help="dump the GATT tree of the connected device")

    w = sub.add_parser("write", help="write a hex frame, optionally listen for uplinks")
    w.add_argument("hex", metavar="HEX")
    w.add_argument("--listen", type=float, metavar="SECONDS", default=0.0,
                   help="collect uplinks for N seconds after the write")

    su = sub.add_parser("sub", help="subscribe and collect uplinks for N seconds")
    su.add_argument("--timeout", type=float, required=True, metavar="SECONDS")

    sub.add_parser("disconnect", help="clear the remembered device in the state file")

    cf = sub.add_parser("confirm", help="answer a pending physical-stimulus confirmation")
    cf.add_argument("--case-id", metavar="ID", help="case id to confirm (default: the pending one)")
    cf.add_argument("--yes", dest="answer", action="store_const", const="yes", default="yes")
    cf.add_argument("--no", dest="answer", action="store_const", const="no")
    cf.add_argument("--note", metavar="TEXT")

    cl = sub.add_parser("cases", help="case document operations")
    cases_sub = cl.add_subparsers(dest="cases_command", parser_class=ArgParser)
    lst = cases_sub.add_parser("list", help="parse and list cases from the doc")
    lst.add_argument("--doc", metavar="PATH", required=True)
    lst.add_argument("--config", metavar="PATH", help="cases config TOML (parse options)")
    lst.add_argument("--validate", action="store_true", help="include a validation report")
    lst.add_argument("--group", metavar="G", help="only this group")

    run = cases_sub.add_parser("run", help="execute cases against the device")
    run.add_argument("--doc", metavar="PATH", required=True)
    run.add_argument("--rules", metavar="DIR", required=True, help="assertion rules directory")
    run.add_argument("--config", metavar="PATH")
    run.add_argument("--id", metavar="CASE", action="append", dest="ids",
                     help="run only these case ids (repeatable)")
    run.add_argument("--group", metavar="G", help="run only this group")
    run.add_argument("--confirm-timeout", type=float, default=600.0,
                     help="seconds to wait for a tester confirmation")
    run.add_argument("--window", type=int, default=3000,
                     help="uplink collection window (ms) for rule-less cases")
    run.add_argument("--out", metavar="PATH", help="report output path (without extension)")

    rep = sub.add_parser("report", help="summarize a report (JSON sidecar)")
    rep.add_argument("--path", metavar="PATH", help="report path (default: last run)")

    sub.add_parser("repl", help="interactive human-mode shell")

    return p


# ---------------------------------------------------------------- dispatch

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    envelope = Envelope(command="usage")
    try:
        args = parser.parse_args(argv)
    except BleCliError as exc:  # argparse usage errors are raised, not printed
        envelope.error = exc
        return envelope.emit()
    envelope.command = args.command or "usage"
    envelope.state_file = args.state_file

    if not args.command:
        if args.json:
            envelope.error = BleCliError.usage("no command given")
            return envelope.emit()
        parser.print_help()
        return EXIT_OK

    try:
        data = asyncio.run(_dispatch(args, envelope))
        envelope.data = data
    except BleCliError as exc:
        envelope.error = exc
    except KeyboardInterrupt:
        envelope.error = BleCliError(INTERNAL_ERROR, "interrupted")
    except Exception as exc:  # noqa: BLE001 - last-resort envelope, never a traceback
        envelope.error = internal_error(exc)
    return envelope.emit()


async def _dispatch(args, envelope: Envelope) -> dict[str, Any]:
    command = args.command
    if command == "scan":
        backend = BleakBackend()
        devices = await scan(backend, args.timeout, args.filters)
        return {"devices": [d.to_dict() for d in devices]}

    if command == "repl":
        from .repl import run_repl
        await run_repl(args)
        return {}

    if command == "disconnect":
        state = _state(args)
        prev = state.data.get("device")
        state.set_device(None, None)
        state.log("disconnect: cleared remembered device")
        state.save()
        return {"disconnected": True, "previous": prev}

    if command == "confirm":
        return _do_confirm(args)

    if command in ("cases",):
        return await _do_cases(args, envelope)

    if command == "report":
        from .cases.report import read_summary
        state = _state(args)
        path = args.path or state.data.get("last_report")
        if not path:
            raise BleCliError("usage_error", "no report path given and none recorded in state")
        try:
            return read_summary(path)
        except FileNotFoundError as exc:
            raise BleCliError("cases_parse_failed", str(exc)) from exc

    # --- device commands ------------------------------------------------
    profile = _profile(args)
    if command in ("connect", "init"):
        return await _with_session(args, profile, _do_connect,
                                   run_handshake=not getattr(args, "no_handshake", False))
    if command == "gatt":
        return await _with_session(args, profile, _do_gatt)
    if command == "write":
        try:
            frame = parse_hex(args.hex)
        except ValueError as exc:
            raise BleCliError.usage(f"bad hex: {exc}") from None
        return await _with_session(args, profile, _do_write, frame=frame, listen_s=args.listen)
    if command == "sub":
        return await _with_session(args, profile, _do_listen, listen_s=args.timeout)

    raise BleCliError(INTERNAL_ERROR, f"unknown command {command}")


# ---------------------------------------------------------------- helpers

def _profile(args) -> Profile:
    if not args.profile:
        raise BleCliError.usage("--profile is required for this command (or set BLE_CLI_PROFILE)")
    return load_profile(args.profile)


def _state(args) -> State:
    return State(Path(args.state_file))


async def _with_session(args, profile: Profile, fn: Callable,
                        run_handshake: bool = True, **kwargs) -> dict[str, Any]:
    backend = BleakBackend()
    state = _state(args)
    state_address = (state.data.get("device") or {}).get("address") if state.data.get("device") else None
    session = await open_session(backend, profile, address=args.address,
                                 state_address=state_address, run_handshake=run_handshake)
    try:
        state.set_device(session.device_name, session.address)
        state.log(f"{args.command}: connected {session.address}")
        state.save()
        return await fn(session, **kwargs)
    finally:
        await close_session(session)


async def _do_connect(session: Session) -> dict[str, Any]:
    by_uuid = {s.uuid: s for s in session.services}
    return {
        "connected": True,
        "address": session.address,
        "device_name": session.device_name,
        "profile": session.profile.name,
        "mtu": session.mtu,
        "services_found": {
            svc: [c.uuid for c in by_uuid[svc].characteristics]
            for svc in session.profile.service_uuids
        },
        "handshake": session.handshake,
    }


async def _do_gatt(session: Session) -> dict[str, Any]:
    return gatt_tree(session.services)


async def _do_write(session: Session, frame: bytes, listen_s: float) -> dict[str, Any]:
    session.recorder.clear()
    await session.backend.write(session.profile.write_char_uuid, frame,
                                session.profile.write_with_response)
    out: dict[str, Any] = {"written": [" ".join(f"{b:02X}" for b in frame)]}
    if listen_s > 0:
        await asyncio.sleep(listen_s)
        out["uplinks"] = session.recorder.snapshot()
    return out


async def _do_listen(session: Session, listen_s: float) -> dict[str, Any]:
    session.recorder.clear()
    await asyncio.sleep(listen_s)
    return {"uplinks": session.recorder.snapshot()}


def _do_confirm(args) -> dict[str, Any]:
    state = _state(args)
    pending = state.pending_confirm()
    if args.case_id and pending and pending.get("case_id") != args.case_id:
        raise BleCliError.usage(
            f"pending confirmation is for case {pending['case_id']!r}, not {args.case_id!r}")
    if pending is None:
        raise BleCliError.usage("no pending confirmation to answer")
    case_id = args.case_id or pending["case_id"]
    state.set_confirm(case_id, args.answer, args.note)
    state.log(f"confirm {case_id}: {args.answer}")
    state.save()
    return {"case_id": case_id, "confirmed": args.answer}


async def _do_cases(args, envelope: Envelope) -> dict[str, Any]:
    from .cases.parser import ParseConfig, parse_doc, validate
    from .cases.report import write_report
    from .cases.runner import RunOptions, run_case

    if not args.cases_command:
        raise BleCliError.usage("cases needs a subcommand: list | run")

    config = ParseConfig.from_toml(Path(args.config)) if args.config else ParseConfig(doc=Path(args.doc))
    cases = parse_doc(args.doc, config)
    if args.group:
        cases = [c for c in cases if c.group == args.group]
    if not cases:
        raise BleCliError("cases_parse_failed", "no cases matched the filter")

    if args.cases_command == "list":
        data: dict[str, Any] = {"cases": [c.to_dict() for c in cases]}
        if args.validate:
            data.update(validate(cases))
        return data

    if args.cases_command == "run":
        profile = _profile(args)
        state = _state(args)
        if args.ids:
            cases = [c for c in cases if c.id in args.ids]
            if not cases:
                raise BleCliError.usage(
                    f"none of {args.ids!r} found in document")

        opts = RunOptions(
            rules_dir=Path(args.rules),
            state_path=Path(args.state_file),
            confirm_timeout_s=args.confirm_timeout,
            ruleless_window_ms=args.window,
        )
        envelope.warnings.append(f"running {len(cases)} case(s)")

        results = []
        for i, case in enumerate(cases, start=1):
            print(f"[{i}/{len(cases)}] {case.id}", file=sys.stderr, flush=True)
            r = await run_case(case, profile, lambda: BleakBackend(),
                               Path(args.state_file), opts, args.address)
            results.append(r)
            state.log(f"case {case.id} -> {r.result}")
            state.save()

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = args.out or str(Path(args.state_file).parent / f"report_{stamp}")
        md_path, summary = write_report(out_path, results, meta={
            "doc": str(args.doc), "profile": profile.name,
        })
        state.data["last_report"] = str(md_path)
        state.save()
        return {"cases": [r.to_dict() for r in results],
                "summary": summary, "report": str(md_path)}

    raise BleCliError(INTERNAL_ERROR, f"unknown cases subcommand {args.cases_command}")
