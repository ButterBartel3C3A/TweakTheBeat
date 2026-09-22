from pathlib import Path

import pytest

from blecli.errors import BleCliError
from blecli.profiles.loader import load_profile

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo_profile" / "profile.toml"


def test_demo_profile_loads():
    p = load_profile(DEMO)
    assert p.name == "demo"
    assert p.device_name_filters == ["DemoDevice*"]
    assert len(p.service_uuids) == 1
    assert p.write_with_response is False
    assert p.notify_char_uuid
    assert p.handshake is not None
    assert p.handshake.deadline_ms == 5000
    assert len(p.handshake.sequence) == 1
    assert [e.name for e in p.handshake.expect] == ["ack", "sn"]
    assert [ft.name for ft in p.frame_table.types] == ["ack", "sn", "float_report"]
    assert "decode_float" in p.hooks


def test_demo_hook_decodes_float():
    p = load_profile(DEMO)
    rec = p.frame_table.decode(bytes.fromhex("F1 81 00 00 3F 80 00 00 12 34"))
    assert rec["type"] == "float_report"
    assert rec["fields"]["value"] == 1.0


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "profile.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_missing_file():
    with pytest.raises(BleCliError) as ei:
        load_profile("nonexistent_profile.toml")
    assert ei.value.code == "profile_not_found"


def test_unknown_key_rejected(tmp_path):
    p = _write(tmp_path, """
[meta]
name = "x"

[device]
name_filter = "X*"

[gatt]
[[gatt.services]]
service = "0000180D-0000-1000-8000-00805F9B34FB"

[gatt.chars.write]
uuid = "00002A00-0000-1000-8000-00805F9B34FB"

[bonk]
extra = "nope"
""")
    with pytest.raises(BleCliError) as ei:
        load_profile(p)
    assert ei.value.code == "profile_invalid"
    assert "bonk" in ei.value.message


def test_missing_write_char(tmp_path):
    p = _write(tmp_path, """
[meta]
name = "x"

[device]
name_filter = "X*"
""")
    with pytest.raises(BleCliError) as ei:
        load_profile(p)
    assert ei.value.code == "profile_invalid"


def test_missing_hook_module(tmp_path):
    p = _write(tmp_path, """
[meta]
name = "x"

[device]
name_filter = "X*"

[[gatt.services]]
service = "0000180D-0000-1000-8000-00805F9B34FB"

[gatt.chars.write]
uuid = "00002A00-0000-1000-8000-00805F9B34FB"

[frames]
[[frames.types]]
name = "f"
pattern = "01"
hook = "ghost"

[hooks]
module = "adapter"
""")
    with pytest.raises(BleCliError) as ei:
        load_profile(p)
    assert ei.value.code == "profile_hook_error"


def test_adapter_hook_module(tmp_path):
    (tmp_path / "adapter.py").write_text(
        "def my_hook(payload):\n    return {'n': len(payload)}\n", encoding="utf-8")
    p = _write(tmp_path, """
[meta]
name = "x"

[device]
name_filter = "X*"

[[gatt.services]]
service = "0000180D-0000-1000-8000-00805F9B34FB"

[gatt.chars.write]
uuid = "00002A00-0000-1000-8000-00805F9B34FB"

[frames]
[[frames.types]]
name = "f"
pattern = "01 XX"
hook = "my_hook"

[hooks]
module = "adapter"
""")
    prof = load_profile(p)
    rec = prof.frame_table.decode(bytes.fromhex("01 02"))
    assert rec["fields"]["n"] == 2
