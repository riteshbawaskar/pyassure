"""APPLICATION-specific steps. They speak business language and delegate to page objects / API clients,
so scenarios never contain selectors or URLs.  Add your own steps here."""
from behave import then, when

from app.api.customers_api import CustomersApi
from app.pages.customers_page import CustomersPage
from app.pages.login_page import LoginPage
from pyassure.bdd import checkpoint
from pyassure.web.api import api_client
from pyassure.web.pages import page_object


# ------------------------------------------------------------------------------ UI (page objects)
@when("I open the sign-in page")
def step_open_login(context):
    page_object(context, LoginPage).open()


@when('I sign in as "{user}" with password "{password}"')
def step_sign_in(context, user, password):
    page_object(context, LoginPage).login(user, password)


@then('the sign-in error should be "{text}"')
def step_login_error(context, text):
    actual = page_object(context, LoginPage).error_text()
    checkpoint(context, "Sign-in error message", actual == text, text, actual)


@then("I should land on the customers page")
def step_on_customers(context):
    page = page_object(context, CustomersPage).wait_ready()
    checkpoint(context, "Landed on customers page", page.url.split("/")[-1] in page.page.url, page.path, page.page.url)


@when("I open the customers page")
def step_open_customers(context):
    page_object(context, CustomersPage).open()


@when('I search customers for "{text}"')
def step_search(context, text):
    page_object(context, CustomersPage).search(text)


@then('the result summary should read "{text}"')
def step_summary(context, text):
    page = page_object(context, CustomersPage)
    page.page.wait_for_function("t => document.querySelector('#result-count').textContent === t", arg=text)
    checkpoint(context, "Result summary", page.summary() == text, text, page.summary())


@then("the customers grid should show {count:d} rows")
def step_rows(context, count):
    actual = page_object(context, CustomersPage).visible_rows()
    checkpoint(context, "Rows in grid", actual == count, count, actual)


# ----------------------------------------------------------------------------- API (typed client)
@when('I create a customer named "{name}" through the API')
def step_create_customer(context, name):
    api = api_client(context, CustomersApi, session="admin")
    resp = api.create(name=name, email=f"{name.lower()}@example.com", country="IN", credit_limit=1234.5)
    context.vars = {**getattr(context, "vars", {}), "customer_id": resp.json().get("id")}
    context.api = api                                       # so generic "the response ..." steps use it


@then("the new customer should be retrievable through the API")
def step_retrievable(context):
    api = api_client(context, CustomersApi, session="admin")
    got = api.get_one(context.vars["customer_id"])
    checkpoint(context, "New customer is retrievable", got.status == 200, 200, got.status)
    context.api = api


@then("the new customer should be removed through the API")
def step_remove(context):
    """Clean up what the scenario created, so other scenarios (and parallel workers) see untouched data."""
    api = api_client(context, CustomersApi, session="admin")
    gone = api.remove(context.vars["customer_id"])
    checkpoint(context, "New customer removed", gone.status == 204, 204, gone.status)
