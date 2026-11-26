"""Turn a series of runs into per-test flakiness statistics."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum

from flaky_hunter.parser import Outcome, Run


class Status(StrEnum):
    FLAKY = "flaky"  # passes and failures mixed together
    REGRESSED = "regressed"  # passed, then started failing and kept failing - a real bug
    BROKEN = "broken"  # fails every time - a real bug, not flakiness
    RECOVERED = "recovered"  # failed, then started passing and kept passing - fixed
    STABLE = "stable"  # passes every time
    INSUFFICIENT = "insufficient"  # too few runs to judge


@dataclass
class TestStats:
    test_id: str
    history: list[Outcome] = field(default_factory=list)  # oldest -> newest, skips removed
    errors: Counter = field(default_factory=Counter)
    status: Status = Status.INSUFFICIENT

    @property
    def runs(self) -> int:
        return len(self.history)

    @property
    def failures(self) -> int:
        return sum(1 for o in self.history if o == Outcome.FAILED)

    @property
    def fail_rate(self) -> float:
        return self.failures / self.runs if self.runs else 0.0

    @property
    def flips(self) -> int:
        """How many times the result changed between consecutive runs."""
        return sum(1 for a, b in zip(self.history, self.history[1:], strict=False) if a != b)

    @property
    def flip_rate(self) -> float:
        """Flips per opportunity to flip. 1.0 = alternates every run; ~0 = consistent.

        This separates true flakiness (pass, fail, pass, fail) from a test that broke
        once and stayed broken (pass, pass, fail, fail), which has a low flip rate.
        """
        return self.flips / (self.runs - 1) if self.runs > 1 else 0.0

    @property
    def top_error(self) -> str:
        return self.errors.most_common(1)[0][0] if self.errors else ""

    @property
    def quarantine_candidate(self) -> bool:
        return self.status == Status.FLAKY and self.runs >= 5 and self.flip_rate >= 0.3


_NOISE = [
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I), "UUID"),
    (re.compile(r"0x[0-9a-f]+", re.I), "HEX"),
    # Long numbers (timings, IDs, ports) vary run to run. Short ones like HTTP status
    # codes carry meaning ("got 500" vs "got 404"), so they are kept.
    (re.compile(r"\d+\.\d+|\d{4,}"), "N"),
    (re.compile(r"\s+"), " "),
]


def error_signature(message: str, max_len: int = 120) -> str:
    """Normalise an error so 'Timeout 5012ms' and 'Timeout 5034ms' group together."""
    first_line = message.strip().splitlines()[0] if message.strip() else ""
    for pattern, replacement in _NOISE:
        first_line = pattern.sub(replacement, first_line)
    return first_line.strip()[:max_len]


def analyze(runs: list[Run], min_runs: int = 3) -> list[TestStats]:
    stats: dict[str, TestStats] = {}
    for run in runs:
        for result in run.results.values():
            if result.outcome == Outcome.SKIPPED:
                continue
            s = stats.setdefault(result.test_id, TestStats(result.test_id))
            s.history.append(result.outcome)
            if result.outcome == Outcome.FAILED and result.message:
                s.errors[error_signature(result.message)] += 1

    for s in stats.values():
        if s.runs < min_runs:
            s.status = Status.INSUFFICIENT
        elif s.failures == 0:
            s.status = Status.STABLE
        elif s.failures == s.runs:
            s.status = Status.BROKEN
        elif s.flips == 1:
            # A single change of state is a regression or a fix, not flakiness.
            s.status = Status.REGRESSED if s.history[-1] == Outcome.FAILED else Status.RECOVERED
        else:
            s.status = Status.FLAKY

    order = {
        Status.FLAKY: 0,
        Status.REGRESSED: 1,
        Status.BROKEN: 2,
        Status.RECOVERED: 3,
        Status.INSUFFICIENT: 4,
        Status.STABLE: 5,
    }
    return sorted(
        stats.values(), key=lambda s: (order[s.status], -s.flip_rate, -s.fail_rate, s.test_id)
    )
