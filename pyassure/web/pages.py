"""Page Object Model (POM) building blocks.

Philosophy
  A *page object* is the ONLY place that knows how a page is built (selectors) and what a user can do
  on it (methods). Scenarios and steps talk to page objects, never to raw selectors, so a UI change is
  fixed in ONE class instead of in every test.

  pyassure/   provides the base classes below (generic).
  app/pages/  holds YOUR page objects (application specific):

      class LoginPage(BasePage):
          path = "/login.html"                       # appended to ui.base_url
          ready = "#login-btn"                       # visible when the page has loaded
          USERNAME, PASSWORD, SUBMIT = "#username", "#password", "#login-btn"

          def login(self, user, pwd):                # an ACTION: returns the next page object
              self.fill(self.USERNAME, user); self.fill(self.PASSWORD, pwd); self.click(self.SUBMIT)
              return CustomersPage(self.page)

  Use them from steps:  `page = page_object(context, LoginPage).open()`
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import List, Optional, Type, TypeVar

import pandas as pd

from pyassure.web.runtime import get_page, resolve_url

T = TypeVar("T", bound="BasePage")


class BasePage:
    """Base class for page objects. Subclasses set `path` (and usually `ready`) and add actions."""

    path: str = ""                    # URL path of this page, e.g. "/customers.html"
    ready: Optional[str] = None       # selector that is visible once the page has loaded

    def __init__(self, page):
        self.page = page              # Playwright Page

    # ---- navigation
    @property
    def url(self) -> str:
        return resolve_url(self.path)

    def open(self: T) -> T:
        """Navigate to the page and wait until it is ready. Returns self (chainable)."""
        self.page.goto(self.url)
        return self.wait_ready()

    def wait_ready(self: T) -> T:
        if self.ready:
            self.page.wait_for_selector(self.ready, state="visible")
        return self

    # ---- thin, intention-revealing helpers (keep selectors in constants, call these in actions)
    def loc(self, selector: str):
        return self.page.locator(selector)

    def click(self, selector: str) -> None:
        self.page.click(selector)

    def fill(self, selector: str, value: str) -> None:
        self.page.fill(selector, value)

    def select(self, selector: str, label: str) -> None:
        self.page.select_option(selector, label=label)

    def text(self, selector: str) -> str:
        return self.loc(selector).first.inner_text().strip()

    def is_visible(self, selector: str) -> bool:
        return self.loc(selector).first.is_visible()

    def count(self, selector: str) -> int:
        return self.loc(selector).count()

    def title(self) -> str:
        return self.page.title()


class Component:
    """A reusable widget inside a page (nav bar, modal, table). Scoped to a root locator."""

    def __init__(self, root):
        self.root = root              # Playwright Locator of the component's root element

    def loc(self, selector: str):
        return self.root.locator(selector)


class TableComponent(Component):
    """An HTML <table> as data: headers, rows and a DataFrame (handy to compare UI vs database)."""

    def to_dataframe(self) -> pd.DataFrame:
        return parse_html_table(self.root.evaluate("el => el.outerHTML"))

    def row_count(self) -> int:
        return self.loc("tbody tr").count()


def page_object(context, cls: Type[T], session: Optional[str] = None) -> T:
    """Instantiate page object `cls` on the scenario's current page (cached per scenario)."""
    cache = getattr(context, "pa_pages", None)
    if cache is None:
        cache = context.pa_pages = {}
    if cls not in cache:
        cache[cls] = cls(get_page(context, session))
    return cache[cls]


# --------------------------------------------------------------------------- table parsing
class _TableCollector(HTMLParser):
    """Collects cell text row by row; nested tables are ignored (only the outer table is read)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: List[List[str]] = []
        self._row: Optional[List[str]] = None
        self._cell: Optional[List[str]] = None
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._depth += 1
        elif self._depth == 1:
            if tag == "tr":
                self._row = []
            elif tag in ("td", "th"):
                self._cell = []

    def handle_endtag(self, tag):
        if tag == "table":
            self._depth -= 1
        elif self._depth == 1:
            if tag in ("td", "th") and self._cell is not None and self._row is not None:
                self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
                self._cell = None
            elif tag == "tr" and self._row is not None:
                if self._row:
                    self.rows.append(self._row)
                self._row = None

    def handle_data(self, data):
        if self._cell is not None and self._depth == 1:
            self._cell.append(data)


def parse_html_table(html: str, header: bool = True) -> pd.DataFrame:
    """HTML of one <table> -> DataFrame of strings (pure Python, testable without a browser).

    header=True: first row = column names; False: COL_1, COL_2 ... Ragged rows are padded with ''.
    """
    p = _TableCollector()
    p.feed(html)
    if not p.rows:
        return pd.DataFrame()
    width = max(len(r) for r in p.rows)
    rows = [r + [""] * (width - len(r)) for r in p.rows]
    if header:
        return pd.DataFrame(rows[1:], columns=rows[0])
    return pd.DataFrame(rows, columns=[f"COL_{i}" for i in range(1, width + 1)])
