# flaky-hunter

![CI](https://github.com/Kurtis-H/flaky-hunter/actions/workflows/ci.yml/badge.svg)

**Find the tests that pass sometimes and fail other times**, and tell them apart from tests that are actually broken.

Point it at JUnit XML reports from several CI runs (pytest, Playwright, Jest, Maven and most other frameworks produce them), and it tells you which tests are flaky, which regressed, and which have always been failing.

```
$ flaky-hunter examples/runs
12 runs, 7 tests, 4 flaky or failing

[    flaky] tests.ui.shop::test_search_narrows_list  fail=50% flip=45%  ❌✅❌✅✅✅✅❌❌✅
[    flaky] tests.ui.chat::test_chat_replies        fail=17% flip=36%  ❌✅✅✅✅✅✅❌✅✅
[regressed] tests.api.reviews::test_post_review     fail=42% flip=9%   ✅✅✅✅✅❌❌❌❌❌
[   broken] tests.api.auth::test_token_expiry       fail=100% flip=0%  ❌❌❌❌❌❌❌❌❌❌
```

## Why fail rate alone isn't enough

Two tests that both failed 5 out of 12 times can mean completely different things:

| History | Fail rate | Flip rate | What it really is |
|---|---:|---:|---|
| ✅❌✅✅❌✅❌✅❌✅ | 42% | high | **Flaky**: a timing or environment problem |
| ✅✅✅✅✅❌❌❌❌❌ | 42% | low | **Regressed**: a real bug that shipped in run 8 |

**Flip rate** (how often the result changes between consecutive runs) separates them. A regressed test needs a bug fix, and a flaky test needs a stability fix. Retrying a regression only hides it, so that difference matters a lot.

## Statuses

| Status | Meaning | What to do |
|---|---|---|
| `flaky` | Passes and failures mixed | Fix the race or timing issue; quarantine if it flips 30%+ of the time (🚧) |
| `regressed` | Passed, then failed every run since | Find what changed at the first failing run |
| `broken` | Failed every run | A real failure, not flakiness |
| `recovered` | Failed, then passed every run since | Fixed. Nothing to do |
| `stable` | Passed every run | Nothing to do |
| `insufficient` | Fewer runs than `--min-runs` | Wait for more data |

## Install and use

```bash
pip install git+https://github.com/Kurtis-H/flaky-hunter
flaky-hunter reports/                       # a folder of JUnit XML files (one per run)
flaky-hunter "runs/**/*.xml" --format markdown -o flaky.md
flaky-hunter reports/ --format json         # for dashboards or other tools
flaky-hunter reports/ --fail-on-flaky       # exit 1 if anything is flaky (for CI gates)
```

Try it on the included sample data: `python examples/generate_sample.py && flaky-hunter examples/runs`

## GitHub Action

One CI run only produces one report, so the Action keeps a **rolling history of recent runs in the Actions cache**. It adds each new run, drops the oldest, and analyzes the rest. There's no database and no external service, and it costs nothing.

```yaml
- name: Run tests
  run: npx playwright test   # or pytest --junitxml=reports/junit.xml

- name: Hunt flaky tests
  if: always()               # run even when tests fail - that's when it matters
  uses: Kurtis-H/flaky-hunter@v0
  with:
    junit: reports/junit.xml
    history-size: 30
```

The report appears in the job summary. To also post it as a PR comment:

```yaml
- uses: marocchino/sticky-pull-request-comment@v2
  if: always() && github.event_name == 'pull_request'
  with:
    path: flaky-report.md
```

## Design decisions

- **Untrusted input is treated as untrusted.** Report files can come from pull requests, including forks. XML is parsed with `defusedxml` (blocking entity-expansion attacks like "billion laughs"), and test names and error messages are escaped before going into markdown, so a malicious test name can't inject HTML into a PR comment. Both behaviors have tests.
- **Errors are grouped by signature.** `Timeout 5012ms` and `Timeout 5098ms` are the same problem, so long numbers (timings, IDs) are normalized. Short numbers like HTTP status codes are kept, because `got 500` and `got 404` are different bugs.
- **Skips don't count.** A skipped test tells you nothing about flakiness, so it's left out of the history entirely.
- **A retry within one run counts as a failure.** If a test appears twice in one report and failed either time, it failed. Retries hide flakiness; this tool exists to surface it.
- **Bad files are skipped, not fatal.** One corrupt report shouldn't block the whole analysis, so it's reported on stderr and the rest continue.
- **One dependency.** Just `defusedxml`. Everything else is standard library.

## Roadmap

- [ ] Detect which commit a regression started at (needs commit SHA per run)
- [ ] Duration trends: flag tests that are getting slower
- [ ] Quarantine list output that test runners can read to skip known-flaky tests

## License

MIT
