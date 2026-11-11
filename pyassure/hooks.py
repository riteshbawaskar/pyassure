"""behave lifecycle hooks - the wiring of the whole framework.

The application's `app/features/environment.py` is a single line:

    from pyassure.hooks import *          # before_all, before_scenario, after_step, after_scenario, after_all

To add app-specific behaviour define your own hook and call the framework's first:

    import pyassure.hooks as pa
    def before_all(context):
        pa.before_all(context)
        ...your setup...

Order: before_all -> [before_scenario -> steps (after_step each) -> after_scenario]* -> after_all
Responsibilities: result store, optional Allure, app server lifecycle, UI failure evidence
(screenshot / trace / video), closing browser contexts and API clients, writing reports.
"""
import os
import shutil

from pyassure import services
from pyassure.branding import BANNER
from pyassure.config import BASE_DIR, section
from pyassure.reporting import allure_support
from pyassure.reporting.html import write_html_report
from pyassure.reporting.store import ResultStore
from pyassure.web import api, runtime

_server = None          # server process started by THIS process (None when reused/not configured)


def before_all(context):
    """Once per behave process: read -D options, start the app server, create store, enable Allure."""
    global _server
    ud = context.config.userdata
    context.report_dir = BASE_DIR / ud.get("report_dir", "reports")      # runner gives each worker its own
    context.report_dir.mkdir(parents=True, exist_ok=True)
    context.worker = ud.get("worker", "")                                # set by the parallel runner
    if ud.get("browser"):                                                # -D browser=firefox -D headless=false
        os.environ["PYASSURE_BROWSER"] = ud["browser"]
    if ud.get("headless"):
        os.environ["PYASSURE_HEADLESS"] = ud["headless"]

    context.store = ResultStore(ud.get("report_title", "PyAssure Test Report"), ud.get("env", ""))
    _server = services.ensure()

    if allure_support.is_enabled(context):
        out = allure_support.results_dir(context)
        if not context.worker and out.exists():                          # fresh results per plain run
            shutil.rmtree(out, ignore_errors=True)                       # (runner workers share one folder)
        allure_support.activate(context)
    if not context.worker:
        print(BANNER)


def before_scenario(context, scenario):
    context.store.start_scenario(scenario.feature.name, scenario.name, [str(t) for t in scenario.tags])
    if context.worker:
        context.store.info("Worker", context.worker)


def after_step(context, step):
    """On failure: remember the error line and grab a screenshot if this scenario opened a browser page."""
    if step.status.name != "failed":
        return
    detail = (step.error_message or "").strip().splitlines()
    context.store.error(f"{step.keyword} {step.name}\n{detail[-1] if detail else 'step failed'}")
    if section("ui")["screenshot_on_failure"]:
        png = runtime.screenshot(context)
        if png:
            context.store.attach_image(f"Failure: {step.name}", png)
            allure_support.attach(f"Failure: {step.name}", png, "png")


def after_scenario(context, scenario):
    """Close browser context + API clients; keep trace/video of failed scenarios."""
    failed = scenario.status.name in ("failed", "error")
    kept = runtime.end_scenario(context, failed, scenario.name)
    api.close_all(context)
    if kept:
        context.store.info("Artifacts", ", ".join(p.name for p in kept))
        for p in kept:
            allure_support.attach_file(p)
    context.store.end_scenario(scenario.status.name)


def after_all(context):
    """Stop the browser, write the standalone report + JSON, stop the server we started."""
    runtime.shutdown()
    if section("reports")["standalone_html"]:
        html = write_html_report(context.store, context.report_dir / "validation_report.html")
        context.store.write_json(context.report_dir / "validation_results.json")
        if not context.worker:
            print(f"\nPyAssure report : {html}")
    services.stop(_server)


__all__ = ["before_all", "before_scenario", "after_step", "after_scenario", "after_all"]
