"""Playwright runtime: one driver/browser per process, per-scenario pages, and storageState sessions.

Key facts that shape this module
  * Playwright's SYNC API allows ONE driver per process -> a single shared `Runtime`.
    UI pages, API clients, session logins and the web_table/api connectors all go through it.
  * One BROWSER per process (slow to start), one fresh CONTEXT + page per scenario (cheap, isolated
    cookies/storage). API-only runs never launch a browser - only the driver.
  * Parallel workers are separate processes (see runner.py), so each has its own Runtime.

Sessions (storageState)
  Logging in through the UI for every scenario is slow and flaky. Instead an app-defined login
  function (a "session provider") runs ONCE, its cookies/localStorage are saved with
  `context.storage_state()` to a JSON file, and later scenarios start already authenticated:

      app/auth.py:        @session_provider("admin")
                          def admin(context): ...log in using context.new_page() or context.request...
      pyassure.yml:       sessions: {providers: app.auth, ttl_minutes: 60, warmup: [admin]}
      scenario:           Given I am logged in as "admin"

  State files are cached for `ttl_minutes`; creation is guarded by a file lock so parallel workers
  never log in twice; the runner can pre-create them (`warmup`) before workers start.
"""
from __future__ import annotations

import atexit
import contextlib
import importlib
import os
import re
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from pyassure.config import ConfigError, resolve_path, section


# ===================================================================== runtime singleton
def _ui_opts() -> dict:
    """UI settings with env overrides (PYASSURE_BROWSER / PYASSURE_HEADLESS, set from -D options)."""
    ui = dict(section("ui"))
    if os.environ.get("PYASSURE_BROWSER"):
        ui["browser"] = os.environ["PYASSURE_BROWSER"]
    if os.environ.get("PYASSURE_HEADLESS"):
        ui["headless"] = os.environ["PYASSURE_HEADLESS"].strip().lower() in ("1", "true", "yes", "on")
    if ui["browser"] not in ("chromium", "firefox", "webkit"):
        raise ConfigError(f"Unknown browser '{ui['browser']}' (chromium, firefox or webkit)")
    return ui


class Runtime:
    """Owns the Playwright driver and (lazily) the browser."""

    def __init__(self) -> None:
        self.ui = _ui_opts()
        self._pw = None
        self._browser = None

    @property
    def playwright(self):
        if self._pw is None:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:
                raise ConfigError("Playwright missing: pip install playwright && playwright install chromium") from exc
            self._pw = sync_playwright().start()
        return self._pw

    @property
    def browser(self):
        if self._browser is None:
            self._browser = getattr(self.playwright, self.ui["browser"]).launch(
                headless=self.ui["headless"], slow_mo=self.ui["slow_mo_ms"] or 0)
        return self._browser

    def new_context(self, storage_state: Optional[str] = None, **extra):
        """Isolated browser context, optionally pre-authenticated from a storageState file."""
        vp = self.ui["viewport"]
        ctx = self.browser.new_context(viewport={"width": vp["width"], "height": vp["height"]},
                                       storage_state=storage_state, **extra)
        ctx.set_default_timeout(self.ui["timeout_ms"])
        return ctx

    def new_api_context(self, storage_state: Optional[str] = None, base_url: str = "", headers: Optional[dict] = None):
        """Playwright APIRequestContext (HTTP client sharing cookies with a storageState file)."""
        api = section("api")
        return self.playwright.request.new_context(
            base_url=base_url or api["base_url"] or self.ui["base_url"] or None,
            extra_http_headers={**api["headers"], **(headers or {})} or None,
            storage_state=storage_state, timeout=api["timeout_ms"],
            ignore_https_errors=api["ignore_https_errors"])

    def stop(self) -> None:
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._pw:
            self._pw.stop()
            self._pw = None


_RUNTIME: Optional[Runtime] = None


def runtime() -> Runtime:
    """The process-wide Runtime (created on first use, stopped automatically at exit)."""
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = Runtime()
        atexit.register(shutdown)
    return _RUNTIME


def shutdown() -> None:
    global _RUNTIME
    if _RUNTIME:
        _RUNTIME.stop()
        _RUNTIME = None


_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


def resolve_url(url: str) -> str:
    """http(s)/file URL as-is; an existing project file -> file:// URL; else appended to ui.base_url."""
    if _SCHEME.match(url) or url.startswith("about:"):
        return url
    local = resolve_path(url)
    if local.exists():
        return local.as_uri()
    base = section("ui")["base_url"]
    return base.rstrip("/") + "/" + url.lstrip("/") if base else url


# ========================================================================= sessions
_PROVIDERS: Dict[str, tuple] = {}      # name -> (login function, kind)


def session_provider(name: str, kind: str = "browser"):
    """Register the login routine for session `name`.

        @session_provider("admin_ui")                  # kind="browser": gets a fresh BrowserContext
        def admin_ui(context):
            page = context.new_page(); ...             # log in through the UI (use your page objects)

        @session_provider("admin", kind="api")         # kind="api": gets an APIRequestContext - no browser
        def admin(request):                            #   is launched, so this is the fastest way to log in
            request.post("/api/login", data={...})     #   (the saved cookies work for UI *and* API tests)
    """
    def deco(fn):
        _PROVIDERS[name] = (fn, kind)
        return fn
    return deco


