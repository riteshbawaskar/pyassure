"""Every data source in one place. A connector turns a QUERY into a pandas DataFrame (a "recordset").

Because comparison/validation only ever see DataFrames, any source can be mixed with any other:
Oracle vs Snowflake, database vs Excel, database vs the table on a web page, database vs an API.

  type        reads                                   key settings
  ---------   -------------------------------------   -------------------------------------------------
  snowflake   Snowflake SQL                           account, user, password, warehouse, database, schema
  oracle      Oracle SQL (python-oracledb thin)       user, password, host/port/service_name | dsn
  databricks  Databricks SQL                          server_hostname, http_path, access_token
  sqlite      SQLite SQL (demos / tests)              path
  excel       Excel sheets queried with SQL           path, [sheet], [header_row], [read_as_text]
  web_table   an HTML <table> on a page (Playwright)  url, selector, [session], [actions]
  api         a JSON list from an API (Playwright)    path, [method], [json_path], [session], [body]

Endpoint dict (source:/target: in compare configs, top level of validation configs):

    connection: snowflake_dev     # name from app/config/connections.yml ... or inline: type: excel, path: ...
    query: SELECT ...             # or query_file: path.sql  or  table: SCHEMA.TABLE
    <any other key overrides the named connection's setting>

Secrets written as ENC(...) are decrypted in memory just before connecting (see pyassure.secrets).
Database drivers are imported lazily, so install only the ones you use.
"""
from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import pandas as pd

from pyassure.config import ConfigError, connection, resolve_path
from pyassure.secrets import decrypt_tree


class Connector(ABC):
    """Lifecycle: connect() -> fetch_df(query) -> close(). Used as a context manager."""

    def __init__(self, config: Dict[str, Any]):
        self.config = dict(config)          # merged settings, secrets already decrypted

    def connect(self) -> None: ...
    def close(self) -> None: ...

    @abstractmethod
    def fetch_df(self, query: Optional[str]) -> pd.DataFrame: ...

    def __enter__(self): self.connect(); return self
    def __exit__(self, *exc): self.close()


def _cursor_df(cur) -> pd.DataFrame:
    """Any DB-API (PEP 249) cursor -> DataFrame. Avoids driver specific pandas helpers."""
    return pd.DataFrame.from_records([tuple(r) for r in cur.fetchall()], columns=[d[0] for d in cur.description])


class _DbApiConnector(Connector):
    """Shared logic for DB-API drivers: subclasses implement connect() and set self._conn."""

    def fetch_df(self, query):
        cur = self._conn.cursor()
        try:
            cur.execute(query)
            return _cursor_df(cur)
        finally:
            cur.close()

    def close(self):
        if getattr(self, "_conn", None):
            self._conn.close()


# ================================================================================ databases
class SnowflakeConnector(_DbApiConnector):
    def connect(self):
        import snowflake.connector
        c = self.config
        keys = ("account", "user", "password", "warehouse", "database", "schema", "role",
                "authenticator", "token", "session_parameters")
        params = {k: c[k] for k in keys if c.get(k)}
        if c.get("private_key_path"):                       # key-pair auth: PEM -> DER bytes
            from cryptography.hazmat.primitives import serialization as s
            pem = resolve_path(c["private_key_path"]).read_bytes()
            pw = c.get("private_key_passphrase")
            key = s.load_pem_private_key(pem, password=pw.encode() if pw else None)
            params["private_key"] = key.private_bytes(s.Encoding.DER, s.PrivateFormat.PKCS8, s.NoEncryption())
        self._conn = snowflake.connector.connect(**params)

    def fetch_df(self, query):
        cur = self._conn.cursor()
        try:
            cur.execute(query, num_statements=0)
            result = None
            while True:
                if cur.description is not None:
                    if self.config.get("use_arrow"):        # use_arrow: faster for big results (pyarrow)
                        result = cur.fetch_pandas_all()
                    else:
                        result = _cursor_df(cur)
                if not cur.nextset():
                    break
            if result is None:
                raise ConfigError("Snowflake query did not return a result set")
            return result
        finally:
            cur.close()


class OracleConnector(_DbApiConnector):
    def connect(self):
        import oracledb
        c = self.config
        if c.get("thick_mode"):                             # only for legacy features (Instant Client)
            oracledb.init_oracle_client(lib_dir=c.get("lib_dir"))
        oracledb.defaults.fetch_lobs = False                # CLOB/BLOB as str/bytes
        dsn = c.get("dsn") or oracledb.makedsn(c["host"], int(c.get("port", 1521)), service_name=c["service_name"])
        self._conn = oracledb.connect(user=c["user"], password=c["password"], dsn=dsn)


class DatabricksConnector(_DbApiConnector):
    def connect(self):
        from databricks import sql
        c = self.config
        extra = {k: c[k] for k in ("catalog", "schema") if c.get(k)}
        self._conn = sql.connect(server_hostname=c["server_hostname"], http_path=c["http_path"],
                                 access_token=c["access_token"], **extra)


class SqliteConnector(_DbApiConnector):
    def connect(self):
        self._conn = sqlite3.connect(str(resolve_path(self.config["path"])))

    def fetch_df(self, query):
        return pd.read_sql_query(query, self._conn)


