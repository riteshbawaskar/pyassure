"""Reusable API steps built on Playwright's APIRequestContext (no browser is started).

    Given the API is authenticated as "admin"                  # storageState session (cookies) from app/auth.py
    When  I send a POST request to "/api/customers" with JSON:
          \"\"\"{"name": "Eve"}\"\"\"
    Then  the response status should be 201
    And   the response JSON field "name" should equal "Eve"
    When  I save the response JSON field "id" as "new_id"       # chain calls with {{new_id}}
    And   I send a DELETE request to "/api/customers/{{new_id}}"

`{{name}}` placeholders in paths and bodies are replaced from values saved earlier in the scenario.
Typed, app-specific clients (app/api/*.py) can be used from your own steps via `api_client(context, MyApi)`.
Compare an API response with a database by using an `api` source in a compare config.
"""
import json
import re

from behave import given, then, when

from pyassure.bdd import checkpoint
from pyassure.web.api import MISSING, api_client, json_path


def _client(context):
    """The scenario's current API client (anonymous unless an auth step ran)."""
    if getattr(context, "api", None) is None:
        context.api = api_client(context)
    return context.api


def _subst(context, text: str) -> str:
    """Replace {{var}} with values saved by 'I save the response JSON field ...'."""
    vars_ = getattr(context, "vars", {})
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(vars_.get(m.group(1), m.group(0))), text)


def _last(context):
    resp = _client(context).last
    assert resp is not None, "No API request has been sent yet in this scenario"
    return resp


# ------------------------------------------------------------------------------- setup
@given('the API is authenticated as "{session}"')
def step_api_auth(context, session):
    context.api = api_client(context, session=session)
    context.store.info("API session", session)


@given("the API is anonymous")
def step_api_anon(context):
    context.api = api_client(context)


# ------------------------------------------------------------------------------ requests
@when('I send a {method} request to "{path}"')
def step_send(context, method, path):
    _client(context).request(method, _subst(context, path))


@when('I send a {method} request to "{path}" with JSON:')
def step_send_json(context, method, path):
    _client(context).request(method, _subst(context, path), json=json.loads(_subst(context, context.text)))


@when('I save the response JSON field "{path}" as "{name}"')
def step_save(context, path, name):
    value = json_path(_last(context).json(), path)
    assert value is not MISSING, f"JSON field '{path}' not found in the response"
    context.vars = {**getattr(context, "vars", {}), name: value}


# ------------------------------------------------------------------------------ assertions
@then("the response status should be {code:d}")
def step_status(context, code):
    r = _last(context)
    checkpoint(context, f"{r.method} {r.url.split('//', 1)[-1].split('/', 1)[-1]} status", r.status == code,
               code, r.status, r.text[:200] if r.status != code else "")


@then('the response JSON field "{path}" should equal "{value}"')
def step_field_equals(context, path, value):
    actual = json_path(_last(context).json(), path)
    ok = actual is not MISSING and (str(actual) == value or str(actual).lower() == value.lower() and isinstance(actual, bool))
    checkpoint(context, f"JSON {path} equals", ok, value, "<missing>" if actual is MISSING else actual)


@then('the response JSON field "{path}" should exist')
def step_field_exists(context, path):
    found = json_path(_last(context).json(), path) is not MISSING
    checkpoint(context, f"JSON {path} exists", found, "present", "present" if found else "missing")


@then('the response JSON list "{path}" should have {count:d} items')
def step_list_count(context, path, count):
    """Use "$" as the path when the body itself is the list."""
    value = json_path(_last(context).json(), path)
    n = len(value) if isinstance(value, list) else "not a list"
    checkpoint(context, f"JSON list {path} size", n == count, count, n)


@then('the response header "{name}" should contain "{text}"')
def step_header(context, name, text):
    actual = {k.lower(): v for k, v in _last(context).headers.items()}.get(name.lower(), "")
    checkpoint(context, f"Header {name} contains", text.lower() in actual.lower(), text, actual or "<missing>")


@then("the response time should be under {ms:d} ms")
def step_time(context, ms):
    r = _last(context)
    checkpoint(context, "Response time", r.elapsed_ms < ms, f"< {ms} ms", f"{r.elapsed_ms:.0f} ms")
