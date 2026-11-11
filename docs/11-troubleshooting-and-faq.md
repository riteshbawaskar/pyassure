# 11. Troubleshooting and FAQ

| Symptom | Cause / fix |
|---|---|
| `behave: error: BAD_FORMAT=html (problem: ModuleNotFoundError)` | `pip install -e .` (or run `python -m behave` from the project root) |
| `Playwright missing` / *Executable doesn't exist* | `pip install playwright && playwright install chromium` |
| `No @session_provider registered for session 'x'` | define it in `app/auth.py` and set `sessions.providers: app.auth` |
| Scenario starts logged out / redirected to login | wrong/expired session: delete `.pyassure/sessions/<name>.json`; check the provider's cookie domain matches `ui.base_url` |
| `Timed out waiting for session lock` | a crashed worker left `*.lock` in `.pyassure/sessions/`; delete it |
| *Playwright Sync API inside the asyncio loop* | a second driver was started; always use `pyassure.web.runtime.runtime()` (steps/connectors already do) |
| `web_server did not answer at ...` | fix `web_server.command/url`, or start the app yourself and remove the block |
| Allure folder empty | `reports.allure.enabled` false (or `-D allure=false`); `pip install allure-behave` |
| Allure HTML blank | open via `allure open <dir>` / `allure serve` - it cannot run from `file://` |
| `Allure results written, no HTML` | install the Allure CLI + Java, or set `reports.allure.cli` / `ALLURE_BIN` |
| `Connection 'x': environment variables not set` | export the `${VAR}` used in `connections.yml` |
| `No master key found` / `Could not decrypt value` | `python -m pyassure.secrets keygen` / set `PYASSURE_KEY` / wrong key - re-encrypt or `rotate` |
| `Column(s) [...] not found in target dataset` | typo, not selected by the query, or missing `column_mapping` (`target: source`) |
| Mismatches `10` vs `10.0`, `NULL` vs `''`, dates | defaults handle numbers and NULL/''; use `datetime_truncate: s`/`D` for dates; `read_as_text: true` to keep Excel leading zeros |
| `No scenarios selected` (runner) | tags matched nothing; repeated `-t` are AND-ed - use commas for OR |
| Only the first failure shows | behave stops at the first failing step; end with a composite step |
| Parallel run flaky | shared data between scenarios - isolate/clean up/filter ([6.5](06-api-testing.md)); lower `-w`; use `--retries` as a stopgap only |
| API/UI comparison off by extra rows | other scenarios' data: filter both sides identically |
| Failed UI assertion is slow | it waits `ui.timeout_ms`; lower it for fast feedback |

**What has been verified?** In this repository's demo (SQLite + Excel + a local web app in Chromium): comparison, validation, Excel cells, POM, storageState sessions
(API- and browser-kind), API testing, encrypted secrets, parallel runs (both schemes, sharding logic), standalone/BDD/JUnit reports and **Allure** (results + a real generated
report), plus 22 pytest tests. Snowflake / Oracle / Databricks use each vendor's standard DB-API driver through the same code path but need your credentials to exercise -
run the `@requires_creds` scenarios against a non-production account first.

**Does it modify data?** The database/file layers only read. API/UI scenarios do whatever you script - create and delete your own test data.
