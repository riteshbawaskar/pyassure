"""Configuration: YAML loading, ${ENV} expansion, project paths and the central settings file.

Everything configurable lives in the APPLICATION folder (`app/config/`), never in the framework:

    app/config/pyassure.yml     behaviour switches: ui, api, sessions, reports (Allure), parallel ...
    app/config/connections.yml  named database connections (secrets may be ENC(...) encrypted)

Conventions shared by every YAML file read through `load_yaml`:
  * relative paths resolve from the PROJECT ROOT (same behaviour from IDE, CLI and CI);
  * ``${VAR}`` / ``${VAR:default}`` are replaced from environment variables.

Override file locations per environment with PYASSURE_CONFIG / PYASSURE_CONNECTIONS.
"""
from __future__ import annotations

import copy
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent          # project root
APP_DIR = BASE_DIR / "app"                                  # application-specific code lives here

# Defaults for every switch. app/config/pyassure.yml only needs to list what it changes.
DEFAULTS: Dict[str, Any] = {
    "ui": {
        "browser": "chromium",          # chromium | firefox | webkit
        "headless": True,
        "base_url": "",                 # relative URLs / page-object paths are appended to this
        "timeout_ms": 5000,             # default wait for elements and navigation
        "viewport": {"width": 1366, "height": 768},
        "slow_mo_ms": 0,
        "screenshot_on_failure": True,
        "trace_on_failure": False,      # keep Playwright trace.zip for failed scenarios
        "record_video": False,          # keep a .webm for failed scenarios
    },
    "api": {
        "base_url": "",                 # falls back to ui.base_url when empty
        "timeout_ms": 10000,
        "headers": {},                  # sent with every API call
        "ignore_https_errors": False,
    },
    "sessions": {                       # Playwright storageState management
        "dir": ".pyassure/sessions",    # where the cached state files are kept
        "ttl_minutes": 60,              # re-login when a state file is older than this
        "providers": "",                # python module with @session_provider functions, e.g. app.auth
        "warmup": [],                   # sessions the parallel runner creates ONCE before the workers start
    },
    "reports": {
        "standalone_html": True,        # compact PyAssure HTML + JSON
        "allure": {
            "enabled": False,           # <- the Allure switch (override per run: -D allure=true)
            "results_dir": "reports/allure-results",
            "report_dir": "reports/allure-report",
            "generate": True,           # runner: run `allure generate` when the CLI is available
            "cli": "",                  # path to the allure binary (default: found on PATH / ALLURE_BIN)
        },
    },
    "parallel": {"workers": "auto", "scheme": "feature", "retries": 0},
    "web_server": {                     # like Playwright's `webServer`: started if the url is not up
        "command": "", "url": "", "startup_timeout_s": 30,
    },
}


class ConfigError(Exception):
    """Invalid or missing configuration (clear message, no stack-trace noise)."""


# ------------------------------------------------------------------------------ helpers
def resolve_path(path: str | os.PathLike) -> Path:
    """Project-relative -> absolute path (absolute paths pass through)."""
    p = Path(path).expanduser()
    return p if p.is_absolute() else BASE_DIR / p


_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}")


def expand_env(value: Any) -> Any:
    """Recursively replace ${VAR}/${VAR:default}. Unresolved placeholders are left as-is so
    callers can detect them and raise a helpful error."""
    if isinstance(value, str):
        def sub(m: re.Match) -> str:
            env = os.environ.get(m.group(1))
            return env if env is not None else (m.group(2) if m.group(2) is not None else m.group(0))
        return _ENV.sub(sub, value)
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    return value


def load_yaml(path: str | os.PathLike) -> Dict[str, Any]:
    p = resolve_path(path)
    if not p.exists():
        raise ConfigError(f"Config file not found: {p}")
    with p.open(encoding="utf-8") as fh:
        return expand_env(yaml.safe_load(fh) or {})


def _merge(base: Dict, over: Dict) -> Dict:
    """Deep-merge `over` into a copy of `base` (dicts merge, everything else replaces)."""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


# ------------------------------------------------------------------------------ settings
@lru_cache(maxsize=1)
def settings() -> Dict[str, Any]:
    """The merged settings (DEFAULTS < app/config/pyassure.yml). Cached per process."""
    f = os.environ.get("PYASSURE_CONFIG", str(APP_DIR / "config" / "pyassure.yml"))
    return _merge(DEFAULTS, load_yaml(f) if resolve_path(f).exists() else {})


def section(name: str) -> Dict[str, Any]:
    """One top-level section of the settings, e.g. section("ui")."""
    return settings()[name]


def connection(name: str) -> Dict[str, Any]:
    """Named connection from connections.yml. ENC(...) values stay encrypted here; they are
    decrypted in memory by the connector factory just before connecting."""
    f = os.environ.get("PYASSURE_CONNECTIONS", str(APP_DIR / "config" / "connections.yml"))
    conns = load_yaml(f).get("connections", {})
    if name not in conns:
        raise ConfigError(f"Connection '{name}' not found in {f}. Available: {sorted(conns)}")
    cfg = dict(conns[name])
    unresolved = [k for k, v in cfg.items() if isinstance(v, str) and "${" in v]
    if unresolved:   # leftover ${...} = env var not set and no default
        raise ConfigError(f"Connection '{name}': environment variables not set for {unresolved}")
    if "type" not in cfg:
        raise ConfigError(f"Connection '{name}' is missing the 'type' key")
    return cfg
