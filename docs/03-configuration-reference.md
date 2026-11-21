# 3. Configuration reference

All YAML shares two conventions: **relative paths resolve from the project root**, and **`${VAR}` / `${VAR:default}`** come
from environment variables. Set `PYASSURE_ROOT` to an absolute path to override the root derived from `__file__`
(useful when the project is accessed through a mapped network drive). Set `PYASSURE_APP_DIR` to select a different
application folder; absolute paths are used as-is, while relative paths resolve from `PYASSURE_ROOT` (or the default project
root). The selected folder must contain `config/pyassure.yml` and `config/connections.yml`. Unknown *option* names raise an error, so typos are caught.

## 3.1 `app/config/pyassure.yml` - behaviour switches

Only list what differs from the defaults (defaults live in `pyassure/config.py` -> `DEFAULTS`; sections are deep-merged).

```yaml
ui:
  base_url: http://localhost:8080      # page-object paths and relative URLs are appended to this
  browser: chromium                    # chromium | firefox | webkit        (per run: -D browser=firefox)
  headless: true                       #                                   (per run: -D headless=false)
  timeout_ms: 5000                     # default wait for elements/navigation (and how long a failing assertion waits)
  viewport: {width: 1366, height: 768}
  slow_mo_ms: 0
  screenshot_on_failure: true          # full-page PNG in the HTML report (and Allure)
  trace_on_failure: false              # keep reports/.../ui_artifacts/<scenario>_trace.zip   (playwright show-trace)
  record_video: false                  # keep a .webm for failed scenarios
api:
  base_url: ""                         # falls back to ui.base_url
  timeout_ms: 10000
  headers: {}                          # sent with every API call
  ignore_https_errors: false
sessions:                              # Playwright storageState
  providers: app.auth                  # module with the @session_provider functions
  dir: .pyassure/sessions              # cached state files
  ttl_minutes: 60                      # re-login when older
  warmup: [admin]                      # parallel runner creates these ONCE before workers start
reports:
  standalone_html: true                # PyAssure HTML + JSON
  allure:
    enabled: false                     # <- Allure ON/OFF   (per run: -D allure=true|false, run_tests.py --allure/--no-allure)
    results_dir: reports/allure-results
    report_dir: reports/allure-report
    generate: true                     # runner builds the HTML when the Allure CLI is found
    cli: ""                            # explicit path to `allure` (else $ALLURE_BIN, else PATH)
parallel: {workers: auto, scheme: feature, retries: 0}     # defaults for run_tests.py
web_server:                            # start the app under test if the url is not already answering
  command: "{python} app/demo_app/server.py"
  url: http://127.0.0.1:8765/api/health
  startup_timeout_s: 30
```

## 3.2 `app/config/connections.yml`
```yaml
connections:
  <name>:
    type: snowflake | oracle | databricks | sqlite
```
| type | keys |
|---|---|
| `snowflake` | `account, user, password, warehouse, database, schema, role`; optional `authenticator`, `private_key_path` + `private_key_passphrase`, `session_parameters`, `use_arrow` |
| `oracle` | `user, password` + (`host, port, service_name` or `dsn`); optional `thick_mode`, `lib_dir` |
| `databricks` | `server_hostname, http_path, access_token`; optional `catalog, schema` |
| `sqlite` | `path` |

Secrets: `${ENV}`, `ENC(...)` ([chapter 9](09-security-and-encryption.md)) or plain text (demos only). Missing env vars are reported as
*"Connection 'x': environment variables not set for [...]"*.

## 3.3 Endpoints (`source:` / `target:`; top level of validation configs)
```yaml
connection: snowflake_dev      # name from connections.yml ... or inline  type: excel, path: ...
query: SELECT ...              # or  query_file: sql/x.sql   or   table: SCHEMA.TABLE
schema: QA                     # any other key overrides the named connection's setting
```
Precedence: **named connection < inline keys**. A trailing `;` is removed (Oracle).
Snowflake `query` and `query_file` values may contain semicolon-separated statements (for example, `SET` followed by a `WITH` query); all statements run, and the final result set is returned.

| inline `type` | keys |
|---|---|
| `excel` | `path`, optional `sheet`, `header_row` (1-based), `read_as_text`; each sheet is a SQL table: `SELECT * FROM "Sheet 1" WHERE ...` |
| `web_table` | `url`, `selector` (default `table`), `header`, `session` (storageState), `actions` (`fill`/`click`/`wait_for`), `wait_for_rows` |
| `api` | `path`, `method` (GET), `params`, `body`, `headers`, `json_path` (where the list is; `''` = whole body), `session`, `base_url` |

