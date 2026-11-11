# 6. API testing with Playwright

PyAssure uses Playwright's own HTTP client (`APIRequestContext`): the **same driver, sessions and reports** as UI tests, and API-only runs
**never launch a browser**. A test can log in over the API and continue in the browser (or the reverse).

## 6.1 Scenarios with the generic steps
```gherkin
@api
Scenario: A customer can be created, read back and deleted
  Given the API is authenticated as "admin"
  When I send a POST request to "/api/customers" with JSON:
    """
    {"name": "Eve Example", "email": "eve@example.com"}
    """
  Then the response status should be 201
  When I save the response JSON field "id" as "new_id"
  And I send a GET request to "/api/customers/{{new_id}}"
  Then the response JSON field "name" should equal "Eve Example"
  When I send a DELETE request to "/api/customers/{{new_id}}"
  Then the response status should be 204
```
`{{name}}` placeholders in paths/bodies come from `I save the response JSON field ... as "name"`. JSON paths: `a.b`, `items[0].id`, `items.0.id`, `$` (root).

## 6.2 Typed clients (`app/api/`)
```python
from pyassure.web.api import ApiClient

class CustomersApi(ApiClient):
    def list(self, name=""):  return self.get("/api/customers", params={"name": name} if name else None)
    def create(self, **f):    return self.post("/api/customers", json=f)
    def remove(self, id):     return self.delete(f"/api/customers/{id}")
```
In steps: `api = api_client(context, CustomersApi, session="admin")` (cached per scenario, closed afterwards).

| API | |
|---|---|
| `ApiClient.request(method, path, params=, json=, data=, headers=)` | never raises on 4xx/5xx - tests assert the status |
| `get/post/put/patch/delete(path, **kw)` | shortcuts |
| `ApiClient.open(session=None, base_url="", headers=None)` | standalone client (outside behave) |
| `client.last`, `client.calls` | last / all `ApiResponse` |
| `ApiResponse` | `status, ok, headers, text, json(), elapsed_ms, method, url, request_body` |
| `json_path(data, "a.b[0]")` | value or `MISSING` (distinct from JSON `null`) |

Base URL, default headers and timeout: `api:` in `pyassure.yml`. Auth: storageState cookies via `session=`; for token APIs set `api.headers` or pass
`headers=` (e.g. `{"Authorization": "Bearer ..."}`; secrets may be `ENC(...)`).

## 6.3 Compare an API with the database
```yaml
target:
  type: api
  path: /api/customers
  params: {name: Customer}
  json_path: customers            # where the list is in the body
  session: admin
column_mapping: {id: CUSTOMER_ID}  # target_column: source_column
```
## 6.4 Evidence
Every call is listed in the report's **API calls** table (method, URL, status, time, bodies) and, with Allure on, attached as request/response JSON.

## 6.5 Test-data hygiene (matters in parallel runs)
Scenarios that create data must **delete it** (see `the new customer should be removed through the API`), and comparisons should **filter** to the data
they own (`params: {name: Customer}` + matching SQL) so concurrent scenarios cannot disturb each other.
