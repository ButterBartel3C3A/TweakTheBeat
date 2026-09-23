"""Case runner (D3): the state machine around one or many cases.

Per case: open a fresh session (re-connect + handshake replay), inject or
wait for physical stimulus, collect uplinks, evaluate the assertion rule,
and ask the tester for confirmation when the rule carries human checks or
the case is physical (``ble-cli confirm`` in another process writes the
state file; the blocked runner polls it).

Verdicts (design matrix):
    PASS   - rule satisfied, no forbidden uplinks, human checks confirmed
    FAIL   - assertion failed / window timed out / infrastructure error
    MANUAL - no rule, parse failure, wildcard inject without rule,
             divergence noted, expect_none violated, unconfirmed human step
"""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..core.connection import Session, close_session, open_session
from ..core.state import State
from ..errors import BleCliError
from .parser import Case
from .rules import Rule, evaluate, find_rule, load_rule, result_of

DEFAULT_CONFIRM_TIMEOUT_S = 600.0
DEFAULT_RULELESS_WINDOW_MS = 3000
CONFIRM_POLL_S = 0.5


@dataclass
class RunOptions:
    rules_dir: Path
    state_path: Path
    confirm_timeout_s: float = DEFAULT_CONFIRM_TIMEOUT_S
    ruleless_window_ms: int = DEFAULT_RULELESS_WINDOW_MS
    progress: Callable[[str], None] = lambda msg: print(msg, file=sys.stderr, flush=True)


@dataclass
class CaseResult:
    case_id: str
    group: str
    group_title: str
    result: str                       # PASS | FAIL | MANUAL
    uplinks: list[dict]
    assertion_results: list[dict] = field(default_factory=list)
    human_checks: list[dict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    injected: list[str] = field(default_factory=list)
    instruction: str | None = None
    duration_ms: int = 0
    rule: str | None = None           # path of the rule file, if any

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "group": self.group,
            "group_title": self.group_title,
            "result": self.result,
            "injected": self.injected,
            "instruction": self.instruction,
            "uplinks": self.uplinks,
            "assertion_results": self.assertion_results,
            "human_checks": self.human_checks,
            "reasons": self.reasons,
            "duration_ms": self.duration_ms,
            "rule": self.rule,
        }


