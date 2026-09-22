"""Programmatic parser for case-table markdown docs (D4: validate + degrade).

The document is the data source: sections (``## A. ...`` style headers)
contain pipe tables with columns like 用例 | 注入帧(->写) | 物理刺激 |
预期上行/响应(<-收) | 备注.  Column mapping is auto-detected from header
keywords; group headers use a configurable regex (defaults match
``# A. title`` style).

Cell notations handled:
    ``XX`` wildcard bytes, ``-``/empty = none,
    ``A -> B`` sequences, ``A / B`` alternatives, ``x N`` repeats.
Rows that fail parsing are kept with their errors (never silently dropped);
``cases list --validate`` reports them with line numbers.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import BleCliError, CASES_DOC_NOT_FOUND, CASES_PARSE_FAILED
from ..util import parse_hex

NONE_MARKERS = {"-", "--", "—", "–", "无", "无注入", "none", "n/a", ""}

# header keyword -> logical column (checked in order)
COLUMN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "id": ("用例", "编号", "case", "id"),
    "inject": ("注入", "写", "inject", "tx"),
    "physical": ("物理", "刺激", "操作", "physical", "manual"),
    "expect": ("预期", "响应", "上行", "expect", "rx"),
    "note": ("备注", "说明", "note", "remark"),
}

DEFAULT_GROUP_RE = r"^(#{1,6})\s*([A-Z])\s*[.、．]"  # group 1 = letter
ID_RE = re.compile(r"^[A-Z]\d+\.\d+$")


@dataclass
class FrameStep:
    raw: str
    has_wildcard: bool
    repeat: int = 1


@dataclass
class InjectSpec:
    alternatives: list[list[FrameStep]]
    raw: str


@dataclass
class Case:
    id: str
    group: str
    group_title: str
    inject: InjectSpec | None
    physical: str
    expect_raw: str
    note: str
    source_line: int
    parse_errors: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.parse_errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "group": self.group,
            "group_title": self.group_title,
            "inject": self.inject.raw if self.inject else None,
            "physical": self.physical or None,
            "expect_raw": self.expect_raw or None,
            "note": self.note or None,
            "source_line": self.source_line,
            "parse_errors": self.parse_errors,
        }


@dataclass
class ParseConfig:
    group_re: str = DEFAULT_GROUP_RE
    doc: Path | None = None

    @classmethod
    def from_toml(cls, path: Path) -> "ParseConfig":
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise BleCliError(CASES_PARSE_FAILED, f"cases config {path}: {exc}") from exc
        parse = raw.get("parse") or {}
        cfg = cls(group_re=str(parse.get("group_re", DEFAULT_GROUP_RE)))
        if raw.get("doc") and raw["doc"].get("path"):
            doc = Path(str(raw["doc"]["path"]))
            if not doc.is_absolute():
                doc = (path.parent / doc).resolve()
            cfg.doc = doc
        return cfg


def parse_doc(doc_path: str | Path, config: ParseConfig | None = None) -> list[Case]:
    """Parse a case-table markdown document into Case objects."""
    doc = Path(doc_path)
    if not doc.exists():
        raise BleCliError(CASES_DOC_NOT_FOUND, f"cases document not found: {doc}")
    config = config or ParseConfig(doc=doc)
    group_re = re.compile(config.group_re)

    cases: list[Case] = []
    group = "?"
    group_title = ""
    in_table = False
    columns: dict[str, int] = {}

    for lineno, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()

        m = group_re.match(stripped)
        if m and not stripped.startswith("|"):
            group, group_title = m.group(2), stripped.lstrip("# ").strip()
            in_table = False
            continue

        if not stripped.startswith("|"):
            in_table = False
            continue

        cells = [c.strip() for c in stripped.strip("|").split("|")]

        # separator row (|---| or |-|) skips; a header row starts a new table
        if all(re.fullmatch(r":?-+:?", c) for c in cells if c):
            continue

        if not in_table:
            columns = _detect_columns(cells, lineno)
            if columns is None:
                continue  # not a case table
            in_table = True
            continue

        case = _parse_row(cells, columns, group, group_title, lineno)
        cases.append(case)

    if not cases:
        raise BleCliError(CASES_PARSE_FAILED, f"no case tables found in {doc}")
    return cases


def _detect_columns(cells: list[str], lineno: int) -> dict[str, int] | None:
    """Map table header cells to logical columns; None if this table is not a
    case table (no id column).  The id cell must be exactly a case-id header
    ("用例"/"case"...) so that docs with columns like "服务 UUID" or cells
    mentioning "ID" are not mistaken for case tables."""
    mapping: dict[str, int] = {}
    for idx, cell in enumerate(cells):
        low = cell.lower().strip()
        if "id" not in mapping and low in ("用例", "编号", "case", "id", "case id", "用例编号"):
            mapping["id"] = idx
            continue
        for logical, keywords in COLUMN_KEYWORDS.items():
            if logical == "id" or logical in mapping:
                continue
            if any(kw in low for kw in keywords):
                mapping[logical] = idx
                break
    if "id" not in mapping:
        return None
    return mapping


def _parse_row(cells: list[str], columns: dict[str, int], group: str,
               group_title: str, lineno: int) -> Case:
    def get(col: str) -> str:
        idx = columns.get(col)
        if idx is None or idx >= len(cells):
            return ""
        return cells[idx]

    errors: list[str] = []
    case_id = get("id")
    if not case_id:
        errors.append(f"line {lineno}: missing case id")
    elif not ID_RE.match(case_id):
        errors.append(f"line {lineno}: unusual case id {case_id!r}")

    inject_text = get("inject")
    inject = None
    if inject_text not in NONE_MARKERS:
        try:
            inject = parse_inject(inject_text)
            if inject is not None and not inject.alternatives:
                inject = None  # cell was a well-formed "none" (e.g. `—`)
        except ValueError as exc:
            errors.append(f"line {lineno}: inject {inject_text!r}: {exc}")
            inject = None

    physical = "" if get("physical") in NONE_MARKERS else get("physical")
    expect = "" if get("expect") in NONE_MARKERS else get("expect")
    note = "" if get("note") in NONE_MARKERS else get("note")

    if inject is None and not physical:
        errors.append(f"line {lineno}: neither inject frames nor physical stimulus")

    return Case(id=case_id, group=group, group_title=group_title, inject=inject,
                physical=physical, expect_raw=expect, note=note,
                source_line=lineno, parse_errors=errors)


def parse_inject(text: str) -> InjectSpec:
    """Parse an injection cell: ``A / B -> C x3`` -> alternatives of step lists.

    Real docs wrap frames in backticks, often with prose around them
    (``"`60 00` 后再写 `50 ..`"``): every backticked token becomes a step
    and the prose is ignored; cells without backticks use the ``->`` split."""
    alternatives: list[list[FrameStep]] = []
    for alt in text.split("/"):
        alt = alt.strip()
        if not alt or alt in NONE_MARKERS:
            continue
        steps: list[FrameStep] = []
        backticked = [tok.strip() for tok in re.findall(r"`([^`]+)`", alt)]
        if backticked:
            for tok in backticked:
                if tok in NONE_MARKERS:
                    continue  # cell like `—`: no injectable frames
                for run in _hex_runs(tok):
                    steps.append(_parse_step(run))
        else:
            for tok in re.split(r"->|→", alt):
                tok = tok.strip()
                if not tok:
                    continue
                steps.append(_parse_step(tok))
        if steps:
            alternatives.append(steps)
    if not alternatives:
        stripped = text.replace("`", "").strip()
        if stripped in NONE_MARKERS or all(
                tok in NONE_MARKERS for tok in re.findall(r"`([^`]+)`", text)):
            return InjectSpec(alternatives=[], raw=text)  # none, but well-formed
        raise ValueError("no injectable frames")
    return InjectSpec(alternatives=alternatives, raw=text)


HEX_RUN_RE = re.compile(r"[0-9A-Fa-f]{2}(?:\s+[0-9A-Fa-f]{2})*")


def _hex_runs(tok: str) -> list[str]:
    """Frames out of a backticked token.  Real docs mix prose and frames
    inside one backtick pair (``"`60 00` 后再写 `50 ..`"``, annotations).
    Extract contiguous hex runs; prose between them is dropped ONLY when it
    cannot be confused with hex (no A-F/x/×/*/wildcard chars).  Anything
    ambiguous falls through to the whole token, which then fails loudly and
    surfaces as a reported bad row instead of a silent mis-parse."""
    runs = HEX_RUN_RE.findall(tok)
    if not runs:
        return [tok]
    leftover = HEX_RUN_RE.sub(" ", tok)
    if re.search(r"[A-Fa-fx×*?X]|XX|？？", leftover):
        return [tok]  # hex-ish prose: let _parse_step raise
    return runs


def _parse_step(tok: str) -> FrameStep:
    tok = tok.strip().strip("`").strip()
    repeat = 1
    m = re.fullmatch(r"(.*?)\s*[x×*]\s*(\d+)\s*", tok)
    if m:
        tok, repeat = m.group(1).strip(), int(m.group(2))
    if not tok:
        raise ValueError("empty step")
    has_wildcard = "XX" in tok.upper() or "??" in tok
    if not has_wildcard:
        try:
            parse_hex(tok)
        except ValueError as exc:
            raise ValueError(str(exc)) from None
    return FrameStep(raw=tok, has_wildcard=has_wildcard, repeat=repeat)


def validate(cases: list[Case]) -> dict[str, Any]:
    stats = {
        "total": len(cases),
        "valid": sum(1 for c in cases if c.valid),
        "with_inject": sum(1 for c in cases if c.inject is not None),
        "with_wildcard_inject": sum(1 for c in cases if c.inject is not None and any(
            s.has_wildcard for alt in c.inject.alternatives for s in alt)),
        "physical_only": sum(1 for c in cases if c.inject is None and c.physical),
    }
    bad = [c.to_dict() for c in cases if not c.valid]
    return {"stats": stats, "bad_rows": bad}
