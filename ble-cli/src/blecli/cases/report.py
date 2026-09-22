"""Report writer: markdown for humans + JSON sidecar for machines.

Every FAIL/MANUAL case carries the full uplink log with timestamps, so a
report alone is enough to triage a run.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .runner import CaseResult


def write_report(out_path: str | Path, results: list[CaseResult],
                 meta: dict[str, Any] | None = None) -> tuple[Path, dict[str, Any]]:
    """Write <out>.md + <out>.json; returns (md_path, summary)."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    summary = summarize(results)

    md = out.with_suffix(".md")
    js = out.with_suffix(".json")

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines: list[str] = []
    lines.append("# 用例执行报告")
    lines.append("")
    lines.append(f"- 生成时间: {ts}")
    for k, v in (meta or {}).items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append(f"| 汇总 | 数量 |")
    lines.append(f"|---|---|")
    lines.append(f"| 总计 | {summary['total']} |")
    lines.append(f"| PASS | {summary['pass']} |")
    lines.append(f"| FAIL | {summary['fail']} |")
    lines.append(f"| MANUAL | {summary['manual']} |")
    lines.append("")

    for r in results:
        lines.append(f"## {r.case_id} — {r.result}")
        lines.append("")
        lines.append(f"- 分组: {r.group_title or r.group}")
        lines.append(f"- 规则: {r.rule or '无'}")
        if r.injected:
            lines.append(f"- 注入: {', '.join(r.injected)}")
        if r.instruction:
            lines.append(f"- 指引: {r.instruction}")
        for reason in r.reasons:
            lines.append(f"- 原因: {reason}")
        if r.assertion_results:
            lines.append("")
            lines.append("| 断言 | 状态 | 命中 | 证据 |")
            lines.append("|---|---|---|---|")
            for a in r.assertion_results:
                lines.append(f"| {a['name']} | {a['status']} | {a['matched']} | "
                             f"{', '.join(a.get('evidence') or [])} |")
        if r.human_checks:
            lines.append("")
            lines.append("| 人工检查 | 答复 |")
            lines.append("|---|---|")
            for h in r.human_checks:
                lines.append(f"| {h['prompt']} | {h.get('answer', '-')} |")
        lines.append("")
        lines.append(f"### 上行日志（{len(r.uplinks)} 条）")
        lines.append("")
        if r.uplinks:
            lines.append("| 时间 | 类型 | hex | 字段 |")
            lines.append("|---|---|---|---|")
            for u in r.uplinks:
                fields = ", ".join(f"{k}={v}" for k, v in u.get("fields", {}).items())
                lines.append(f"| {u.get('ts', '-')} | {u.get('type', 'unknown')} | "
                             f"`{u.get('hex', '')}` | {fields} |")
        else:
            lines.append("（无上行）")
        lines.append("")

    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    js.write_text(json.dumps({
        "meta": meta or {},
        "summary": summary,
        "cases": [r.to_dict() for r in results],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return md, summary


def summarize(results: list[CaseResult]) -> dict[str, int]:
    return {
        "total": len(results),
        "pass": sum(1 for r in results if r.result == "PASS"),
        "fail": sum(1 for r in results if r.result == "FAIL"),
        "manual": sum(1 for r in results if r.result == "MANUAL"),
    }


def read_summary(report_path: str | Path) -> dict[str, Any]:
    """Load the JSON sidecar of a report (used by `ble-cli report`)."""
    js = Path(report_path).with_suffix(".json")
    if not js.exists():
        raise FileNotFoundError(f"report sidecar not found: {js}")
    data = json.loads(js.read_text(encoding="utf-8"))
    return {"path": str(js), "summary": data.get("summary"), "meta": data.get("meta")}
