import json

from blecli.core.state import State


def test_fresh_state_roundtrip(tmp_path):
    p = tmp_path / "state.json"
    s = State(p)
    s.set_device("Dev", "AA:BB:CC:DD:EE:FF")
    s.log("hello")
    s.save()
    s2 = State(p)
    assert s2.data["device"] == {"name": "Dev", "address": "AA:BB:CC:DD:EE:FF"}
    assert s2.data["last_action"] == "hello"


def test_confirm_flow(tmp_path):
    p = tmp_path / "state.json"
    runner = State(p)
    runner.set_pending_confirm("A1.2", "按下按键", [{"id": "led", "prompt": "灯亮?"}])
    runner.save()

    tester = State(p)
    assert tester.pending_confirm()["case_id"] == "A1.2"
    tester.set_confirm("A1.2", "yes", "ok")
    tester.save()

    fresh = State(p)
    assert fresh.pending_confirm() is None
    assert fresh.data["last_confirm"]["answer"] == "yes"


def test_corrupt_state(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{ not json", encoding="utf-8")
    try:
        State(p)
        raise AssertionError("expected state_file_error")
    except Exception as exc:
        assert exc.code == "state_file_error"


def test_state_schema_field_present(tmp_path):
    s = State(tmp_path / "state.json")
    s.save()
    raw = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert raw["schema"] == "ble-cli-state/1"
