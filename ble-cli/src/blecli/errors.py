"""Error codes (D8: exit 0/1/2 + JSON `error.code`) and the exception that carries them.

Exit code policy:
    0 = ok
    1 = execution failure (subtype in ``BleCliError.code``)
    2 = usage error (bad CLI invocation / bad user-supplied data format)
"""

from __future__ import annotations

# Execution failures (exit 1)
DEVICE_NOT_FOUND = "device_not_found"
CONNECT_FAILED = "connect_failed"
SERVICE_NOT_FOUND = "service_not_found"
CHAR_NOT_FOUND = "char_not_found"
WRITE_FAILED = "write_failed"
NOTIFY_FAILED = "notify_failed"
HANDSHAKE_TIMEOUT = "handshake_timeout"
TIMEOUT = "timeout"
DISCONNECTED = "disconnected"
PROFILE_NOT_FOUND = "profile_not_found"
PROFILE_INVALID = "profile_invalid"
PROFILE_HOOK_ERROR = "profile_hook_error"
CASES_DOC_NOT_FOUND = "cases_doc_not_found"
CASES_PARSE_FAILED = "cases_parse_failed"
STATE_FILE_ERROR = "state_file_error"
BLE_OS_ERROR = "ble_os_error"
INTERNAL_ERROR = "internal_error"

# Usage errors (exit 2)
USAGE_ERROR = "usage_error"

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2


class BleCliError(Exception):
    """An error with a stable machine-readable ``code``."""

    def __init__(self, code: str, message: str, *, exit_code: int = EXIT_FAIL):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code

    @classmethod
    def usage(cls, message: str) -> "BleCliError":
        return cls(USAGE_ERROR, message, exit_code=EXIT_USAGE)


def exit_code_for(code: str) -> int:
    return EXIT_USAGE if code == USAGE_ERROR else EXIT_FAIL


def die(code: str, message: str) -> BleCliError:
    """Shorthand: build an execution-failure error from a code + message."""
    return BleCliError(code, message)
