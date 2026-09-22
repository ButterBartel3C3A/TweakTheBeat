import io
import json
import sys
from pathlib import Path

import pytest

from blecli import cli
from blecli.errors import BleCliError
from blecli.output import Envelope


def test_envelope_ok_shape():
    env = Envelope("scan", "s.json")
    env.data = {"devices": []}
    d = env.to_dict()
    assert d["schema"] == "ble-cli/1"
    assert d["command"] == "scan"
    assert d["status"] == "ok"
    assert d["error"] is None
    assert d["state_file"] == "s.json"
    assert isinstance(d["elapsed_ms"], int)


def test_envelope_error_shape():
    env = Envelope("init", None)
    env.error = BleCliError("handshake_timeout", "nope")
    d = env.to_dict()
    assert d["status"] == "error"
    assert d["error"]["code"] == "handshake_timeout"


def test_usage_error_exit_code():
    assert BleCliError.usage("x").exit_code == 2
    assert BleCliError("connect_failed", "x").exit_code == 1


def run_main(args):
    out = io.StringIO()
    old = sys.stdout
    sys.stdout = out
    try:
        code = cli.main(args)
    finally:
        sys.stdout = old
    return code, json.loads(out.getvalue())


def test_no_command_json_usage():
    code, d = run_main(["--json"])
    assert code == 2
    assert d["error"]["code"] == "usage_error"


def test_unknown_command_json_usage():
    code, d = run_main(["--json", "frobnicate"])
    assert code == 2
    assert d["error"]["code"] == "usage_error"


def test_missing_profile_for_device_command():
    code, d = run_main(["--json", "gatt"])
    assert code == 2
    assert d["error"]["code"] == "usage_error"


def test_bad_hex_write_usage():
    demo = Path(__file__).resolve().parent.parent / "examples/demo_profile/profile.toml"
    code, d = run_main(["--json", "--profile", str(demo), "write", "ZZ QQ"])
    assert code == 2
    assert d["error"]["code"] == "usage_error"


def test_confirm_without_pending(tmp_path):
    code, d = run_main(["--json", "--state-file", str(tmp_path / "s.json"), "confirm"])
    assert code == 2
    assert d["error"]["code"] == "usage_error"


def test_confirm_happy_path(tmp_path):
    from blecli.core.state import State
    p = tmp_path / "s.json"
    s = State(p)
    s.set_pending_confirm("A1.2", "按下按键", [])
    s.save()
    code, d = run_main(["--json", "--state-file", str(p), "confirm", "--case-id", "A1.2"])
    assert code == 0
    assert d["data"]["confirmed"] == "yes"


def test_cases_list_without_doc():
    code, d = run_main(["--json", "cases", "list"])
    assert code == 2
    assert d["error"]["code"] == "usage_error"


def test_disconnect_clears_device(tmp_path):
    from blecli.core.state import State
    p = tmp_path / "s.json"
    s = State(p)
    s.set_device("D", "AA")
    s.save()
    code, d = run_main(["--json", "--state-file", str(p), "disconnect"])
    assert code == 0
    assert d["data"]["disconnected"] is True
    assert State(p).data["device"] is None
