"""API testing with Playwright's own HTTP client (APIRequestContext).

Why Playwright for APIs?  The same driver, the same storageState sessions and the same reports serve
UI and API tests, so a test can log in through the API and continue in the browser (or the reverse),
and no second HTTP library is needed. API-only runs never start a browser.

  pyassure/   provides `ApiClient`, `ApiResponse`, `json_path`, `api_client()` (generic).
  app/api/    holds YOUR typed clients (application specific), e.g.

      class CustomersApi(ApiClient):
          def list(self):            return self.get("/api/customers")
          def create(self, body):    return self.post("/api/customers", json=body)

  Use from steps:  `api = api_client(context, CustomersApi, session="admin")`

Every call is recorded (method, url, status, time, bodies) in the report's "API calls" table and, when
Allure is enabled, attached as request/response JSON.
"""
from __future__ import annotations

import json as jsonlib
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Type, TypeVar

from pyassure.web.runtime import get_storage_state, runtime

MISSING = object()          # sentinel: json_path() found nothing (distinct from a JSON null)
C = TypeVar("C", bound="ApiClient")


@dataclass
class ApiResponse:
    """What a test needs from a response (a plain snapshot - safe to keep after the context closes)."""
    method: str
    url: str
    status: int
    headers: Dict[str, str]
    text: str
    elapsed_ms: float
    request_body: Any = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> Any:
        return jsonlib.loads(self.text) if self.text else None


@dataclass
class ApiClient:
    """Thin client over a Playwright APIRequestContext. Subclass it in app/api/ for typed helpers."""

    ctx: Any                                          # APIRequestContext
    calls: List[ApiResponse] = field(default_factory=list)
    on_call: Optional[Callable[[ApiResponse], None]] = None   # hook used by the reporting layer

    # ---- construction
    @classmethod
    def open(cls: Type[C], session: Optional[str] = None, base_url: str = "",
             headers: Optional[dict] = None) -> C:
        """New client. `session` loads a storageState file (cookies) created by a session provider."""
        state = get_storage_state(session) if session else None
        return cls(runtime().new_api_context(state, base_url, headers))

    # ---- requests
    def request(self, method: str, path: str, *, params: Optional[dict] = None, json: Any = None,
                data: Any = None, headers: Optional[dict] = None) -> ApiResponse:
        """Send a request. Never raises on 4xx/5xx - tests assert the status themselves."""
        t0 = time.perf_counter()
        r = self.ctx.fetch(path, method=method.upper(), params=params, data=json if json is not None else data,
                           headers=headers, fail_on_status_code=False)
        resp = ApiResponse(method.upper(), r.url, r.status, dict(r.headers), r.text(),
                           (time.perf_counter() - t0) * 1000, json if json is not None else data)
        self.calls.append(resp)
        if self.on_call:
            self.on_call(resp)
        return resp

    def get(self, path, **kw):    return self.request("GET", path, **kw)
    def post(self, path, **kw):   return self.request("POST", path, **kw)
    def put(self, path, **kw):    return self.request("PUT", path, **kw)
    def patch(self, path, **kw):  return self.request("PATCH", path, **kw)
    def delete(self, path, **kw): return self.request("DELETE", path, **kw)

    @property
    def last(self) -> Optional[ApiResponse]:
        return self.calls[-1] if self.calls else None

    def close(self) -> None:
        self.ctx.dispose()


def json_path(data: Any, path: str) -> Any:
    """Read a value from parsed JSON: 'user.name', 'items[0].id', 'items.0.id'. '' or '$' = root.
    Returns MISSING when any segment does not exist."""
    cur = data
    for part in [p for p in path.replace("[", ".").replace("]", "").split(".") if p and p != "$"]:
        if isinstance(cur, list) and part.lstrip("-").isdigit() and -len(cur) <= int(part) < len(cur):
            cur = cur[int(part)]
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return MISSING
    return cur


# ---------------------------------------------------------- scenario-scoped clients (behave)
def api_client(context, cls: Type[C] = ApiClient, session: Optional[str] = None) -> C:
    """Client of type `cls` for this scenario (cached per (class, session); closed after the scenario)."""
    cache: Dict = getattr(context, "pa_api", None)
    if cache is None:
        cache = context.pa_api = {}
    key = (cls, session)
    if key not in cache:
        client = cls.open(session)
        from pyassure.reporting import allure_support           # lazy: keeps web/ independent of reporting/

        def record(resp):                                         # evidence for both report types
            context.store.api_call(resp)
            allure_support.attach_api_call(resp)
        client.on_call = record
        cache[key] = client
    return cache[key]


def close_all(context) -> None:
    for client in (getattr(context, "pa_api", None) or {}).values():
        client.close()
    context.pa_api = None
