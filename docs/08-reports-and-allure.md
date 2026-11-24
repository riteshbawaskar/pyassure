# 8. Reports (and Allure)

Every run can produce up to **four** outputs; all carry the PyAssure name and version.

| Report | File | Switch |
|---|---|---|
| **PyAssure standalone HTML** | `reports/validation_report.html` (+ `validation_results.json`) | `reports.standalone_html` |
| **Standard BDD HTML** | `reports/behave_report.html` | `behave.ini` (`format`) |
| **JUnit XML** | `reports/junit/*.xml` | `behave.ini` (`junit`) |
| **Allure** | `reports/allure-results/` -> `reports/allure-report/` | `reports.allure.enabled` |

When `run_tests.py` updates the latest merged report files, any previous `validation_report.html`,
`validation_results.json`, and `bdd_index.html` are moved to `reports/log/` with the run timestamp
appended to each filename. Both plain `behave` and `run_tests.py` save a copy of each newly generated
standalone validation report as `reports/backup/validation_report_<timestamp>.html` (or under the
selected report directory). Existing timestamped run folders under `reports/runs/` are also retained.

## 8.1 PyAssure standalone report
One self-contained file (CSS, JS, screenshots inlined; works offline; dark-mode aware). Header: name/version, title, time, environment (+ workers/scheme/wall time/shard
for parallel runs). Feature and scenario headings use character-based icons. Summary cards, filters (*All / Failures only / Tag / Expand / Collapse*); the tag filter combines with the status filter. Per scenario: status, tags, duration, worker, fact chips. Per checkpoint:
`PASSED | FAILED | WARNING`, expected vs actual, message and **evidence tables** (missing keys, mismatching values, invalid rows, differing cells). Plus an **API calls** table and
**screenshots**. Failed scenarios open automatically; tables are capped by `max_report_rows`.

## 8.2 Allure - enable / disable in config
```yaml
# app/config/pyassure.yml
reports:
  allure:
    enabled: true                      # <- the switch
    results_dir: reports/allure-results
    report_dir: reports/allure-report
    generate: true                     # runner builds the HTML if the Allure CLI is found
    cli: ""                            # explicit path (else $ALLURE_BIN, else PATH)
```
Per run, without editing the file: `behave -D allure=true` / `-D allure=false`, `python run_tests.py --allure` / `--no-allure` (these override the file).

Install: `pip install allure-behave` (adapter) and the **Allure CLI + Java** to render HTML (https://allurereport.org/docs/install/).
Without the CLI the raw results are still written; render later:
```bash
allure serve reports/allure-results                # temporary web server (opens a browser)
allure generate reports/allure-results -o reports/allure-report --clean && allure open reports/allure-report
```
(Allure reports need an HTTP server - opening `index.html` from disk shows a blank page; `allure open` / `serve` handle that.)

### What appears in Allure
* **Behaviors / Suites / Timeline**: features, scenarios and steps (from `allure-behave`); behave tags become labels.
* **Environment widget**: PyAssure version, browser, `--env` label.
* **Attachments** per scenario: every **checkpoint** (name, expected, actual, evidence rows), **API calls** as request/response JSON, **screenshots** on UI failure,
  **trace.zip / video** when `trace_on_failure` / `record_video` are on.
* **Parallel runs**: all workers write to one shared `runs/<ts>/allure-results/`, so one report covers the whole run.

### How it is wired (so you can trust it and debug it)
`allure-behave` provides a behave *formatter*, but behave creates formatters before `before_all`, so a config switch cannot add one through `behave.ini`.
`pyassure.reporting.allure_support.activate()` therefore builds an `AllureFormatter` and appends it to the running behave runner in `before_all`
(`context._runner.formatters`). If a future behave version changes that, PyAssure prints a warning and all other reporting keeps working; you can then fall back to
the stock formatter: `behave -f allure_behave.formatter:AllureFormatter -o reports/allure-results`. Attachments go through no-op-safe helpers, so tests never depend on Allure.
Verified with behave 1.3, allure-behave 2.16, Allure CLI 2.32.

## 8.3 Choosing reports
* Day-to-day / sharing a failure: the **standalone HTML** (single file, evidence inline).
* Trends, history, categories, dashboards: **Allure** (keep `allure-results/history` between runs for trends).
* CI test tabs: **JUnit**. Programmatic use: **JSON**.

## 8.4 Customising
Name/tagline/version: `pyassure/branding.py`. HTML look: `_CSS` in `pyassure/reporting/html.py`. Report title/environment: `-D report_title=... -D env=QA` or `--title/--env`.
