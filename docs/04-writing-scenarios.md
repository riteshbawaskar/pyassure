# 4. Writing scenarios (step reference)

Scenarios live in `app/features/`. Framework steps are enabled in `app/features/steps/framework_steps.py`; your own go in `app_steps.py`.

## Tags
| tag | meaning | default |
|---|---|---|
| `@requires_creds` | needs Snowflake/Oracle/Databricks access | skipped |
| `@requires_browser` | needs Playwright browsers (UI) | skipped |
| `@demo_fail` | fails on purpose | skipped |
| `@ui @api @db @excel @snowflake ...` | free-form filters | run |

`behave --tags=ui` **replaces** the default exclusions. Repeated `--tags` are AND-ed; commas inside one are OR-ed.
behave stops at a scenario's **first failing step**; finish with a composite step (`the data comparison should pass`,
`all validations should pass`) when you want every problem recorded in one run.

## Database / file steps (`pyassure.steps.data_steps`)
| Step | |
|---|---|
| `Given the compare config "path"` / `When I run the data comparison` | load YAML, fetch both sides, compare |
| `the source and target row counts should match` | |
| `there should be no records missing in the target` / `...in the source` | |
| `there should be no duplicate primary keys in the source` / `...in the target` | |
| `the source and target should have the same columns` | |
| `there should be no field mismatches` / `the field "X" should have no mismatches` | |
| `the mismatch percentage should be at most N` | |
| `the data comparison should pass` | all of the above, all recorded |
| `Given the validation config "path"` / `When I run the data validations` | |
| `all validations should pass` / `all "not_null" validations should pass` / `the validation "name" should pass` | |
| `Given the excel compare config "path"` / `When I run the excel cell comparison` | |
| `both workbooks should contain the same sheets` / `there should be no cell differences` | |
| `the sheet "S" should have no cell differences` / `the cell "B2" in sheet "S" should match` | |

## UI steps (`pyassure.steps.ui_steps`) - generic, selector based
`Given I am logged in as "session"` - `Given I open the page "url"` - `When I click|fill|select ...` -
`When I wait for "sel" to be visible` - `When I take a screenshot named "x"` - `Then the page title should be "x"` -
`the element "sel" should be visible | contain the text "x" | have the value "x"` - `there should be N elements matching "sel"` -
`the text of "sel" should equal the value returned by "SQL" on connection "name"`.
Prefer **page-object steps in `app_steps.py`** for real applications ([chapter 5](05-ui-testing-pom-and-sessions.md)).

## API steps (`pyassure.steps.api_steps`) - see [chapter 6](06-api-testing.md)
`Given the API is authenticated as "session"` / `the API is anonymous` - `When I send a GET request to "/path"` -
`When I send a POST request to "/path" with JSON:` (docstring body) - `When I save the response JSON field "id" as "name"` (reuse as `{{name}}`) -
`Then the response status should be 200` - `the response JSON field "a.b[0]" should equal "x" | should exist` -
`the response JSON list "items" should have N items` - `the response header "content-type" should contain "json"` -
`the response time should be under N ms`.

## Data-driven scenarios
```gherkin
Scenario Outline: Every migrated table matches
  Given the compare config "<config>"
  When I run the data comparison
  Then the data comparison should pass
  Examples:
    | config                                    |
    | app/datasets/compare/customers.yml        |
    | app/datasets/compare/orders.yml           |
```

## Custom steps
```python
# app/features/steps/app_steps.py
from behave import then, when
from pyassure.bdd import checkpoint
from pyassure.web.pages import page_object

@when('I search customers for "{text}"')
def step(context, text):
    page_object(context, CustomersPage).search(text)         # business language -> page object

@then("the source should have at least {n:d} rows")
def step(context, n):
    checkpoint(context, f"Source >= {n} rows", context.result.source_count >= n, f">= {n}", context.result.source_count)
```
`checkpoint(context, name, passed, expected, actual, message="", tables=None, severity="error", raise_on_fail=True)` records the result
(HTML + Allure) and raises on failure. `severity="warning"` never fails; `raise_on_fail=False` records several checkpoints before failing once.
Add report facts with `context.store.info("Key", value)` and evidence with `tables=[Table.from_df("Title", df)]`.
