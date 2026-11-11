"""Reusable, application-agnostic UI steps (Playwright). App-specific steps belong in
app/features/steps/ and should use PAGE OBJECTS (app/pages/) instead of raw selectors.

Selectors are Playwright selectors: CSS (#id, .cls), text (text=Save), role (role=button[name='Save']).
A browser starts lazily on the first UI step. Assertions use Playwright's auto-retrying `expect` (they wait
up to ui.timeout_ms) and every one is recorded as a checkpoint; a screenshot is added automatically on failure.

Sessions (storageState):  Given I am logged in as "admin"
  starts the scenario's browser ALREADY authenticated using the session provider registered for "admin"
  (app/auth.py); the login runs once, is cached and shared by all scenarios and parallel workers.
"""
from behave import given, then, when

from pyassure.bdd import checkpoint
from pyassure.config import ConfigError
from pyassure.data.connectors import fetch_dataset
from pyassure.reporting.store import FAILED
from pyassure.web.runtime import get_page, resolve_url


def _expect():
    """Playwright's `expect`, imported lazily so non-UI users don't need Playwright."""
    try:
        from playwright.sync_api import expect
    except ImportError as exc:
        raise ConfigError("Playwright missing: pip install playwright && playwright install chromium") from exc
    return expect


def _assert_ui(context, name, expected, check):
    """Run an expect() call and record it as a checkpoint (Playwright's message explains a failure)."""
    try:
        check()
    except Exception as exc:                                   # AssertionError, timeouts, closed page ...
        checkpoint(context, name, False, expected, type(exc).__name__,
                   str(exc).strip().splitlines()[0][:300] if str(exc).strip() else "")
    else:
        checkpoint(context, name, True, expected, "as expected")


# ------------------------------------------------------------------ session + navigation
@given('I am logged in as "{session}"')
def step_login_session(context, session):
    get_page(context, session=session)                         # creates the page from the cached storageState
    context.store.info("Session", session)


@given('I open the page "{url}"')
def step_open(context, url):
    target = resolve_url(url)
    get_page(context).goto(target)
    context.store.info("Page", target)


# ------------------------------------------------------------------------------ actions
@when('I click "{selector}"')
def step_click(context, selector):
    get_page(context).click(selector)


@when('I fill "{selector}" with "{value}"')
def step_fill(context, selector, value):
    get_page(context).fill(selector, value)


@when('I select "{option}" from "{selector}"')
def step_select(context, option, selector):
    get_page(context).select_option(selector, label=option)


@when('I wait for "{selector}" to be visible')
def step_wait(context, selector):
    get_page(context).wait_for_selector(selector, state="visible")


@when('I take a screenshot named "{name}"')
def step_screenshot(context, name):
    context.store.attach_image(name, get_page(context).screenshot(full_page=True))


# --------------------------------------------------------------------------- assertions
@then('the page title should be "{title}"')
def step_title(context, title):
    _assert_ui(context, "Page title", title, lambda: _expect()(get_page(context)).to_have_title(title))


@then('the element "{selector}" should be visible')
def step_visible(context, selector):
    _assert_ui(context, f"{selector} is visible", "visible",
               lambda: _expect()(get_page(context).locator(selector).first).to_be_visible())


@then('the element "{selector}" should contain the text "{text}"')
def step_contains(context, selector, text):
    _assert_ui(context, f"{selector} contains text", text,
               lambda: _expect()(get_page(context).locator(selector).first).to_contain_text(text))


@then('the element "{selector}" should have the value "{value}"')
def step_value(context, selector, value):
    _assert_ui(context, f"{selector} has value", value,
               lambda: _expect()(get_page(context).locator(selector).first).to_have_value(value))


@then('there should be {count:d} elements matching "{selector}"')
def step_count(context, count, selector):
    _assert_ui(context, f"{selector} count", str(count),
               lambda: _expect()(get_page(context).locator(selector)).to_have_count(count))


@then('the text of "{selector}" should equal the value returned by "{query}" on connection "{connection}"')
def step_text_equals_query(context, selector, query, connection):
    """One on-screen value vs the first cell of a query; numbers compare numerically ('1,234.5' == 1234.5)."""
    ui = get_page(context).locator(selector).first.inner_text().strip()
    db = str(fetch_dataset({"connection": connection, "query": query}).iloc[0, 0]).strip()

    def num(v):
        try:
            return float(v.replace(",", ""))
        except ValueError:
            return v
    checkpoint(context, f"UI value of {selector} equals database value", num(ui) == num(db),
               f"{db} (database)", f"{ui} (UI)")
