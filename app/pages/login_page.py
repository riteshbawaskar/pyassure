"""Page object: the sign-in page.  Selectors live here ONLY - when the UI changes, fix this file."""
from pyassure.web.pages import BasePage


class LoginPage(BasePage):
    path = "/login.html"                 # appended to ui.base_url
    ready = "#login-btn"                 # visible when the page has loaded

    USERNAME, PASSWORD, SUBMIT, ERROR = "#username", "#password", "#login-btn", "#login-error"

    def login(self, username: str, password: str) -> None:
        """Fill the form and submit. (Does not assert - callers/steps decide what is expected next.)"""
        self.fill(self.USERNAME, username)
        self.fill(self.PASSWORD, password)
        self.click(self.SUBMIT)

    def error_text(self) -> str:
        self.page.wait_for_selector(self.ERROR, state="visible")
        return self.text(self.ERROR)
