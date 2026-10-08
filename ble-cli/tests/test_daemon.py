"""Daemon channel protocol tests (D11): the file channel works end to end
with a fake handler; routing returns None when no daemon is alive or the
channel is unresponsive, so callers fall back to direct connections."""

import asyncio
import json
from pathlib import Path

from blecli.daemon import _dir, CMD_FILE, read_record, route_or_none, run_channel


def _write_record(state_path: Path, pid: int = 9999) -> None:
    rec = {"pid": pid, "profile": "demo", "started_at": 0.0, "idle_timeout_s": 600}
    (state_path.parent / "daemon.json").write_text(
        json.dumps(rec), encoding="utf-8")


async def _handler(cmd: dict) -> dict:
    if cmd["command"] == "ping":
        return {"data": {"pong": True}}
    if cmd["command"] == "stop":
        return {"data": {"stopped": True}, "__stop__": True}
    return {"data": {"echo": cmd.get("args")}}


def test_channel_roundtrip(tmp_path):
    state_path = tmp_path / "state.json"
    _write_record(state_path)

    async def scenario():
        task = asyncio.create_task(run_channel(state_path, 10.0, _handler))
        try:
            out = await route_or_none(state_path, "ping", {})
            assert out == {"data": {"pong": True}}
            out2 = await route_or_none(state_path, "write", {"hex": "DE AD"})
            assert out2 == {"data": {"echo": {"hex": "DE AD"}}}
            # command file is consumed after each request
            assert not (state_path.parent / CMD_FILE).exists()
        finally:
            task.cancel()

    asyncio.run(scenario())


def test_route_none_without_record(tmp_path):
    state_path = tmp_path / "state.json"
    assert read_record(state_path) is None

    async def scenario():
        assert await route_or_none(state_path, "ping", {}, timeout_s=0.3) is None

    asyncio.run(scenario())


def test_route_none_when_channel_unresponsive(tmp_path):
    state_path = tmp_path / "state.json"
    _write_record(state_path)  # record exists but no serve loop running

    async def scenario():
        assert await route_or_none(state_path, "ping", {}, timeout_s=0.3) is None
        # stale command file must be cleaned up for the caller's fallback
        assert not (state_path.parent / CMD_FILE).exists()

    asyncio.run(scenario())


def test_stop_command_exits_channel(tmp_path):
    state_path = tmp_path / "state.json"
    _write_record(state_path)

    async def scenario():
        task = asyncio.create_task(run_channel(state_path, 10.0, _handler))
        out = await route_or_none(state_path, "stop", {})
        assert out["data"] == {"stopped": True}
        await asyncio.wait_for(task, timeout=2.0)  # channel exits on __stop__

    asyncio.run(scenario())


def test_idle_timeout_exits_channel(tmp_path):
    state_path = tmp_path / "state.json"

    async def scenario():
        await run_channel(state_path, 0.2, _handler)  # returns by itself

    asyncio.run(scenario())
