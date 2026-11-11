# 7. Parallel execution (with Playwright)

```bash
python run_tests.py -w 4                         # 4 workers, one unit per feature file
python run_tests.py -w 8 --scheme scenario       # one unit per scenario / outline row
python run_tests.py --shard 2/3                  # CI machine 2 of 3
pyassure-run -w 4 --allure                       # installed command + Allure
```
| option | meaning |
|---|---|
| `paths...` | feature files/folders (default `app/features`) |
| `-w N\|auto` | processes (auto = min(4, CPUs)); defaults in `parallel:` |
| `--scheme feature\|scenario` | unit of parallelism |
| `-t EXPR` | behave tags (repeat = AND, comma = OR; replaces `default_tags`) |
| `-D k=v` | user data for every worker (`-D browser=firefox`) |
| `--retries N` / `--timeout S` / `--fail-fast` | flaky-test retries, per-unit kill, stop starting new units after a failure |
| `--shard I/N` | run only shard I of N (deterministic round-robin) |
| `--allure` / `--no-allure` | force Allure on/off (else `pyassure.yml`) |
| `--list` | show units and exit |

Exit code: 0 all passed - 1 failures - 2 nothing selected.

## How it works
```
discover units (behave tag rules) -> [--shard] -> start app server once -> warm up sessions once
   -> N behave processes  [-D report_dir=<run>/workers/<unit> -D worker=<unit> -D allure_dir=<shared>]
   -> merge results -> one report (+ Allure HTML)
```
Why **processes**: Playwright's sync API allows one driver per process; each worker owns its browser and gives each scenario a fresh context - no shared state, no locks in tests.

## Recommended Playwright setup
1. **Parallelise by process, isolate by context.** One browser per worker, one context per scenario (already how PyAssure works).
2. **Log in once.** Put the session in `sessions.warmup`; the runner creates the storageState before workers start and they all reuse it. Prefer
   `kind="api"` providers (no browser needed). A file lock covers sessions that were not warmed up.
3. **Start the app once.** `web_server` is started by the runner; workers reuse it and never stop it.
4. **Pick the scheme by shape.** Many similar feature files -> `feature`. A few big files or slow UI scenarios -> `scenario` (best balance; slowest units start first from `.durations.json`).
5. **Retry precisely.** `--scheme scenario --retries 1` re-runs only the failed scenario (in `feature` scheme the whole file reruns). Retries hide flakiness - track attempts in the report/bdd index.
6. **Size workers to the weakest resource**: CPU for browsers (~1 core and 300-500 MB per Chromium worker), but also DB sessions / warehouse concurrency / API rate limits. Start at 3-4.
7. **Make data parallel-safe.** Independent scenarios, own test data, cleanup, filtered comparisons ([6.5](06-api-testing.md)). Never let two scenarios write the same record.
8. **Scale across machines with `--shard`.** Each CI job runs `--shard k/n` with its own workers; merge the JSON/Allure results afterwards.
9. **Debug a single failure** headed and serial: `behave -D headless=false -D browser=chromium app/features/x.feature:12`, with `trace_on_failure: true`.
10. **Keep runs hermetic**: fresh sessions per CI run (`.pyassure/` is git-ignored), pinned browser versions (`playwright install` of the version in `requirements.txt`).

## Outputs
```
reports/
  validation_report.html / .json   latest merged PyAssure report      bdd_index.html   links to each unit's standard BDD report
  allure-report/                   (when Allure is on and the CLI exists)
  runs/<timestamp>/  validation_report.html  validation_results.json  allure-results/  junit/  run_summary.json  workers/<unit>/{behave.log,...}
```
A worker that crashes before reporting appears as an *error* scenario with its last log lines. The standard behave HTML cannot be merged; `bdd_index.html` links to each one.

## CI example
```yaml
- run: pip install -e ".[ui,allure,snowflake]" && playwright install --with-deps chromium
- run: python run_tests.py -w 4 --scheme scenario --retries 1 --allure --shard ${{ matrix.shard }}/3 --env QA
  env: {PYASSURE_KEY: "${{ secrets.PYASSURE_KEY }}", PYASSURE_CONNECTIONS: app/config/connections.qa.yml}
- uses: actions/upload-artifact@v4
  if: always()
  with: {name: "reports-${{ matrix.shard }}", path: reports/}
```
