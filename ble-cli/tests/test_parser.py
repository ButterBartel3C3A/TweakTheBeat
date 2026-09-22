import pytest

from blecli.cases.parser import parse_doc, parse_inject, validate
from blecli.errors import BleCliError

DOC = """# 测试文档

## A. 连接
| 用例 | 注入帧(->写) | 物理刺激 | 预期上行/响应(<-收) | 备注 |
|---|---|---|---|---|
| A1.1 | DE AD -> BE EF | - | BE EF XX | 序列 |
| A1.2 | - | 按下按键后松开 | CA FE +4B | 物理 |
| A1.3 | 0x5F XX | - | - | 通配 |
| A1.4 | AA / BB | - | - | 多选 |

## B. 工具
| 用例 | 注入帧 | 物理刺激 | 预期 | 备注 |
|---|---|---|---|---|
| B1.1 | DE AD x3 | - | - | 重复 |
"""


def test_parse_basic(tmp_path):
    doc = tmp_path / "cases.md"
    doc.write_text(DOC, encoding="utf-8")
    cases = parse_doc(doc)
    assert [c.id for c in cases] == ["A1.1", "A1.2", "A1.3", "A1.4", "B1.1"]
    assert all(c.group == "A" for c in cases[:4])
    assert cases[4].group == "B"
    assert all(c.valid for c in cases)


def test_parse_notations(tmp_path):
    doc = tmp_path / "cases.md"
    doc.write_text(DOC, encoding="utf-8")
    cases = {c.id: c for c in parse_doc(doc)}

    a11 = cases["A1.1"].inject
    assert len(a11.alternatives) == 1
    assert [s.raw for s in a11.alternatives[0]] == ["DE AD", "BE EF"]

    assert cases["A1.2"].inject is None
    assert "按键" in cases["A1.2"].physical

    a13 = cases["A1.3"].inject
    assert a13.alternatives[0][0].has_wildcard

    a14 = cases["A1.4"].inject
    assert len(a14.alternatives) == 2

    b11 = cases["B1.1"].inject
    assert b11.alternatives[0][0].repeat == 3


def test_validate_stats(tmp_path):
    doc = tmp_path / "cases.md"
    doc.write_text(DOC, encoding="utf-8")
    cases = parse_doc(doc)
    report = validate(cases)
    assert report["stats"]["total"] == 5
    assert report["stats"]["valid"] == 5
    assert report["stats"]["with_wildcard_inject"] == 1
    assert report["stats"]["physical_only"] == 1
    assert report["bad_rows"] == []


def test_bad_row_reported(tmp_path):
    doc = tmp_path / "cases.md"
    doc.write_text("""## A. 组
| 用例 | 注入帧 | 物理刺激 | 预期 | 备注 |
|---|---|---|---|---|
| A9.9 | ZZ QQ | - | - | 坏hex |
| | AA | - | - | 缺id |
""", encoding="utf-8")
    cases = parse_doc(doc)
    report = validate(cases)
    assert report["stats"]["total"] == 2
    assert report["stats"]["valid"] == 0
    assert len(report["bad_rows"]) == 2


def test_missing_doc():
    with pytest.raises(BleCliError) as ei:
        parse_doc("no_such_cases.md")
    assert ei.value.code == "cases_doc_not_found"


def test_no_tables():
    import tempfile
    from pathlib import Path
    d = Path(tempfile.mkdtemp()) / "empty.md"
    d.write_text("只有文字没有表格", encoding="utf-8")
    with pytest.raises(BleCliError) as ei:
        parse_doc(d)
    assert ei.value.code == "cases_parse_failed"


def test_parse_inject_errors():
    with pytest.raises(ValueError):
        parse_inject("ZZ QQ")
    # a bare none marker is well-formed: no frames, no error
    assert parse_inject("—").alternatives == []
    assert parse_inject("`—`").alternatives == []

# real-doc backtick cell shapes
def test_inject_backticked_frames():
    from blecli.cases.parser import parse_inject
    spec = parse_inject("`50 01 05 01 00 0A 00 05 1E 00 01 00 64 00 00 00 32`")
    assert len(spec.alternatives) == 1 and len(spec.alternatives[0]) == 1
    assert not spec.alternatives[0][0].has_wildcard

    spec = parse_inject("`60 00` 后再写 `50 01 05 01 00 0A 00 05 1E 00 01 00 64 00 00 00 32`")
    assert [s.raw for s in spec.alternatives[0]] == [
        "60 00", "50 01 05 01 00 0A 00 05 1E 00 01 00 64 00 00 00 32"]

    spec = parse_inject("`50 01 05 01 00 0A 00 05 1E 00 01 00 64 00 00 00 32` 触发后断开->重连")
    assert len(spec.alternatives[0]) == 1  # prose after backticks ignored
