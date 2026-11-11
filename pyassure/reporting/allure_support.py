"""Allure integration, switchable from app/config/pyassure.yml:

    reports:
      allure:
        enabled: false                       # <- the switch (per run: behave -D allure=true / run_tests.py --allure)
        results_dir: reports/allure-results  # raw results (JSON + attachments) written during the run
        report_dir: reports/allure-report    # HTML produced by `allure generate`
        generate: true                       # runner: build the HTML when the Allure CLI is available
        cli: ""                              # path to the allure binary (else $ALLURE_BIN, else PATH)

How it plugs into behave
  allure-behave ships a *formatter*. behave creates formatters before `before_all`, so a config switch
  cannot add one through behave.ini. Instead `activate()` builds an AllureFormatter and appends it to the
  running behave Runner's formatter list in before_all - behave then feeds it features/scenarios/steps
  like any other formatter. (This relies on `context._runner.formatters`; if a future behave changes
  that, `activate()` warns and the rest of the reporting keeps working.)

Attachments (screenshots, traces, videos, API calls) go through the small helpers below, which are
silent no-ops when Allure is disabled or not installed - callers never need to check.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from pyassure.branding import NAME, VERSION
from pyassure.config import resolve_path, section


def cfg() -> dict:
    return section("reports")["allure"]


def is_enabled(context=None) -> bool:
    """`-D allure=true|false` (userdata) overrides pyassure.yml."""
    if context is not None:
        override = context.config.userdata.get("allure")
        if override is not None:
            return str(override).strip().lower() in ("1", "true", "yes", "on")
    return bool(cfg()["enabled"])


def results_dir(context=None) -> Path:
    """Runner workers pass a shared `-D allure_dir=...` so all of them write one results folder."""
    override = context.config.userdata.get("allure_dir") if context is not None else None
    return resolve_path(override or cfg()["results_dir"])


def activate(context) -> bool:
    """Attach an AllureFormatter to the running behave runner. Returns True when Allure is active."""
    if not is_enabled(context):
        return False
    try:
        from allure_behave.formatter import AllureFormatter
        from behave.formatter.base import StreamOpener
    except ImportError:
        print(f"[{NAME}] Allure is enabled but allure-behave is not installed: pip install allure-behave")
        return False
    out = results_dir(context)
    out.mkdir(parents=True, exist_ok=True)
    try:
        context._runner.formatters.append(AllureFormatter(StreamOpener(str(out)), context.config))
    except Exception as exc:                       # never let reporting break the test run
        print(f"[{NAME}] Could not activate Allure: {exc}")
        return False
    # Shown on Allure's Overview page ("Environment" widget).
    ud = context.config.userdata
    (out / "environment.properties").write_text(
        f"Framework={NAME} {VERSION}\nBrowser={os.environ.get('PYASSURE_BROWSER') or section('ui')['browser']}\n"
        f"Environment={ud.get('env', '')}\n", encoding="utf-8")
    return True


# ------------------------------------------------------------------------- attachments
def attach(name: str, content, kind: str = "txt") -> None:
    """Attach bytes/str to the current Allure test. kind: png | json | txt | zip | webm."""
    try:
        import allure
        types = {"png": allure.attachment_type.PNG, "json": allure.attachment_type.JSON,
                 "txt": allure.attachment_type.TEXT}
        if kind in types:
            allure.attach(content, name=name, attachment_type=types[kind])
        else:                                      # arbitrary file types (trace.zip, video)
            allure.attach(content, name=name, extension=kind)
    except Exception:
        pass                                       # Allure inactive/not installed: ignore


def attach_file(path: Path) -> None:
    attach(path.name, path.read_bytes(), path.suffix.lstrip(".") or "bin")


def attach_api_call(resp) -> None:
    attach(f"{resp.method} {resp.url} -> {resp.status}", json.dumps(
        {"request": resp.request_body, "status": resp.status, "elapsed_ms": round(resp.elapsed_ms, 1),
         "response": resp.text[:5000]}, indent=2, default=str), "json")


# ------------------------------------------------------------------------- HTML report
def find_cli() -> Optional[str]:
    return cfg()["cli"] or os.environ.get("ALLURE_BIN") or shutil.which("allure")


def generate(results: Path, report: Path) -> bool:
    """Run `allure generate` (needs the Allure CLI + Java). Returns True when a report was produced."""
    cli = find_cli()
    if not cli:
        return False
    r = subprocess.run([cli, "generate", str(results), "-o", str(report), "--clean"],
                       capture_output=True, text=True)
    return r.returncode == 0
