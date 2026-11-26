"""Parse JUnit XML files (pytest, Playwright, Jest, Maven...) into test runs.

Each file is treated as one CI run. XML is parsed with defusedxml because report files
can come from forks and pull requests - i.e. untrusted input - and the standard library
parser is exposed to entity-expansion attacks ("billion laughs").
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException


class Outcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class TestResult:
    test_id: str
    outcome: Outcome
    message: str = ""
    duration: float = 0.0


@dataclass
class Run:
    source: str
    timestamp: datetime | None
    results: dict[str, TestResult] = field(default_factory=dict)


class ReportError(Exception):
    """A report file could not be read. The CLI skips it with a warning."""


def _test_id(case) -> str:
    classname = (case.get("classname") or "").strip()
    name = (case.get("name") or "").strip()
    return f"{classname}::{name}" if classname else name


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _outcome(case) -> tuple[Outcome, str]:
    for tag in ("failure", "error"):
        node = case.find(tag)
        if node is not None:
            message = node.get("message") or (node.text or "")
            return Outcome.FAILED, message.strip()
    if case.find("skipped") is not None:
        return Outcome.SKIPPED, ""
    return Outcome.PASSED, ""


def parse_junit(path: str) -> Run:
    try:
        root = SafeET.parse(path).getroot()
    except DefusedXmlException as exc:
        raise ReportError(f"{path}: rejected unsafe XML ({type(exc).__name__})") from exc
    except (SafeET.ParseError, OSError) as exc:
        raise ReportError(f"{path}: {exc}") from exc

    suites = [root] if root.tag == "testsuite" else root.iter("testsuite")
    if root.tag not in ("testsuite", "testsuites"):
        raise ReportError(f"{path}: not a JUnit report (root element <{root.tag}>)")

    run = Run(source=os.path.basename(path), timestamp=None)
    timestamps = []
    for suite in suites:
        ts = _parse_timestamp(suite.get("timestamp"))
        if ts:
            timestamps.append(ts)
        for case in suite.iter("testcase"):
            outcome, message = _outcome(case)
            test_id = _test_id(case)
            try:
                duration = float(case.get("time") or 0)
            except ValueError:
                duration = 0.0
            # If a test appears twice in one file (e.g. a retry), any failure wins.
            existing = run.results.get(test_id)
            if existing and existing.outcome == Outcome.FAILED:
                continue
            run.results[test_id] = TestResult(test_id, outcome, message, duration)

    if not timestamps:
        ts = _parse_timestamp(root.get("timestamp"))
        if ts:
            timestamps.append(ts)
    run.timestamp = min(timestamps) if timestamps else None
    return run


def order_runs(runs: list[Run]) -> list[Run]:
    """Oldest first. Uses report timestamps when every run has one, otherwise file names."""
    if runs and all(r.timestamp for r in runs):
        # Normalise naive and aware datetimes so they compare cleanly.
        return sorted(
            runs,
            key=lambda r: (
                r.timestamp.replace(tzinfo=None) if r.timestamp else datetime.min,
                r.source,
            ),
        )
    return sorted(runs, key=lambda r: r.source)
