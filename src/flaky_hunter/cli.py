"""Command-line entry point: `flaky-hunter reports/**/*.xml`."""

from __future__ import annotations

import argparse
import glob
import os
import sys

from flaky_hunter import __version__
from flaky_hunter.analyzer import Status, analyze
from flaky_hunter.parser import ReportError, order_runs, parse_junit
from flaky_hunter.report import to_json, to_markdown, to_text

EXIT_OK, EXIT_FLAKY_FOUND, EXIT_USAGE = 0, 1, 2


def _expand(patterns: list[str]) -> list[str]:
    files: list[str] = []
    for pattern in patterns:
        if os.path.isdir(pattern):
            pattern = os.path.join(pattern, "**", "*.xml")
        files.extend(glob.glob(pattern, recursive=True))
    return sorted(set(files))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="flaky-hunter",
        description="Find flaky tests by comparing JUnit XML reports from many CI runs.",
    )
    p.add_argument(
        "paths", nargs="+", help="JUnit XML files, globs or directories (one file per run)"
    )
    p.add_argument("--format", choices=["markdown", "json", "text"], default="text")
    p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")
    p.add_argument(
        "--min-runs", type=int, default=3, help="Runs needed before judging a test (default 3)"
    )
    p.add_argument("--limit", type=int, default=20, help="Max flaky tests listed in markdown")
    p.add_argument("--fail-on-flaky", action="store_true", help="Exit 1 if any flaky test is found")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = p.parse_args(argv)

    files = _expand(args.paths)
    if not files:
        print("flaky-hunter: no report files matched", file=sys.stderr)
        return EXIT_USAGE

    runs = []
    for path in files:
        try:
            runs.append(parse_junit(path))
        except ReportError as exc:
            print(f"flaky-hunter: skipping {exc}", file=sys.stderr)
    if not runs:
        print("flaky-hunter: no readable reports", file=sys.stderr)
        return EXIT_USAGE

    stats = analyze(order_runs(runs), min_runs=args.min_runs)
    if args.format == "markdown":
        report = to_markdown(stats, len(runs), args.limit)
    elif args.format == "json":
        report = to_json(stats, len(runs))
    else:
        report = to_text(stats, len(runs))

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report)
    else:
        sys.stdout.write(report)

    if args.fail_on_flaky and any(s.status == Status.FLAKY for s in stats):
        return EXIT_FLAKY_FOUND
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
