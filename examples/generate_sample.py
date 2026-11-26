"""Generate 12 fake CI runs of JUnit XML with a few planted flaky and broken tests.

Run: python examples/generate_sample.py   (writes examples/runs/run-01.xml ... run-12.xml)
"""

import random
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import quoteattr

OUT = Path(__file__).parent / "runs"
START = datetime(2026, 11, 1, 9, 0, 0)

# (classname, name, behaviour)
TESTS = [
    ("tests.api.products", "test_lists_products", "stable"),
    ("tests.api.products", "test_search_filters", "stable"),
    ("tests.api.orders", "test_read_own_order", "stable"),
    ("tests.ui.shop", "test_search_narrows_list", "flaky_timeout"),  # classic UI race
    ("tests.ui.chat", "test_chat_replies", "flaky_rare"),  # fails ~1 in 6
    ("tests.api.reviews", "test_post_review", "broke_midway"),  # regression at run 8
    ("tests.api.auth", "test_token_expiry", "always_fails"),
]


def outcome(behaviour: str, run: int, rng: random.Random) -> tuple[str, str]:
    if behaviour == "flaky_timeout" and rng.random() < 0.45:
        return (
            "failure",
            f"TimeoutError: locator.click: Timeout {rng.randint(5000, 5100)}ms exceeded",
        )
    if behaviour == "flaky_rare" and rng.random() < 0.18:
        return "failure", "AssertionError: expected chat log to contain 'ShopBot:'"
    if behaviour == "broke_midway" and run >= 8:
        return "failure", "AssertionError: expected status 201, got 500"
    if behaviour == "always_fails":
        return "failure", f"AssertionError: token still valid after {rng.randint(3600, 3700)}s"
    return "pass", ""


def main() -> None:
    rng = random.Random(42)
    OUT.mkdir(exist_ok=True)
    for run in range(1, 13):
        ts = (START + timedelta(hours=6 * run)).isoformat()
        cases = []
        for classname, name, behaviour in TESTS:
            kind, msg = outcome(behaviour, run, rng)
            time = f"{rng.uniform(0.05, 2.5):.3f}"
            attrs = (
                f"classname={quoteattr(classname)} name={quoteattr(name)} time={quoteattr(time)}"
            )
            if kind == "failure":
                cases.append(
                    f"    <testcase {attrs}>\n"
                    f"      <failure message={quoteattr(msg)}>{msg}</failure>\n    </testcase>"
                )
            else:
                cases.append(f"    <testcase {attrs}/>")
        failures = sum("<failure" in c for c in cases)
        xml = (
            '<?xml version="1.0" encoding="utf-8"?>\n<testsuites>\n'
            f'  <testsuite name="pytest" tests="{len(cases)}" failures="{failures}" '
            f'timestamp="{ts}">\n' + "\n".join(cases) + "\n  </testsuite>\n</testsuites>\n"
        )
        (OUT / f"run-{run:02d}.xml").write_text(xml, encoding="utf-8")
    print(f"Wrote 12 runs to {OUT}")


if __name__ == "__main__":
    main()
