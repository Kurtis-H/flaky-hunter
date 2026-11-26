from pathlib import Path

import pytest

from flaky_hunter.analyzer import Status, analyze, error_signature
from flaky_hunter.cli import EXIT_FLAKY_FOUND, EXIT_OK, EXIT_USAGE, main
from flaky_hunter.parser import Outcome, ReportError, order_runs, parse_junit
from flaky_hunter.report import to_markdown


def write_run(tmp_path: Path, name: str, cases: dict[str, str], timestamp: str = "") -> Path:
    """cases: {"Class::test": "pass" | "fail" | "skip" | "error"}"""
    body = []
    for test_id, result in cases.items():
        classname, test_name = test_id.split("::")
        child = {
            "pass": "",
            "fail": '<failure message="boom 12345ms">trace</failure>',
            "error": '<error message="crashed">trace</error>',
            "skip": "<skipped/>",
        }[result]
        body.append(
            f'<testcase classname="{classname}" name="{test_name}" time="0.1">{child}</testcase>'
        )
    ts = f' timestamp="{timestamp}"' if timestamp else ""
    xml = f'<testsuites><testsuite name="s"{ts}>{"".join(body)}</testsuite></testsuites>'
    path = tmp_path / name
    path.write_text(xml, encoding="utf-8")
    return path


def runs_from(tmp_path, sequence: list[str], test_id: str = "A::t"):
    return [
        parse_junit(str(write_run(tmp_path, f"run-{i:02d}.xml", {test_id: r})))
        for i, r in enumerate(sequence)
    ]


# --- parsing -----------------------------------------------------------------


def test_parses_outcomes_and_messages(tmp_path):
    run = parse_junit(
        str(
            write_run(
                tmp_path,
                "r.xml",
                {"A::ok": "pass", "A::bad": "fail", "A::err": "error", "A::s": "skip"},
            )
        )
    )
    assert run.results["A::ok"].outcome == Outcome.PASSED
    assert run.results["A::bad"].outcome == Outcome.FAILED
    assert run.results["A::bad"].message == "boom 12345ms"
    assert run.results["A::err"].outcome == Outcome.FAILED, "errors count as failures"
    assert run.results["A::s"].outcome == Outcome.SKIPPED


def test_parses_bare_testsuite_root(tmp_path):
    path = tmp_path / "r.xml"
    path.write_text('<testsuite name="x"><testcase classname="A" name="t"/></testsuite>')
    assert "A::t" in parse_junit(str(path)).results


def test_failure_wins_when_test_appears_twice(tmp_path):
    path = tmp_path / "r.xml"
    path.write_text(
        '<testsuite><testcase classname="A" name="t"><failure message="x"/></testcase>'
        '<testcase classname="A" name="t"/></testsuite>'
    )
    assert parse_junit(str(path)).results["A::t"].outcome == Outcome.FAILED


def test_rejects_entity_expansion_attack(tmp_path):
    path = tmp_path / "evil.xml"
    path.write_text(
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
        "<testsuite><testcase classname='A' name='&lol2;'/></testsuite>"
    )
    with pytest.raises(ReportError, match="unsafe XML"):
        parse_junit(str(path))


@pytest.mark.parametrize("content", ["not xml at all", "<html><body/></html>"])
def test_rejects_non_junit_files(tmp_path, content):
    path = tmp_path / "bad.xml"
    path.write_text(content)
    with pytest.raises(ReportError):
        parse_junit(str(path))


def test_orders_runs_by_timestamp_not_filename(tmp_path):
    later = parse_junit(str(write_run(tmp_path, "a.xml", {"A::t": "pass"}, "2026-11-02T00:00:00")))
    earlier = parse_junit(
        str(write_run(tmp_path, "b.xml", {"A::t": "pass"}, "2026-11-01T00:00:00"))
    )
    assert [r.source for r in order_runs([later, earlier])] == ["b.xml", "a.xml"]


def test_falls_back_to_filename_order_without_timestamps(tmp_path):
    b = parse_junit(str(write_run(tmp_path, "b.xml", {"A::t": "pass"})))
    a = parse_junit(str(write_run(tmp_path, "a.xml", {"A::t": "pass"})))
    assert [r.source for r in order_runs([b, a])] == ["a.xml", "b.xml"]


# --- classification -----------------------------------------------------------


@pytest.mark.parametrize(
    ("sequence", "expected"),
    [
        (["pass"] * 5, Status.STABLE),
        (["fail"] * 5, Status.BROKEN),
        (["pass", "fail", "pass", "fail", "pass"], Status.FLAKY),
        (["pass", "pass", "pass", "fail", "fail"], Status.REGRESSED),
        (["fail", "fail", "pass", "pass", "pass"], Status.RECOVERED),
        (["pass", "fail"], Status.INSUFFICIENT),
    ],
)
def test_classification(tmp_path, sequence, expected):
    (stats,) = analyze(runs_from(tmp_path, sequence))
    assert stats.status == expected


