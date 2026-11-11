"""Session providers: HOW to log in to this application (used for Playwright storageState).

Each function runs ONCE per session name (result cached for `sessions.ttl_minutes`, shared by every
scenario and parallel worker). Names are what scenarios use:  Given I am logged in as "admin".

Credentials: read them from the environment; values may be ENC(...) encrypted
(`python -m pyassure.secrets encrypt`), `decrypt_value` returns plain text unchanged.
"""
import os

from pyassure.secrets import decrypt_value
from pyassure.web.runtime import resolve_url, session_provider

ADMIN_USER = os.environ.get("APP_ADMIN_USER", "admin")


def _admin_password() -> str:
    return decrypt_value(os.environ.get("APP_ADMIN_PASSWORD", "admin123"))


@session_provider("admin", kind="api")           # fastest: log in over HTTP, no browser launched
def admin(request):
    """`request` is a Playwright APIRequestContext. The session cookie it receives is saved and works
    for BOTH browser pages and API calls."""
    r = request.post(resolve_url("/api/login"), data={"username": ADMIN_USER, "password": _admin_password()})
    assert r.ok, f"admin login failed: HTTP {r.status}"


@session_provider("admin_ui")                    # browser kind: log in through the real UI with the page object
def admin_ui(context):
    from app.pages.customers_page import CustomersPage
    from app.pages.login_page import LoginPage

    page = context.new_page()
    login = LoginPage(page).open()
    login.login(ADMIN_USER, _admin_password())
    CustomersPage(page).wait_ready()             # proves the login worked before the state is saved
