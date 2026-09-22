"""Assertion rules (D7): one TOML file per case, progressive coverage.

No rule file -> the case still runs (inject + uplink log) but reports MANUAL.
Rules never use string matching against natural language; everything is
structured: inject frames, expectation window, ordered pattern expectations
(with XX wildcards / +4B suffixes), expect_not, expect_none, optional
semantic checks (count, increasing) and hook checks.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.frames import make_frame_type
from ..errors import BleCliError, CASES_PARSE_FAILED
from ..util import Pattern, parse_hex

RULE_KEYS = {"case_id", "mode", "inject", "human", "assert", "expect", "divergence"}


@dataclass
class ExpectSpec:
    name: str
    pattern: Pattern
    count_min: int = 1
    check: str | None = None      # "increasing" (needs field) | "adapter:xxx"
    field: str | None = None


@dataclass
class Rule:
    case_id: str
    mode: str                      # inject | physical | observe
    inject: list[bytes] = field(default_factory=list)
    instruction: str | None = None
    checks: list[dict[str, str]] = field(default_factory=list)
    timeout_ms: int = 5000
    expect: list[ExpectSpec] = field(default_factory=list)
    expect_not: list[Pattern] = field(default_factory=list)
    expect_none: bool = False
    divergence: str | None = None

    @property
    def has_assertions(self) -> bool:
        return bool(self.expect or self.expect_not or self.expect_none)


def load_rule(path: str | Path) -> Rule:
    p = Path(path)
    try:
        raw = tomllib.loads(p.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise BleCliError(CASES_PARSE_FAILED, f"rule file {p}: TOML syntax error: {exc}") from exc
    return _build(p, raw)


def find_rule(rules_dir: str | Path, case_id: str) -> Path | None:
    """Rules live as <rules_dir>/<case_id>.toml (dot in the id is fine)."""
    p = Path(rules_dir) / f"{case_id}.toml"
    return p if p.exists() else None


def _fail(p: Path, msg: str) -> None:
    raise BleCliError(CASES_PARSE_FAILED, f"rule file {p}: {msg}")


def _build(p: Path, raw: dict[str, Any]) -> Rule:
    for key in raw:
        if key not in RULE_KEYS:
            _fail(p, f"unknown key '{key}' (allowed: {sorted(RULE_KEYS)})")

    case_id = str(raw.get("case_id", p.stem))
    mode = str(raw.get("mode", "inject"))
    if mode not in ("inject", "physical", "observe"):
        _fail(p, f"mode {mode!r} not in {{inject, physical, observe}}")

    inject: list[bytes] = []
    for i, step in enumerate(raw.get("inject") or []):
        for k in step:
            if k != "write":
                _fail(p, f"inject[{i}] has unknown key '{k}'")
        if "write" not in step:
            _fail(p, f"inject[{i}] needs a write field")
        try:
            inject.append(parse_hex(str(step["write"])))
        except ValueError as exc:
            _fail(p, f"inject[{i}].write: {exc}")

    human = raw.get("human") or {}
    for k in human:
        if k not in ("instruction", "checks"):
            _fail(p, f"[human] has unknown key '{k}'")
    instruction = human.get("instruction")
    checks = []
    for c in human.get("checks") or []:
        if "id" not in c or "prompt" not in c:
            _fail(p, "[human].checks entries need id + prompt")
        checks.append({"id": str(c["id"]), "prompt": str(c["prompt"])})

    assert_raw = raw.get("assert") or {}
    for k in assert_raw:
        if k not in ("timeout_ms", "expect", "expect_not", "expect_none"):
            _fail(p, f"[assert] has unknown key '{k}'")
    expect: list[ExpectSpec] = []
    for i, spec in enumerate(assert_raw.get("expect") or []):
        try:
            ft = make_frame_type(spec, default_name=f"expect{i}")
        except ValueError as exc:
            _fail(p, f"assert.expect[{i}]: {exc}")
        count_min = int((spec.get("count") or {}).get("min", 1))
        check = spec.get("check")
        field = spec.get("field")
        if check == "increasing" and not field:
            _fail(p, f"assert.expect[{i}]: check=increasing needs a field name")
        expect.append(ExpectSpec(name=ft.name, pattern=ft.pattern,
                                 count_min=count_min, check=check, field=field))

    expect_not = []
    for i, spec in enumerate(assert_raw.get("expect_not") or []):
        try:
            ft = make_frame_type(spec, default_name=f"expect_not{i}")
        except ValueError as exc:
            _fail(p, f"assert.expect_not[{i}]: {exc}")
        expect_not.append(ft.pattern)

    return Rule(
        case_id=case_id,
        mode=mode,
        inject=inject,
        instruction=str(instruction) if instruction else None,
        checks=checks,
        timeout_ms=int(assert_raw.get("timeout_ms", 5000)),
        expect=expect,
        expect_not=expect_not,
        expect_none=bool(assert_raw.get("expect_none", False)),
        divergence=raw.get("divergence"),
    )


# ------------------------------------------------------------------ evaluate

def evaluate(rule: Rule, uplinks: list[dict], hooks: dict | None = None) -> list[dict[str, Any]]:
    """Evaluate an assertion rule against collected uplinks.

    Returns one result entry per expectation (+ extras), each with
    status pass|fail and evidence.  Order of ``expect`` = required temporal
    order: matches are searched with a moving cursor.
    """
    results: list[dict[str, Any]] = []
    cursor = 0

    for spec in rule.expect:
        matched: list[dict] = []
        i = cursor
        while i < len(uplinks) and len(matched) < spec.count_min:
            u = uplinks[i]
            if spec.pattern.matches(_payload(u)):
                matched.append(u)
            i += 1
        if matched:
            cursor = max(cursor, i)
        ok = len(matched) >= spec.count_min
        entry: dict[str, Any] = {
            "name": spec.name,
            "pattern": spec.pattern.text,
            "status": "pass" if ok else "fail",
            "matched": len(matched),
            "required": spec.count_min,
            "evidence": [u.get("hex") for u in matched],
        }
        if spec.check == "increasing" and ok and spec.field:
            values = [u.get("fields", {}).get(spec.field) for u in matched]
            if any(v is None for v in values):
                entry["status"] = "fail"
                entry["detail"] = f"field {spec.field!r} missing on some matches: {values}"
            else:
                entry["status"] = "pass" if all(
                    values[j] >= values[j - 1] for j in range(1, len(values))) else "fail"
                entry["detail"] = f"values={values}"
        elif spec.check and spec.check.startswith("adapter:") and ok:
            entry["status"] = "manual"
            entry["detail"] = "adapter check not wired for evaluation"
        if entry["status"] == "fail":
            entry["detail"] = entry.get("detail") or (
                f"expected >= {spec.count_min} matches, got {len(matched)}")
        results.append(entry)

    for pat in rule.expect_not:
        hits = [u.get("hex") for u in uplinks if pat.matches(_payload(u))]
        results.append({
            "name": f"expect_not({pat.text})",
            "pattern": pat.text,
            "status": "fail" if hits else "pass",
            "matched": len(hits),
            "evidence": hits,
        })

    if rule.expect_none:
        results.append({
            "name": "expect_none",
            "pattern": None,
            "status": "manual" if uplinks else "pass",
            "matched": len(uplinks),
            "evidence": [u.get("hex") for u in uplinks[:5]],
        })

    return results


def result_of(results: list[dict[str, Any]], *, had_rule: bool,
              human_confirmed: bool, human_declined: bool,
              divergence: str | None = None) -> str:
    """Overall verdict per the design matrix: PASS / FAIL / MANUAL."""
    if divergence:
        return "MANUAL"
    if human_declined:
        return "MANUAL"
    if not had_rule:
        return "MANUAL"
    if human_confirmed is False:  # human checks pending -> cannot claim PASS
        return "MANUAL"
    if any(r["status"] == "manual" for r in results):
        return "MANUAL"
    if any(r["status"] == "fail" for r in results):
        return "FAIL"
    return "PASS"


def _payload(uplink: dict) -> bytes:
    return bytes.fromhex(uplink.get("hex", "").replace(" ", ""))
