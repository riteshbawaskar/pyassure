# 5. UI testing: Page Object Model and storageState sessions

## 5.1 Page Object Model (POM)

A **page object** is the only place that knows a page's selectors and what a user can do on it. Scenarios and steps talk to page objects, so a UI change is
fixed in **one class** instead of every test. Base classes live in `pyassure/web/pages.py`; yours live in `app/pages/`.

```python
# app/pages/customers_page.py
from pyassure.web.pages import BasePage, TableComponent

class CustomersPage(BasePage):
    path = "/customers.html"                 # appended to ui.base_url
    ready = "#customers tbody tr"            # visible when the page has loaded (open() waits for it)

    FILTER, SEARCH, SUMMARY, GRID = "#name-filter", "#search", "#result-count", "#customers"   # selectors in ONE place

    def search(self, text):                  # an ACTION (returns self so calls chain)
        self.fill(self.FILTER, text); self.click(self.SEARCH); return self

    def summary(self):                       # a QUERY (returns data; assertions stay in steps)
        return self.text(self.SUMMARY)

    def table(self) -> TableComponent:       # reusable widget -> .to_dataframe() for UI-vs-database compares
        return TableComponent(self.loc(self.GRID))
```

| Class / function | Purpose |
|---|---|
| `BasePage` | `path`, `ready`, `open()`, `wait_ready()`, helpers `loc/click/fill/select/text/is_visible/count/title` |
| `Component` | reusable widget scoped to a root locator (nav bar, modal ...) |
| `TableComponent` | an HTML table as data: `to_dataframe()`, `row_count()` |
| `page_object(context, Cls, session=None)` | instance of `Cls` on the scenario's current page (cached per scenario) |

**Rules of thumb:** selectors only inside page objects - actions return data or the next page object - assertions in steps via `checkpoint()` -
one page object per page/major component - wait for `ready`, never `sleep`.

Use them from `app/features/steps/app_steps.py`:
```python
@when('I sign in as "{user}" with password "{password}"')
def step(context, user, password):
    page_object(context, LoginPage).login(user, password)
```

## 5.2 Sessions with storageState

Logging in through the UI before every scenario is slow and flaky. PyAssure logs in **once**, saves cookies/localStorage with Playwright's
`storage_state`, and starts later scenarios **already authenticated**.

**1. Describe how to log in** (`app/auth.py`):
```python
from pyassure.web.runtime import session_provider, resolve_url

@session_provider("admin", kind="api")            # kind="api": HTTP login, NO browser launched (fastest)
def admin(request):                               # request = Playwright APIRequestContext
    r = request.post(resolve_url("/api/login"), data={"username": "admin", "password": "..."})
    assert r.ok

@session_provider("admin_ui")                     # kind="browser" (default): log in through the real UI
def admin_ui(context):                            # context = fresh Playwright BrowserContext
    page = context.new_page()
    LoginPage(page).open().login("admin", "...")
    CustomersPage(page).wait_ready()              # prove it worked before the state is saved
```
**2. Enable it** (`app/config/pyassure.yml`): `sessions: {providers: app.auth, ttl_minutes: 60, warmup: [admin]}`.

**3. Use it:**
```gherkin
Given I am logged in as "admin"                   # UI: browser starts authenticated
Given the API is authenticated as "admin"         # API: same cookies
```
or in data configs: `web_table: {session: admin}`, `api: {session: admin}`.

| Facts | |
|---|---|
| Cache | `<sessions.dir>/<name>.json`, reused until `ttl_minutes` expires (then the provider runs again) |
| Parallel safety | creation is guarded by a cross-process **file lock** + atomic write: workers never log in twice or read half-written files |
| Warm-up | `warmup:` sessions are created by the runner before workers start (see [chapter 7](07-parallel-execution.md)) |
| Switching users | `Given I am logged in as "other"` after a page is open re-creates the page; log in **first** |
| API-kind sessions | the cookies work for browser pages too, so one fast API login can serve UI and API tests |
| Credentials | read from env in `auth.py`; values may be `ENC(...)` - wrap with `decrypt_value()` |
| Force a re-login | delete `.pyassure/sessions/<name>.json` (or `runtime.invalidate("admin")`) |
| Token-in-localStorage apps | browser-kind providers capture localStorage too (storageState includes it) |

## 5.3 Failure evidence and artifacts
On a failed step: full-page **screenshot** (HTML report + Allure). Optional per `ui:` settings: `trace_on_failure` (open with
`playwright show-trace x_trace.zip`) and `record_video`. Kept files go to `<report-dir>/ui_artifacts/`.

## 5.4 Assertions
Generic steps use Playwright's auto-retrying `expect` (waits up to `ui.timeout_ms`). In your own steps use `playwright.sync_api.expect(locator)`
or read data from the page object and call `checkpoint()`.

## 5.5 Selectors
Playwright selectors: CSS (`#id`, `.cls`), text (`text=Save`), role (`role=button[name='Save']`), test ids (`[data-testid=submit]`).
Prefer stable attributes (`data-testid`, ids, roles) over layout-dependent CSS.

## 5.6 Compare the UI with the database
```yaml
target:
  type: web_table
  url: /customers.html
  selector: "#customers"
  session: admin
  actions:
    - {wait_for: "#customers tbody tr"}
    - {fill: "#name-filter", value: "Customer"}
    - {click: "#search"}
```
Cells are read as text (whitespace collapsed, nested tables ignored); pagination is not followed - filter or compare one page. Filter both sides the same way so
other tests' data cannot disturb the comparison.
