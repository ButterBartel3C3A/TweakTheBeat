from blecli.core.frames import FrameTable, make_frame_table, make_frame_type


def test_make_frame_type_pattern():
    ft = make_frame_type({"name": "ack", "pattern": "BE EF XX",
                          "fields": [{"name": "value", "byte": 2}]})
    rec = ft.decode(bytes.fromhex("BE EF 07"))
    assert rec["type"] == "ack"
    assert rec["fields"]["value"] == 7
    assert ft.decode(bytes.fromhex("BE EE 07")) is None


def test_make_frame_type_prefix_len():
    ft = make_frame_type({"name": "sn", "match": {"prefix": [0xCA, 0xFE], "len": 6},
                          "fields": [{"name": "seq", "byte": 2, "len": 4}]})
    rec = ft.decode(bytes.fromhex("CA FE 00 00 00 05"))
    assert rec["fields"]["seq"] == 5
    assert ft.decode(bytes.fromhex("CA FE 00")) is None


def test_frame_table_first_match_wins_and_unknown():
    table = make_frame_table([
        {"name": "a", "pattern": "01 XX"},
        {"name": "b", "pattern": "01 02"},
    ])
    assert table.decode(bytes.fromhex("01 03"))["type"] == "a"
    assert table.decode(bytes.fromhex("01 02"))["type"] == "a"  # first wins
    rec = table.decode(bytes.fromhex("FF"))
    assert rec["type"] == "unknown"
    assert rec["hex"] == "FF"


def test_hook_decode():
    table = make_frame_table([{"name": "f", "pattern": "F1 81 +8B", "hook": "d"}],
                             hooks={"d": lambda p: {"v": int.from_bytes(p[2:4], "big")}})
    rec = table.decode(bytes.fromhex("F1 81 00 2A 00 00 00 00 00 00"))
    assert rec["fields"]["v"] == 42


def test_missing_hook_does_not_lose_frame():
    table = make_frame_table([{"name": "f", "pattern": "F1 81 +8B", "hook": "nope"}])
    rec = table.decode(bytes.fromhex("F1 81 00 00 00 00 00 00 00 00"))
    assert rec["type"] == "f"
    assert "hook_error" in rec


def test_field_out_of_range_is_null():
    ft = make_frame_type({"name": "x", "pattern": "01",
                          "fields": [{"name": "far", "byte": 5}]})
    assert ft.decode(bytes.fromhex("01"))["fields"]["far"] is None