def test_skips_are_ignored_not_counted_as_runs(tmp_path):
    (stats,) = analyze(runs_from(tmp_path, ["pass", "skip", "pass", "skip", "pass"]))
    assert stats.runs == 3
    assert stats.status == Status.STABLE


def test_flip_rate_maths(tmp_path):
    (stats,) = analyze(runs_from(tmp_path, ["pass", "fail", "pass", "fail", "pass"]))
    assert stats.flips == 4
    assert stats.flip_rate == 1.0
    assert stats.fail_rate == pytest.approx(0.4)


def test_quarantine_candidate_needs_enough_runs_and_flips(tmp_path):
    (tmp_path / "often").mkdir()
    (tmp_path / "rare").mkdir()
    (often,) = analyze(runs_from(tmp_path / "often", ["pass", "fail"] * 3))
    (rare,) = analyze(runs_from(tmp_path / "rare", ["pass"] * 8 + ["fail", "pass"]))
    assert often.quarantine_candidate
    assert rare.status == Status.FLAKY and not rare.quarantine_candidate


def test_flaky_tests_sort_first(tmp_path):
    runs = [
        parse_junit(str(write_run(tmp_path, f"r{i}.xml", {"A::stable": "pass", "A::flaky": r})))
        for i, r in enumerate(["pass", "fail", "pass", "fail"])
    ]
    assert [s.test_id for s in analyze(runs)] == ["A::flaky", "A::stable"]


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("Timeout 5012ms exceeded", "Timeout 5098ms exceeded", True),
        ("expected 201, got 500", "expected 201, got 404", False),  # status codes matter
        ("took 1.234s", "took 9.876s", True),
        ("id 0xdeadbeef", "id 0xcafe", True),
        ("first line\nstack trace 1", "first line\nstack trace 2", True),
    ],
)
def test_error_signature_grouping(a, b, same):
    assert (error_signature(a) == error_signature(b)) is same


# --- reporting ------------------------------------------------------------------


def test_markdown_escapes_untrusted_text(tmp_path):
    path = tmp_path / "r1.xml"
    runs = []
    for i, result in enumerate(["pass", "fail", "pass", "fail"]):
        failure = (
            '<failure message="&lt;img src=x onerror=alert(1)&gt; | col"/>'
            if result == "fail"
            else ""
        )
        path = tmp_path / f"r{i}.xml"
        path.write_text(
            f'<testsuite><testcase classname="A" name="t|x">{failure}</testcase></testsuite>'
        )
        runs.append(parse_junit(str(path)))
    md = to_markdown(analyze(runs), len(runs))
    assert "<img" not in md
    assert "&lt;img" in md
    assert "t\\|x" in md, "pipes must not break the table"


# --- CLI --------------------------------------------------------------------------


def test_cli_exit_codes(tmp_path, capsys):
    runs_from(tmp_path, ["pass", "fail", "pass", "fail"])
    assert main([str(tmp_path), "--fail-on-flaky"]) == EXIT_FLAKY_FOUND
    assert main([str(tmp_path)]) == EXIT_OK
    assert main([str(tmp_path / "nothing-here" / "*.xml")]) == EXIT_USAGE


def test_cli_skips_bad_files_and_keeps_going(tmp_path, capsys):
    runs_from(tmp_path, ["pass", "pass", "pass"])
    (tmp_path / "broken.xml").write_text("garbage")
    assert main([str(tmp_path), "--format", "json"]) == EXIT_OK
    captured = capsys.readouterr()
    assert "skipping" in captured.err
    assert '"runs": 3' in captured.out


def test_cli_writes_output_file(tmp_path):
    runs_from(tmp_path, ["pass", "fail", "pass"])
    out = tmp_path / "report.md"
    assert main([str(tmp_path / "*.xml"), "--format", "markdown", "-o", str(out)]) == EXIT_OK
    assert out.read_text().startswith("## 🔁 Flaky test report")


def test_example_data_finds_planted_problems():
    examples = Path(__file__).parent.parent / "examples" / "runs"
    runs = order_runs([parse_junit(str(p)) for p in sorted(examples.glob("*.xml"))])
    by_id = {s.test_id: s.status for s in analyze(runs)}
    assert by_id["tests.ui.shop::test_search_narrows_list"] == Status.FLAKY
    assert by_id["tests.api.reviews::test_post_review"] == Status.REGRESSED
    assert by_id["tests.api.auth::test_token_expiry"] == Status.BROKEN
    assert by_id["tests.api.products::test_lists_products"] == Status.STABLE
