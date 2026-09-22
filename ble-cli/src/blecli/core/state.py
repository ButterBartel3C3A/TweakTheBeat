"""State file (design.md section 4): persistent logical state on disk.

BLE connection objects cannot cross process boundaries, so every AI-mode
command re-connects and replays the profile handshake.  The state file
carries the *logical* state instead:

    - ``device``: last-known device (name/address) so commands re-connect
      without repeating a scan.
    - ``last_action`` / ``run_log``: audit trail.
    - ``pending_confirm``: set by the case runner when it blocks on a
      physical-stimulus case; the tester runs ``ble-cli confirm`` (a separate
      process) which writes the confirmation here, and the blocked runner
      polls this field.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from ..errors import BleCliError, STATE_FILE_ERROR
from ..util import timestamp_ms

STATE_SCHEMA = "ble-cli-state/1"


class State:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.data: dict[str, Any] = {
            "schema": STATE_SCHEMA,
            "device": None,
            "last_action": None,
            "pending_confirm": None,
            "last_confirm": None,
            "last_report": None,
            "run_log": [],
        }
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BleCliError(STATE_FILE_ERROR, f"state file {self.path} unreadable: {exc}") from exc
        if not isinstance(raw, dict):
            raise BleCliError(STATE_FILE_ERROR, f"state file {self.path} is not a JSON object")
        self.data.update(raw)
        self.data.setdefault("schema", STATE_SCHEMA)
        self.data.setdefault("device", None)
        self.data.setdefault("last_action", None)
        self.data.setdefault("pending_confirm", None)
        self.data.setdefault("last_confirm", None)
        self.data.setdefault("last_report", None)
        self.data.setdefault("run_log", [])

    def save(self) -> None:
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
                tmp.replace(self.path)
            except OSError as exc:
                raise BleCliError(STATE_FILE_ERROR, f"cannot write state file {self.path}: {exc}") from exc

    def log(self, entry: str) -> None:
        self.data["run_log"].append({"ts": timestamp_ms(), "event": entry})
        self.data["run_log"] = self.data["run_log"][-200:]
        self.data["last_action"] = entry

    def set_device(self, name: str | None, address: str | None) -> None:
        self.data["device"] = None if name is None and address is None else {"name": name, "address": address}

    def set_pending_confirm(self, case_id: str, instruction: str, checks: list[dict[str, str]]) -> None:
        self.data["pending_confirm"] = {
            "case_id": case_id,
            "instruction": instruction,
            "checks": checks,
            "set_at": timestamp_ms(),
        }

    def pending_confirm(self) -> dict[str, Any] | None:
        return self.data.get("pending_confirm")

    def clear_pending_confirm(self) -> None:
        self.data["pending_confirm"] = None

    def set_confirm(self, case_id: str, answer: str, note: str | None = None) -> None:
        self.data["last_confirm"] = {
            "case_id": case_id, "answer": answer, "note": note,
            "ts": timestamp_ms(),
        }
        self.data["pending_confirm"] = None
