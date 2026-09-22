"""--json envelope (design.md section 4): every command emits one JSON object.

Envelope invariants:
    - ``schema`` / ``command`` / ``status`` / ``error.code`` always present.
    - ``status`` is exactly ``"ok"`` or ``"error"``.
    - On error, ``error = {"code": <enum>, "message": <human text>}``.
Usage errors (exit 2) are JSON-ified too, never a bare argparse traceback.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from .errors import BleCliError, EXIT_FAIL, EXIT_OK, EXIT_USAGE, INTERNAL_ERROR

SCHEMA = "ble-cli/1"


class Envelope:
    def __init__(self, command: str, state_file: str | None = None):
        self.command = command
        self.state_file = state_file
        self._start = time.monotonic()
        self.data: dict[str, Any] = {}
        self.warnings: list[str] = []
        self.error: BleCliError | None = None

    @property
    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._start) * 1000)

    def to_dict(self) -> dict[str, Any]:
        status = "ok" if self.error is None else "error"
        env: dict[str, Any] = {
            "schema": SCHEMA,
            "command": self.command,
            "status": status,
            "data": self.data,
            "error": None,
            "warnings": self.warnings,
            "elapsed_ms": self.elapsed_ms,
        }
        if self.state_file:
            env["state_file"] = self.state_file
        if self.error is not None:
            env["error"] = {"code": self.error.code, "message": self.error.message}
        return env

    def emit(self) -> int:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass  # non-reconfigurable stdout (redirected) - best effort
        json.dump(self.to_dict(), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return EXIT_OK if self.error is None else self.error.exit_code


import argparse


class ArgParser(argparse.ArgumentParser):
    """ArgumentParser that raises JSON-able usage errors instead of printing
    usage text and calling sys.exit(2).  Also used as the parser_class for
    subparsers, so every subcommand behaves the same way."""

    def error(self, message: str):
        raise BleCliError.usage(message)


def internal_error(exc: BaseException) -> BleCliError:
    """Wrap an unexpected exception into the internal_error envelope."""
    return BleCliError(INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")