def _providers() -> Dict[str, tuple]:
    module = section("sessions")["providers"]
    if module:
        importlib.import_module(module)         # importing registers the @session_provider functions
    return _PROVIDERS


def state_file(name: str) -> Path:
    return resolve_path(section("sessions")["dir"]) / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', name)}.json"


def _fresh(path: Path) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < section("sessions")["ttl_minutes"] * 60


@contextlib.contextmanager
def _lock(path: Path, timeout: float = 60):
    """Cross-process lock (atomic create). Stale locks (a crashed worker) are removed after 2x timeout."""
    path.parent.mkdir(parents=True, exist_ok=True)
    start = time.time()
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.time() - path.stat().st_mtime > timeout * 2:
                path.unlink(missing_ok=True)
            elif time.time() - start > timeout:
                raise ConfigError(f"Timed out waiting for session lock {path.name}")
            time.sleep(0.2)
    try:
        yield
    finally:
        os.close(fd)
        path.unlink(missing_ok=True)


def get_storage_state(name: str, force: bool = False) -> str:
    """Path of a valid storageState file for session `name`, logging in via its provider if needed."""
    path = state_file(name)
    if not force and _fresh(path):
        return str(path)
    with _lock(path.with_suffix(".lock")):
        if not force and _fresh(path):          # another worker created it while we waited
            return str(path)
        provider = _providers().get(name)
        if provider is None:
            raise ConfigError(f"No @session_provider registered for session '{name}' "
                              f"(set sessions.providers in pyassure.yml to the module that defines it)")
        fn, kind = provider
        ctx = runtime().new_api_context() if kind == "api" else runtime().new_context()
        try:
            fn(ctx)
            tmp = path.with_suffix(".tmp")
            ctx.storage_state(path=str(tmp))
            os.replace(tmp, path)               # atomic: readers never see a half-written file
        finally:
            ctx.dispose() if kind == "api" else ctx.close()
    return str(path)


def warm_up(names: List[str]) -> None:
    """Create sessions once up-front (the parallel runner does this before starting workers)."""
    for n in names:
        get_storage_state(n)


def invalidate(name: str) -> None:
    state_file(name).unlink(missing_ok=True)


# ===================================================== per-scenario UI (behave context)
class _ScenarioUI:
    """Holds the scenario's context/page so they can be closed (and trace/video kept) at the end."""

    def __init__(self, session: Optional[str], artifacts: Path):
        ui = runtime().ui
        self.session, self.artifacts, self.ui = session, artifacts, ui
        state = get_storage_state(session) if session else None
        extra = {}
        if ui["record_video"]:
            extra["record_video_dir"] = str(artifacts / "videos_tmp")
        self.ctx = runtime().new_context(storage_state=state, **extra)
        if ui["trace_on_failure"]:
            self.ctx.tracing.start(screenshots=True, snapshots=True)
        self.page = self.ctx.new_page()
        self.video = self.page.video if ui["record_video"] else None

    def close(self, failed: bool, name: str) -> List[Path]:
        """Close the context; keep trace/video only for failed scenarios. Returns kept files."""
        kept: List[Path] = []
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)[:80]
        if self.ui["trace_on_failure"]:
            if failed:
                self.artifacts.mkdir(parents=True, exist_ok=True)
                self.ctx.tracing.stop(path=str(self.artifacts / f"{safe}_trace.zip"))
                kept.append(self.artifacts / f"{safe}_trace.zip")
            else:
                self.ctx.tracing.stop()
        video = Path(self.video.path()) if self.video else None
        self.ctx.close()                         # finalises the video file
        if video and video.exists():
            if failed:
                dest = self.artifacts / f"{safe}.webm"
                video.replace(dest)
                kept.append(dest)
            else:
                video.unlink(missing_ok=True)
        return kept


def get_page(context, session: Optional[str] = None):
    """The scenario's Playwright Page (opened on first use). `session` starts it pre-authenticated.

    Asking for a different session than the open page uses re-creates the page, so log in FIRST.
    """
    cur: Optional[_ScenarioUI] = getattr(context, "pa_ui", None)
    if cur is not None and session and cur.session != session:
        cur.close(failed=False, name="session-switch")
        cur = None
    if cur is None:
        cur = context.pa_ui = _ScenarioUI(session, Path(context.report_dir) / "ui_artifacts")
    return cur.page


def screenshot(context) -> Optional[bytes]:
    """Full-page PNG of the scenario's page (None if no page is open or it crashed)."""
    cur = getattr(context, "pa_ui", None)
    try:
        return cur.page.screenshot(full_page=True) if cur else None
    except Exception:
        return None


def end_scenario(context, failed: bool, name: str) -> List[Path]:
    cur = getattr(context, "pa_ui", None)
    if cur is None:
        return []
    context.pa_ui = None
    return cur.close(failed, name)
