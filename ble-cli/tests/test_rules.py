from pathlib import Path

import pytest

from blecli.cases.rules import (evaluate, find_rule, load_rule, result_of)
from blecli.errors import BleCliError

U = lambda h, **f: {"hex": h, "type": "x", "fields": f}


def _write(tmp_path: Path, text: str, case_id: str = "X1.1") -> Path:
    p = tmp_path / f"{case_id}.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_load_rule(tmp_path):
    p = _write(tmp_path, """
case_id = "X1.1"
mode = "inject"
[[inject]]
write = "DE AD"
[assert]
timeout_ms = 5000
[[assert.expect]]
name = "ack"
pattern = "BE EF XX"
[[assert.expect_not]]
pattern = "AC 01 01"
""")
    r = load_rule(p)
    assert r.case_id == "X1.1"
    assert r.mode == "inject"
    assert r.inject == [b"\xde\xad"]
    assert r.timeout_ms == 5000
    assert r.expect[0].name == "ack"
    assert r.has_assertions


def test_load_rule_unknown_key(tmp_path):
    p = _write(tmp_path, """
case_id = "X1.1"
[assert]
bogus = 1
""")
    with pytest.raises(BleCliError) as ei:
        load_rule(p)
    assert ei.value.code == "cases_parse_failed"
    assert "bogus" in ei.value.message


def test_evaluate_order_and_wildcard(tmp_path):
    p = _write(tmp_path, """
[assert]
[[assert.expect]]
name = "a"
pattern = "01 XX"
[[assert.expect]]
name = "b"
pattern = "02 XX"
""")
    r = load_rule(p)
    ok = [U("01 07"), U("02 09")]
    res = evaluate(r, ok)
    assert [x["status"] for x in res] == ["pass", "pass"]

    wrong_order = [U("02 09"), U("01 07")]
    res = evaluate(r, wrong_order)
    assert res[0]["status"] == "pass"      # 01 XX matches at position 1
    assert res[1]["status"] == "fail"      # 02 XX must come after 01 XX


def test_evaluate_expect_not_and_none(tmp_path):
    p = _write(tmp_path, """
[assert]
[[assert.expect_not]]
pattern = "AC 01 01"
""")
    r = load_rule(p)
    res = evaluate(r, [U("01 07")])
    assert res[0]["status"] == "pass"
    res = evaluate(r, [U("AC 01 01")])
    assert res[0]["status"] == "fail"

    p = _write(tmp_path, """
[assert]
expect_none = true
""", case_id="X2.2")
    r = load_rule(p)
    assert evaluate(r, [])[0]["status"] == "pass"
    assert evaluate(r, [U("01 07")])[0]["status"] == "manual"


def test_evaluate_increasing(tmp_path):
    p = _write(tmp_path, """
[assert]
[[assert.expect]]
name = "p"
pattern = "CA FE +4B"
count = { min = 3 }
check = "increasing"
field = "seq"
""")
    r = load_rule(p)
    up = [U("CA FE 00 00 00 01", seq=1),
          U("CA FE 00 00 00 02", seq=2),
          U("CA FE 00 00 00 03", seq=3)]
    res = evaluate(r, up)
    assert res[0]["status"] == "pass"

    down = [U("CA FE 00 00 00 03", seq=3),
            U("CA FE 00 00 00 02", seq=2),
            U("CA FE 00 00 00 01", seq=1)]
    res = evaluate(r, down)
    assert res[0]["status"] == "fail"


def test_verdict_matrix(tmp_path):
    p = _write(tmp_path, """
[assert]
[[assert.expect]]
name = "a"
pattern = "01 XX"
""")
    r = load_rule(p)
    passed = evaluate(r, [U("01 07")])
    failed = evaluate(r, [])
    assert result_of(passed, had_rule=True, human_confirmed=True, human_declined=False) == "PASS"
    assert result_of(failed, had_rule=True, human_confirmed=True, human_declined=False) == "FAIL"
    assert result_of(passed, had_rule=False, human_confirmed=True, human_declined=False) == "MANUAL"
    assert result_of(passed, had_rule=True, human_confirmed=False, human_declined=False) == "MANUAL"
    assert result_of(passed, had_rule=True, human_confirmed=True, human_declined=True) == "MANUAL"

    p = _write(tmp_path, """
case_id = "D1.1"
divergence = "固件现状与需求不符"
""", case_id="D1.1")
    r = load_rule(p)
    assert result_of([], had_rule=True, human_confirmed=True, human_declined=False,
                     divergence=r.divergence) == "MANUAL"


def test_find_rule(tmp_path):
    assert find_rule(tmp_path, "X1.1") is None
    _write(tmp_path, "case_id = 'X1.1'\n", case_id="X1.1")
    assert find_rule(tmp_path, "X1.1") is not None