# ==================================================================================== files
class ExcelConnector(Connector):
    """Excel as SQL: every sheet becomes a table of an in-memory SQLite DB named after the sheet
    (quote names with spaces: SELECT * FROM "Sales Data"). No query -> the whole (single) sheet."""

    def connect(self):
        path = resolve_path(self.config["path"])
        if not path.exists():
            raise ConfigError(f"Excel file not found: {path}")
        args = {"header": int(self.config.get("header_row", 1)) - 1}      # header_row is 1-based
        if self.config.get("read_as_text"):
            args["dtype"] = str                                           # keep leading zeros
        sheet = self.config.get("sheet")
        loaded = pd.read_excel(path, sheet_name=sheet or None, **args)
        self._sheets = {sheet: loaded} if sheet else loaded
        self._db = sqlite3.connect(":memory:")
        for name, df in self._sheets.items():
            df.to_sql(name, self._db, index=False)

    def fetch_df(self, query):
        q = (query or "").strip().rstrip(";")
        if not q:
            if len(self._sheets) == 1:
                return next(iter(self._sheets.values())).copy()
            raise ConfigError("Excel source needs a 'query' or a single 'sheet'")
        return self._sheets[q].copy() if q in self._sheets else pd.read_sql_query(q, self._db)

    def close(self):
        if getattr(self, "_db", None):
            self._db.close()


# ============================================================================ UI and API
class WebTableConnector(Connector):
    """Reads an on-screen <table> through Playwright, so a UI grid compares with a database using the
    normal compare YAML. `session: admin` opens the page already logged in (storageState);
    `actions` are optional pre-steps ({fill|click|wait_for}); ENC(...) values are decrypted."""

    def fetch_df(self, query):
        from pyassure.web.pages import parse_html_table
        from pyassure.web.runtime import get_storage_state, resolve_url, runtime
        c = self.config
        ctx = runtime().new_context(get_storage_state(c["session"]) if c.get("session") else None)
        try:
            page = ctx.new_page()
            page.goto(resolve_url(c["url"]))
            for act in c.get("actions", []):
                if "fill" in act:
                    page.fill(act["fill"], str(act.get("value", "")))
                elif "click" in act:
                    page.click(act["click"])
                elif "wait_for" in act:
                    page.wait_for_selector(act["wait_for"])
            sel = c.get("selector", "table")
            page.wait_for_selector(sel)
            if c.get("wait_for_rows"):                      # tables filled by JS after load: wait for a data row
                page.wait_for_selector(f"{sel} tbody tr")
            return parse_html_table(page.locator(sel).first.evaluate("el => el.outerHTML"), c.get("header", True))
        finally:
            ctx.close()


class ApiConnector(Connector):
    """Reads a JSON list from an API (Playwright APIRequestContext) as a recordset, so an API response
    compares with a database. `json_path` locates the list ('' = the body itself, 'data.items' ...)."""

    def fetch_df(self, query):
        from pyassure.web.api import ApiClient, json_path, MISSING
        c = self.config
        client = ApiClient.open(c.get("session"), c.get("base_url", ""), c.get("headers"))
        try:
            r = client.request(c.get("method", "GET"), c["path"], params=c.get("params"), json=c.get("body"))
            if not r.ok:
                raise ConfigError(f"API source {r.method} {r.url} returned HTTP {r.status}")
            rows = json_path(r.json(), c.get("json_path", ""))
            if rows is MISSING or not isinstance(rows, list):
                raise ConfigError(f"json_path '{c.get('json_path', '')}' did not locate a list in the response")
            return pd.json_normalize(rows)
        finally:
            client.close()


# ============================================================================== factory
REGISTRY = {"snowflake": SnowflakeConnector, "oracle": OracleConnector, "databricks": DatabricksConnector,
            "sqlite": SqliteConnector, "excel": ExcelConnector, "web_table": WebTableConnector,
            "api": ApiConnector}
_QUERY_KEYS = {"query", "query_file", "table", "connection", "name", "description"}   # WHAT to read, not HOW


def build_connector(endpoint: Dict[str, Any]) -> Connector:
    """Named connection < inline keys; secrets decrypted in memory; returns an unconnected connector."""
    settings: Dict[str, Any] = dict(connection(endpoint["connection"])) if endpoint.get("connection") else {}
    settings.update({k: v for k, v in endpoint.items() if k not in _QUERY_KEYS})
    kind = str(settings.get("type", "")).lower()
    if kind not in REGISTRY:
        raise ConfigError(f"Unsupported or missing connection type '{kind}'. Supported: {sorted(REGISTRY)}")
    return REGISTRY[kind](decrypt_tree(settings))


def resolve_query(endpoint: Dict[str, Any]) -> Optional[str]:
    """SQL from `query`, `query_file` or `table` (a trailing ';' is removed - Oracle rejects it)."""
    if endpoint.get("query"):
        return endpoint["query"].strip().rstrip(";").strip()
    if endpoint.get("query_file"):
        return resolve_path(endpoint["query_file"]).read_text(encoding="utf-8").strip().rstrip(";").strip()
    if endpoint.get("table"):
        return f"SELECT * FROM {endpoint['table']}"
    return None            # excel / web_table / api may legitimately have no query


def fetch_dataset(endpoint: Dict[str, Any]) -> pd.DataFrame:
    """Connect, read, close; return a DataFrame. The single entry point used by all steps."""
    query = resolve_query(endpoint)
    with build_connector(endpoint) as conn:
        return conn.fetch_df(query)
