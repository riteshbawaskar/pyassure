# 2. Getting started

## Install
```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[ui,allure]"        # core + Playwright + Allure adapter   (add: snowflake | oracle | databricks | dev)
playwright install chromium          # browser binaries (UI tests only; CI: playwright install --with-deps chromium)
```
`pip install -e .` matters: `behave.ini` references a PyAssure class (the branded BDD formatter) that behave must import at
start-up. Without installing, use `python -m behave` from the project root.

## Run the demo (no credentials, no external services)
```bash
python app/data/generate_demo_data.py   # (re)creates the demo SQLite DB + Excel files
behave                                   # db + api demo scenarios; the demo app starts automatically
behave --tags=ui                         # UI scenarios (page objects + sessions) - needs playwright browsers
python run_tests.py -w 4 -t "demo,demo_fail"   # everything in parallel, merged report
pytest -q                                # framework self-tests
```
Open `reports/validation_report.html` (standalone) and `reports/behave_report.html` (standard BDD).
`@demo_fail` scenarios fail **on purpose** to show failures with evidence and screenshots.

## Point it at YOUR application (checklist)
1. `app/config/pyassure.yml`: set `ui.base_url`, `api.base_url`; delete the `web_server:` block (or point it at your app's start command).
2. `app/auth.py`: write one `@session_provider` per kind of user (see [chapter 5](05-ui-testing-pom-and-sessions.md)).
3. `app/pages/`: one page object per page you test. `app/api/`: one client per API area.
4. `app/config/connections.yml`: your databases, secrets as `${ENV}` or `ENC(...)`.
5. Write `.feature` files and, where needed, business steps in `app/features/steps/app_steps.py`.
6. Delete `app/demo_app/`, the demo features and `app/data/*` when you no longer need them.

## Your first tests
```gherkin
@ui
Scenario: Admin sees the customer grid
  Given I am logged in as "admin"                  # storageState - no UI login
  When I open the customers page                   # your step -> CustomersPage.open()
  Then the customers grid should show 30 rows

@api
Scenario: Customers API requires login
  Given the API is anonymous
  When I send a GET request to "/api/customers"
  Then the response status should be 401

@db
Scenario: Database equals the UI grid
  Given the compare config "app/datasets/compare/customers_db_vs_ui.yml"
  When I run the data comparison
  Then the data comparison should pass
```

## Environment variables
| Variable | Purpose |
|---|---|
| `SNOWFLAKE_*`, `ORACLE_*`, `DATABRICKS_*` | referenced as `${...}` in `connections.yml` |
| `PYASSURE_CONFIG`, `PYASSURE_CONNECTIONS` | alternative settings / connections files (per environment) |
| `PYASSURE_KEY`, `PYASSURE_KEY_FILE` | master key for `ENC(...)` secrets |
| `PYASSURE_BROWSER`, `PYASSURE_HEADLESS` | set from `-D browser=...` / `-D headless=...` |
| `ALLURE_BIN` | path to the Allure CLI (else found on `PATH`) |
