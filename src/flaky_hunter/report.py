"""Render analysis results as Markdown (PR comments, job summaries), JSON or plain text."""

from __future__ import annotations

import json

from flaky_hunter.analyzer import Status, TestStats
from flaky_hunter.parser import Outcome

ICONS = {Outcome.PASSED: "✅", Outcome.FAILED: "❌"}


def _md_escape(text: str) -> str:
    """Test names and error messages are untrusted text; keep them from breaking the table
    or injecting HTML/markdown into a PR comment."""
    return (
        text.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("`", "'")
        .replace("\n", " ")
    )


def _spark(history: list[Outcome], last: int = 10) -> str:
    return "".join(ICONS[o] for o in history[-last:])


def to_markdown(stats: list[TestStats], run_count: int, limit: int = 20) -> str:
    flaky = [s for s in stats if s.status == Status.FLAKY]
    regressed = [s for s in stats if s.status == Status.REGRESSED]
    broken = [s for s in stats if s.status == Status.BROKEN]
    lines = [
        "## 🔁 Flaky test report",
        "",
        f"Analyzed **{run_count}** runs and **{len(stats)}** tests: "
        f"**{len(flaky)} flaky**, **{len(regressed)} regressed**, "
        f"**{len(broken)} always failing**.",
        "",
    ]
    if flaky:
        lines += [
            "### Flaky tests",
            "",
            "| Test | Runs | Fail rate | Flip rate | Last 10 (old → new) | Most common error |",
            "|---|---:|---:|---:|---|---|",
        ]
        for s in flaky[:limit]:
            flag = " 🚧" if s.quarantine_candidate else ""
            lines.append(
                f"| `{_md_escape(s.test_id)}`{flag} | {s.runs} | "
                f"{s.fail_rate:.0%} | {s.flip_rate:.0%} | {_spark(s.history)} | "
                f"{_md_escape(s.top_error) or '-'} |"
            )
        if len(flaky) > limit:
            lines.append(f"\n_...and {len(flaky) - limit} more._")
        if any(s.quarantine_candidate for s in flaky):
            lines += [
                "",
                "🚧 = quarantine candidate (5+ runs, flips at least 30% of the time).",
            ]
        lines.append("")
    if regressed:
        lines += ["### Regressed (passed, then started failing every run)", ""]
        for s in regressed:
            first_fail = s.runs - s.history[::-1].index(Outcome.PASSED)
            lines.append(
                f"- `{_md_escape(s.test_id)}`: failing since run {first_fail + 1} of {s.runs} "
                f"{_spark(s.history)} - {_md_escape(s.top_error) or '-'}"
            )
        lines.append("")
    if broken:
        lines += ["### Always failing (real failures, not flakiness)", ""]
        lines += [f"- `{_md_escape(s.test_id)}`: {_md_escape(s.top_error) or '-'}" for s in broken]
        lines.append("")
    if not flaky and not regressed and not broken:
        lines += ["No flaky or failing tests found. 🎉", ""]
    lines.append(
        "<sub>Flip rate = how often the result changed between consecutive runs. "
        "High flip rate means flaky; low flip rate with failures means it broke and stayed broken."
        "</sub>"
    )
    return "\n".join(lines) + "\n"


def to_json(stats: list[TestStats], run_count: int) -> str:
    return json.dumps(
        {
            "runs": run_count,
            "tests": [
                {
                    "test_id": s.test_id,
                    "status": s.status.value,
                    "runs": s.runs,
                    "failures": s.failures,
                    "fail_rate": round(s.fail_rate, 4),
                    "flips": s.flips,
                    "flip_rate": round(s.flip_rate, 4),
                    "quarantine_candidate": s.quarantine_candidate,
                    "history": [o.value for o in s.history],
                    "errors": dict(s.errors.most_common()),
                }
                for s in stats
            ],
        },
        indent=2,
    )


def to_text(stats: list[TestStats], run_count: int) -> str:
    shown = [s for s in stats if s.status in (Status.FLAKY, Status.REGRESSED, Status.BROKEN)]
    out = [f"{run_count} runs, {len(stats)} tests, {len(shown)} flaky or failing", ""]
    for s in shown:
        out.append(
            f"[{s.status.value:>9}] {s.test_id}  fail={s.fail_rate:.0%} "
            f"flip={s.flip_rate:.0%}  {_spark(s.history)}"
        )
    return "\n".join(out) + "\n"
