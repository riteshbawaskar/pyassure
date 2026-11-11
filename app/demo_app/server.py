"""DEMO application under test (stdlib only): a login page, a customer directory and a JSON API.

    python app/demo_app/server.py          ->  http://127.0.0.1:8765

Replace it with YOUR application: point `ui.base_url`, `api.base_url` and (optionally) `web_server` in
app/config/pyassure.yml at the real thing and delete this folder.

  Pages  /login.html   (admin / admin123)        /customers.html (needs a login)
  API    GET  /api/health                         POST   /api/login          {"username","password"} -> sets cookie
         GET  /api/customers[?name=...]  (auth)   POST   /api/customers      {"name",...}            -> 201
         GET  /api/customers/<id>        (auth)   DELETE /api/customers/<id>                         -> 204
  Auth   cookie `session=demo-token` (set by /api/login) or header `Authorization: Bearer demo-token`
"""
import json
import sqlite3
import sys
from urllib.parse import parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = 8765
TOKEN = "demo-token"
USERS = {"admin": "admin123"}


def load_customers():
    """Customers come from the same demo DB the database scenarios read (lower-case JSON keys)."""
    with sqlite3.connect(HERE.parent / "data" / "demo.db") as db:
        db.row_factory = sqlite3.Row
        return [{"id": r["CUSTOMER_ID"], "name": r["NAME"], "email": r["EMAIL"], "country": r["COUNTRY"],
                 "credit_limit": r["CREDIT_LIMIT"], "status": r["STATUS"]}
                for r in db.execute("SELECT * FROM customers ORDER BY CUSTOMER_ID")]


CUSTOMERS = load_customers()          # in-memory: POST/DELETE never touch the database file


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):     # keep test output quiet
        pass

    # ---- helpers
    def _send(self, status, body=None, headers=None, ctype="application/json"):
        data = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _authed(self):
        cookie = self.headers.get("Cookie", "")
        return f"session={TOKEN}" in cookie or self.headers.get("Authorization") == f"Bearer {TOKEN}"

    def _json(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _guard(self):
        if not self._authed():
            self._send(401, {"error": "unauthorized"})
            return False
        return True

    # ---- routes
    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/api/health":
            return self._send(200, {"status": "ok"})
        if path == "/api/customers":
            if not self._guard():
                return
            name = parse_qs(query).get("name", [""])[0].lower()      # decodes '+' and %XX properly
            rows = [c for c in CUSTOMERS if name in c["name"].lower()]
            return self._send(200, {"customers": rows, "total": len(rows)})
        if path.startswith("/api/customers/"):
            if not self._guard():
                return
            found = [c for c in CUSTOMERS if str(c["id"]) == path.rsplit("/", 1)[1]]
            return self._send(200, found[0]) if found else self._send(404, {"error": "not found"})
        file = HERE / "site" / (path.lstrip("/") or "login.html")           # static pages
        if file.is_file() and file.parent == HERE / "site":
            return self._send(200, file.read_bytes(), ctype="text/html; charset=utf-8")
        self._send(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/login":
            body = self._json()
            if USERS.get(body.get("username")) == body.get("password"):
                return self._send(200, {"token": TOKEN, "user": body["username"]},
                                  {"Set-Cookie": f"session={TOKEN}; Path=/; HttpOnly"})
            return self._send(401, {"error": "invalid credentials"})
        if path == "/api/customers":
            if not self._guard():
                return
            body = self._json()
            if not body.get("name"):
                return self._send(400, {"error": "name is required"})
            new = {"id": max(c["id"] for c in CUSTOMERS) + 1, "name": body["name"], "email": body.get("email", ""),
                   "country": body.get("country", ""), "credit_limit": body.get("credit_limit", 0.0),
                   "status": body.get("status", "ACTIVE")}
            CUSTOMERS.append(new)
            return self._send(201, new)
        self._send(404, {"error": "not found"})

    def do_DELETE(self):
        if not self._guard():
            return
        cid = self.path.rsplit("/", 1)[1]
        before = len(CUSTOMERS)
        CUSTOMERS[:] = [c for c in CUSTOMERS if str(c["id"]) != cid]
        self._send(204) if len(CUSTOMERS) < before else self._send(404, {"error": "not found"})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