async def run_case(
    case: Case,
    profile,
    backend_factory: Callable[[], Any],
    state_path: Path,
    opts: RunOptions,
    address: str | None,
) -> CaseResult:
    started = time.monotonic()
    rule_path = find_rule(opts.rules_dir, case.id)
    rule = load_rule(rule_path) if rule_path else None

    result = CaseResult(case_id=case.id, group=case.group,
                        group_title=case.group_title, result="MANUAL", uplinks=[],
                        rule=str(rule_path) if rule_path else None)

    # --- resolve what to inject -------------------------------------------
    if rule is not None:
        inject_bytes: list[bytes] = list(rule.inject)
        mode = rule.mode
        instruction = rule.instruction or (case.physical or None)
    else:
        mode = _infer_mode(case)
        instruction = case.physical or None
        inject_bytes = []
        if case.inject:
            primary = case.inject.alternatives[0]
            if len(case.inject.alternatives) > 1:
                result.reasons.append("inject has alternatives; only the first was considered")
            if any(s.has_wildcard for s in primary):
                result.reasons.append(
                    "inject frame contains XX wildcards; a rule file with concrete frames is required")
                inject_bytes = []
            else:
                try:
                    inject_bytes = [bytes.fromhex(s.raw.replace(" ", "")) for s in primary
                                    for _ in range(s.repeat)]
                except ValueError as exc:
                    result.reasons.append(f"inject frame unparseable: {exc}")
                    inject_bytes = []

    if not case.valid:
        result.reasons.append("parse errors: " + "; ".join(case.parse_errors))
        result.duration_ms = _elapsed(started)
        return result

    if rule is not None and rule.divergence:
        result.reasons.append(f"divergence noted in rule: {rule.divergence}")

    # --- device interaction ------------------------------------------------
    backend = None
    session: Session | None = None
    try:
        backend = backend_factory()
        session = await open_session(backend, profile, address=address,
                                     state_address=None)
    except BleCliError as exc:
        result.result = "FAIL"
        result.reasons.append(f"infra: {exc.code}: {exc.message}")
        if backend is not None:
            await backend.disconnect()
        result.duration_ms = _elapsed(started)
        return result

    try:
        if inject_bytes:
            session.recorder.clear()
            for frame in inject_bytes:
                await session.backend.write(profile.write_char_uuid, frame,
                                            profile.write_with_response)
                result.injected.append(" ".join(f"{b:02X}" for b in frame))

        if mode == "physical" or (rule is not None and rule.checks):
            # block on the tester (instruction may be given even in inject
            # mode when the rule carries human visual checks)
            if instruction:
                result.instruction = instruction
                opts.progress(f"[{case.id}] 指引: {instruction}")
            answer = await _wait_for_confirm(
                case.id, instruction or case.physical or "",
                [{"id": c["id"], "prompt": c["prompt"]} for c in (rule.checks if rule else [])],
                state_path, opts.confirm_timeout_s)
            if answer is None:
                result.reasons.append("physical step not confirmed (timeout)")
                result.human_checks = [
                    {**c, "answer": "unanswered"} for c in (rule.checks if rule else [])]
            else:
                result.human_checks = [
                    {**c, "answer": answer["answer"]} for c in (rule.checks if rule else [])]
                if answer["answer"] != "yes":
                    result.reasons.append("tester answered 'no' to the human checks")

        # --- assertion window ----------------------------------------------
        window_ms = rule.timeout_ms if rule is not None else opts.ruleless_window_ms
        await asyncio.sleep(window_ms / 1000.0)
        result.uplinks = session.recorder.snapshot()

        if rule is None or not rule.has_assertions:
            result.assertion_results = []
            result.reasons.append("no assertion rule for this case")
        else:
            result.assertion_results = evaluate(rule, result.uplinks, profile.hooks)

        human_confirmed = not (rule is not None and rule.checks and not result.human_checks)
        human_declined = any(h.get("answer") == "no" for h in result.human_checks)
        result.result = result_of(
            result.assertion_results,
            had_rule=(rule is not None and rule.has_assertions),
            human_confirmed=human_confirmed,
            human_declined=human_declined,
            divergence=rule.divergence if rule else None,
        )
    finally:
        await close_session(session)

    result.duration_ms = _elapsed(started)
    return result


async def _wait_for_confirm(case_id: str, instruction: str,
                            checks: list[dict[str, str]], state_path: Path,
                            timeout_s: float) -> dict[str, Any] | None:
    """Announce a pending confirmation in the state file, then poll it until
    a `ble-cli confirm` process (tester) answers, or the timeout expires."""
    state = State(state_path)
    state.set_pending_confirm(case_id, instruction, checks)
    state.save()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            fresh = State(state_path)
        except BleCliError:
            await asyncio.sleep(CONFIRM_POLL_S)
            continue
        lc = fresh.data.get("last_confirm")
        if fresh.pending_confirm() is None and lc and lc.get("case_id") == case_id:
            return lc
        await asyncio.sleep(CONFIRM_POLL_S)
    # timeout: clear our own pending marker (if the tester confirms exactly
    # now, their write wins because we only clear a matching marker)
    try:
        fresh = State(state_path)
        pc = fresh.pending_confirm()
        if pc and pc.get("case_id") == case_id:
            fresh.clear_pending_confirm()
            fresh.save()
    except BleCliError:
        pass
    return None


def _infer_mode(case: Case) -> str:
    if case.inject is None and case.physical:
        return "physical"
    if case.inject is None:
        return "observe"
    return "inject"


def _elapsed(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
