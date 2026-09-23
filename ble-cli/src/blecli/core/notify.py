"""Uplink recording: every notification lands in a timestamped log, decoded
best-effort by the profile frame table; unknown frames stay as raw hex."""

from __future__ import annotations

import asyncio
import time
from typing import Callable

from ..util import fmt_hex, timestamp_ms
from .frames import FrameTable


class UplinkRecorder:
    def __init__(self, frame_table: FrameTable | None = None):
        self.frame_table = frame_table
        self.uplinks: list[dict] = []

    def on_notify(self, payload: bytes) -> None:
        rec = self.frame_table.record(payload) if self.frame_table else {
            "type": "unknown", "hex": fmt_hex(payload)}
        rec["t_ms"] = timestamp_ms()
        self.uplinks.append(rec)

    def snapshot(self) -> list[dict]:
        return list(self.uplinks)

    def clear(self) -> None:
        self.uplinks.clear()

    async def wait_until(self, predicate: Callable[[list[dict]], bool], timeout_s: float, poll_s: float = 0.05) -> bool:
        """Poll until predicate(uplinks) is true or the timeout expires."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if predicate(self.uplinks):
                return True
            await asyncio.sleep(poll_s)
        return predicate(self.uplinks)
