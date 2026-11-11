"""Page object: the customer directory (needs a logged-in session)."""
from pyassure.web.pages import BasePage, TableComponent


class CustomersPage(BasePage):
    path = "/customers.html"
    ready = "#customers tbody tr"        # the grid is filled by an API call after load

    HEADING, FILTER, SEARCH, SUMMARY, GRID = "#heading", "#name-filter", "#search", "#result-count", "#customers"

    def search(self, text: str) -> "CustomersPage":
        """Type into the filter box and press Search. Returns self so calls can be chained."""
        self.fill(self.FILTER, text)
        self.click(self.SEARCH)
        return self

    def summary(self) -> str:
        return self.text(self.SUMMARY)

    def visible_rows(self) -> int:
        """Rows currently shown (the demo hides non-matching rows by re-rendering the table)."""
        return self.count(f"{self.GRID} tbody tr")

    def table(self) -> TableComponent:
        """The grid as a reusable component (-> to_dataframe() for UI-vs-database comparisons)."""
        return TableComponent(self.loc(self.GRID))