`web_table` and `api` values can contain `ENC(...)`. Rows from `api` are flattened with `pandas.json_normalize`.

## 3.4 Compare config (`app/datasets/compare/*.yml`)
```yaml
name: Customers - Oracle vs Snowflake
source: {...endpoint...}
target: {...endpoint...}
primary_key: [CUSTOMER_ID]             # one or more columns
compare_fields: all                    # 'all' = columns on BOTH sides, or an explicit list
exclude_fields: [LOAD_TS]              # with 'all'
column_mapping: {id: CUSTOMER_ID}      # target_column: source_column
options: {...}
```
| option | default | meaning |
|---|---|---|
| `case_insensitive_columns` | true | match column **names** ignoring case |
| `trim_strings` | true | strip whitespace before comparing text |
| `ignore_case` | false | compare text **values** ignoring case |
| `null_equals_empty` | true | NULL equals `''` (Oracle stores `''` as NULL) |
| `numeric_tolerance` | 0.0 | absolute tolerance |
| `coerce_numeric` | true | `"10"`, `10`, `10.0`, `Decimal(10)` equal (needs *every* value numeric) |
| `datetime_truncate` | none | `s` / `D` to drop precision |
| `max_report_rows` | 200 | rows per evidence table |

Rules: key parts are normalised (`101` = `101.0` = `"101"` = `" 101 "`); duplicate keys are reported and only the first is compared; per field
both-empty = equal, one-empty = different, else compared as datetime -> number -> text (a stage is used only if *all* values convert).

## 3.5 Validation config (`app/datasets/validation/*.yml`)
```yaml
name: ORDERS sanity checks
connection: snowflake_dev              # the top level IS an endpoint
table: SALES.ORDERS
checks:
  - {type: not_null, columns: [ORDER_ID, AMOUNT]}
```
All checks accept `name`, `severity: error|warning` (warnings never fail the scenario), `message`; `column(s)` takes a name, list or `all`.

| type | parameters | fails when |
|---|---|---|
| `row_count` | `min`, `max`, `equals` | outside bounds |
| `columns_exist` | `columns` | a column is missing |
| `not_null` | `columns`, `treat_empty_as_null` | NULL / blank |
| `unique` | `columns` (composite) | duplicates |
| `numeric` | `columns`, `integer: true` | non-numeric / non-integer |
| `date` | `columns`, `format`, `min`, `max` | unparsable / out of range |
| `range` | `column(s)`, `min`, `max` | non-numeric / outside |
| `allowed_values` | `column(s)`, `values`, `case_sensitive` | not in list |
| `regex` | `column(s)`, `pattern`, `full_match` | no match |
| `length` | `column(s)`, `min`, `max` | length outside |
| `expression` | `expression` (pandas, e.g. `SHIP >= ORDER`) | any row violates |
| `sql` | `query`, `expect` or `min`/`max`, `connection` | first cell differs from `expect`; `min`/`max` require a numeric result. `expect` supports text or numeric values |
| `sql_report` | `query`, `connection`, optional `name` | query fails to execute; otherwise the query and returned rows are attached to the HTML report (opened by default) |

NULLs are ignored by all but `not_null`. A check that cannot run is **reported as a failed checkpoint** with the error.

## 3.6 Excel cell compare (`app/datasets/cell_compare/*.yml`)
```yaml
source: {path: app/data/v1.xlsx}
target: {path: app/data/v2.xlsx}
sheets: all                            # or [Summary] or [{source: Q1, target: Q1_new}]
options: {trim_strings: true, ignore_case: false, empty_equals_none: true, numeric_tolerance: 0.0,
          compare_formulas: false, range: A1:H200, ignore_ranges: [F1:F200], max_diffs_stored: 5000, max_report_rows: 200}
```

## 3.7 `behave.ini` (rarely edited)
`paths`, `format`/`outfiles` (branded BDD HTML + console), `default_tags` (`-@requires_creds -@requires_browser -@demo_fail`),
`junit`, and the `html` formatter registration. `--tags` on the command line **replaces** `default_tags`.

## 3.8 behave user data (`-D key=value`)
`report_dir`, `report_title`, `env`, `browser`, `headless`, `allure` (`true|false`), `allure_dir`, `worker` (set by the runner).
