# PyAssure

**A Quality Assurance framework in Python for UI, API and Database Testing.**

BDD scenarios (behave) + Playwright for **UI** (Page Object Model, storageState sessions) and **API** testing, plus **database/file** comparison and
validation across Snowflake, Oracle, Databricks, Excel and SQLite - with parallel execution, encrypted secrets, a compact standalone HTML report,
the standard BDD report and optional **Allure**.

```gherkin
Scenario: The grid shows what the database holds
  Given the compare config "app/datasets/compare/customers_db_vs_ui.yml"
  When I run the data comparison
  Then the data comparison should pass
```

## Two folders, one rule
```
pyassure/   FRAMEWORK   generic and reusable - you should never need to edit it
app/        APPLICATION page objects, API clients, login/sessions, scenarios, datasets, config - the ONLY code a project team changes
```

## Capabilities
| | |
|---|---|
| **UI** | Page Object Model (`BasePage`, `Component`, `TableComponent`), auto screenshots, optional trace/video, UI grid <-> DB comparison |
| **Sessions** | Playwright **storageState**: log in once (API or UI), reuse across scenarios and workers; TTL, file lock, runner warm-up |
| **API** | Playwright `APIRequestContext` client, typed clients, JSON assertions, request chaining, API <-> DB comparison; no browser needed |
| **Database / files** | PK-based compare (single/composite, `all` or listed fields, mapping, tolerance, NULL/case/trim rules), 12 validation checks, Excel cell-to-cell |
| **Parallel** | `python run_tests.py -w 4 [--scheme scenario] [--shard 2/3] [--retries 1]`, shared sessions + app server, merged reports |
| **Reports** | standalone HTML (+JSON) - standard BDD HTML - JUnit - **Allure** (switch in `app/config/pyassure.yml`) |
| **Security** | `password: ENC(...)` decrypted in memory only at connect time; keygen / encrypt / rotate CLI |

## Quick start
```bash
pip install -e ".[ui,allure]" && playwright install chromium
behave                                  # db + api demo scenarios (the demo app starts itself)
behave --tags=ui                        # UI scenarios (page objects + sessions)
python run_tests.py -w 4 --allure       # parallel run, merged report, Allure
pytest -q                               # framework self-tests
```
Reports: `reports/validation_report.html`, `reports/behave_report.html`, `reports/allure-report/` (needs the Allure CLI + Java; raw results are always written).

## Turn Allure on or off
```yaml
# app/config/pyassure.yml
reports: {allure: {enabled: true}}      # or per run:  behave -D allure=true   |   run_tests.py --allure / --no-allure
```

## Documentation
| | |
|---|---|
| [1. Overview and architecture](docs/01-overview-and-architecture.md) | the framework/app contract, data flow, module map |
| [2. Getting started](docs/02-getting-started.md) | install, demo, pointing it at your application |
| [3. Configuration reference](docs/03-configuration-reference.md) | every YAML key |
| [4. Writing scenarios](docs/04-writing-scenarios.md) | all steps, tags, custom steps |
| [5. UI testing: POM and sessions](docs/05-ui-testing-pom-and-sessions.md) | page objects, storageState |
| [6. API testing](docs/06-api-testing.md) | Playwright API client, typed clients, chaining |
| [7. Parallel execution](docs/07-parallel-execution.md) | runner, Playwright guidance, sharding, CI |
| [8. Reports and Allure](docs/08-reports-and-allure.md) | four report types, Allure switch |
| [9. Security and encryption](docs/09-security-and-encryption.md) | master key, `ENC()`, threat model |
| [10. Extending](docs/10-extending-the-framework.md) | databases, checks, steps, config |
| [11. Troubleshooting and FAQ](docs/11-troubleshooting-and-faq.md) | errors and what is verified |
