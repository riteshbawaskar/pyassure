"""Parallel runner:  python run_tests.py -w 4          (installed command: pyassure-run)

behave is single-process, so PyAssure parallelises with several INDEPENDENT behave processes, each with its
own report folder, and merges the results afterwards:

    discover units -> [shard] -> start app server -> warm up sessions -> N workers -> merge reports

Schemes   feature  = one unit per .feature file (default, fewest start-ups)
          scenario = one unit per scenario/outline row (best balance for many slow scenarios)

Playwright-specific design (see docs/07-parallel-execution.md)
  * processes, not threads: Playwright's sync API allows one driver per process; each worker owns its
    browser and gives every scenario a fresh context -> perfect isolation, no locking in tests.
  * login ONCE: `sessions.warmup` in pyassure.yml creates storageState files before the workers start; they
    all reuse them (a file lock protects against races if a worker needs a session that was not warmed up).
  * app server ONCE: `web_server` is started by the runner; workers find it running and leave it alone.
  * --shard i/n splits the units across CI machines (each machine runs `--shard k/n`).
  * Allure: all workers write into one shared results folder; the runner builds the HTML when the CLI exists.

Outputs under <report-dir>/runs/<timestamp>/ (latest copies in <report-dir>/):
  validation_report.html (merged) | validation_results.json | bdd_index.html | junit/ |
  allure-results/ | run_summary.json | workers/<unit>/ (that worker's reports + behave.log)
Each merged validation report is also copied to <report-dir>/backup/ with its timestamp.
Exit code: 0 all passed | 1 some failed | 2 nothing to run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from html import escape as e
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pyassure import services
from pyassure.branding import BANNER, NAME, TAGLINE, VERSION
from pyassure.config import BASE_DIR, section
from pyassure.reporting import allure_support
from pyassure.reporting.archive import backup_validation_report
from pyassure.reporting.html import write_html_report
from pyassure.reporting.store import ResultStore, ScenarioRecord

BDD_FORMATTER = "pyassure.reporting.bdd_html:BrandedHTMLFormatter"
FEATURES_DIR = "app/features"


@dataclass
class Unit:
    """One Behave execution unit, either a feature file or a single scenario."""

    id: str          # unique + filesystem safe (worker folder name)
    target: str      # handed to behave: "app/features/a.feature" or "...:14"
    label: str       # shown to humans


@dataclass
class Result:
    """Outcome and report location for one unit after retries are complete."""

    unit: Unit
    status: str = "pending"            # passed | failed | timeout
    duration: float = 0.0
    attempts: int = 1
    worker_dir: str = ""
    has_results: bool = False
    log_tail: List[str] = field(default_factory=list)


def _slug(t: str) -> str:
    """Convert a feature name into a stable, filesystem-safe worker identifier."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", t).strip("-")[:70] or "unit"


# ------------------------------------------------------------------------------ discovery
def discover(paths: List[str], scheme: str, tag_args: List[str]) -> List[Unit]:
    """Find runnable features or scenarios using Behave's configured tag expression."""
    from behave.configuration import Configuration
    from behave.parser import parse_file
    from behave.tag_expression import make_tag_expression

    expr = make_tag_expression(Configuration(["--no-color", *tag_args]).tags)
    files: List[Path] = []
    for p in paths:
        path = Path(p) if os.path.isabs(p) else BASE_DIR / p
        files += sorted(path.rglob("*.feature")) if path.is_dir() else [path]
    units: List[Unit] = []
    for f in files:
        runnable = [s for s in parse_file(str(f)).walk_scenarios() if s.should_run_with_tags(expr)]
        rel = os.path.relpath(f, BASE_DIR)
        if not runnable:
            continue
        if scheme == "feature":
            units.append(Unit(_slug(f.stem), rel, rel))
        else:
            units += [Unit(f"{_slug(f.stem)}-L{s.line}", f"{rel}:{s.line}", f"{rel}: {s.name}") for s in runnable]
    return units


# ------------------------------------------------------------------------------ execution
def _behave_command(unit: Unit, worker_dir: Path, tag_args: List[str], defines: List[str]) -> List[str]:
    """Build the isolated Behave command for one unit."""
    return [
        sys.executable, "-m", "behave", unit.target, "--no-color",
        "-f", BDD_FORMATTER, "-o", str(worker_dir / "behave_report.html"),
        "-f", "plain",
        "--junit", "--junit-directory", str(worker_dir / "junit"),
        "-D", f"report_dir={os.path.relpath(worker_dir, BASE_DIR)}",
        "-D", f"worker={unit.id}",
        *[arg for define in defines for arg in ("-D", define)],
        *tag_args,
    ]


def run_unit(unit: Unit, run_dir: Path, tag_args: List[str], defines: List[str],
             timeout: Optional[int], retries: int) -> Result:
    """One behave subprocess (+ retries). Everything it writes goes to its own folder."""
    worker_dir = run_dir / "workers" / unit.id
    worker_dir.mkdir(parents=True, exist_ok=True)
    result = Result(unit, worker_dir=str(worker_dir))
    command = _behave_command(unit, worker_dir, tag_args, defines)
    t0 = time.time()
    for attempt in range(1, retries + 2):                                   # 1 try + N retries
        result.attempts = attempt
        try:
            with open(worker_dir / "behave.log", "w", encoding="utf-8") as log:
                rc = subprocess.run(command, cwd=BASE_DIR, stdout=log, stderr=subprocess.STDOUT, timeout=timeout,
                                    env={**os.environ, "PYTHONPATH": str(BASE_DIR)}).returncode
            result.status = "passed" if rc == 0 else "failed"
        except subprocess.TimeoutExpired:
            result.status = "timeout"
        if result.status == "passed":
            break
    result.duration = time.time() - t0
    result.has_results = (worker_dir / "validation_results.json").exists()
    if result.status != "passed":
        result.log_tail = (worker_dir / "behave.log").read_text(
            encoding="utf-8", errors="replace").splitlines()[-15:]
    return result


def merge(results: List[Result], title: str, env: str, info: Dict[str, str]) -> ResultStore:
    """Combine all workers' JSON; a worker that crashed before reporting becomes a visible error scenario."""
    store = ResultStore.merge([Path(r.worker_dir) / "validation_results.json" for r in results if r.has_results],
                              title, env, info)
    for r in results:
        if not r.has_results:
            rec = ScenarioRecord("(worker crashed)", r.unit.label, [], status="error")
            rec.errors = ["Worker produced no results. Last log lines:\n" + "\n".join(r.log_tail)]
            store.scenarios.append(rec)
    return store


def bdd_index(results: List[Result], where: Path, info: Dict[str, str]) -> str:
    """Index linking each unit's standard BDD report (those HTML files cannot be merged)."""
    rows = []
    for result in sorted(results, key=lambda item: item.unit.id):
        status_class = "ok" if result.status == "passed" else "bad"
        report_path = Path(result.worker_dir) / "behave_report.html"
        href = os.path.relpath(report_path, where).replace(os.sep, "/")
        rows.append(
            f'<tr><td class="{status_class}">{result.status.upper()}</td>'
            f'<td><a href="{e(href)}">{e(result.unit.label)}</a></td>'
            f'<td>{result.duration:.1f}s</td><td>{result.attempts}</td></tr>')

    environment = " &middot; ".join(f"{e(key)}: {e(value)}" for key, value in info.items())
    return (
        f'<!doctype html><meta charset="utf-8"><title>{e(NAME)} - BDD reports</title>'
        f'<style>body{{font:13px system-ui;margin:16px}}td,th{{padding:3px 12px;text-align:left;'
        f'border-bottom:1px solid #ddd}}.ok{{color:#15803d;font-weight:600}}'
        f'.bad{{color:#b91c1c;font-weight:600}}</style>'
        f'<h2>Py<b style="color:#15803d">Assure</b> v{e(VERSION)} &mdash; standard BDD reports</h2>'
        f'<p>{e(TAGLINE)}<br>{environment}</p>'
        f'<table><tr><th>Status</th><th>Unit</th><th>Time</th><th>Attempts</th></tr>{"".join(rows)}</table>')


def _archive_latest_reports(report_root: Path, names: List[str], timestamp: str) -> None:
    """Move current latest-run outputs under log/ before writing the new latest copies."""
    existing = [report_root / name for name in names if (report_root / name).is_file()]
    if not existing:
        return
    log_dir = report_root / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    for source in existing:
        destination = log_dir / f"{source.stem}_{timestamp}{source.suffix}"
        duplicate = 1
        while destination.exists():
            destination = log_dir / f"{source.stem}_{timestamp}-{duplicate}{source.suffix}"
            duplicate += 1
        shutil.move(str(source), str(destination))


def _worker_count(requested: str, unit_count: int) -> int:
    """Resolve the configured worker count, capped by CPU and available work."""
    if requested == "auto":
        requested_count = min(4, os.cpu_count() or 1)
    else:
        requested_count = max(1, int(requested))
    return min(requested_count, unit_count)


def _run_workers(units: List[Unit], run_dir: Path, tag_args: List[str], defines: List[str],
                 timeout: Optional[int], retries: int, workers: int,
                 fail_fast: bool) -> Tuple[List[Result], float]:
    """Run units in worker processes and return their results plus elapsed wall time."""
    lock = threading.Lock()
    completed = 0
    stop_starting = threading.Event()

    def run_job(unit: Unit) -> Optional[Result]:
        nonlocal completed
        if stop_starting.is_set():
            return None

        result = run_unit(unit, run_dir, tag_args, defines, timeout, retries)
        with lock:
            completed += 1
            attempt_note = f" (attempt {result.attempts})" if result.attempts > 1 else ""
            print(f"[{completed}/{len(units)}] {result.status.upper():7s} {unit.label}  "
                  f"({result.duration:.1f}s){attempt_note}")

        if result.status != "passed" and fail_fast:
            stop_starting.set()
        return result

    started = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = [result for result in pool.map(run_job, units) if result is not None]
    return results, time.time() - started


def _write_run_outputs(report_root: Path, run_dir: Path, results: List[Result],
                       store: ResultStore, info: Dict[str, str], history: Dict[str, float],
                       wall_seconds: float, workers: int, scheme: str) -> Path:
    """Write run artifacts, archive replaced latest files, and refresh latest copies."""
    write_html_report(store, run_dir / "validation_report.html")
    backup_report = backup_validation_report(report_root, run_dir / "validation_report.html", run_dir.name)
    store.write_json(run_dir / "validation_results.json")
    (run_dir / "bdd_index.html").write_text(bdd_index(results, run_dir, info), encoding="utf-8")
    (run_dir / "run_summary.json").write_text(json.dumps({
        "framework": NAME,
        "version": VERSION,
        "workers": workers,
        "scheme": scheme,
        "wall_seconds": wall_seconds,
        "units": [{**asdict(result), "unit": asdict(result.unit)} for result in results],
    }, indent=2), encoding="utf-8")

    junit_dir = run_dir / "junit"
    junit_dir.mkdir(exist_ok=True)
    for result in results:
        worker_junit = Path(result.worker_dir) / "junit"
        for report in worker_junit.glob("*.xml"):
            shutil.copy(report, junit_dir / f"{result.unit.id}__{report.name}")

    latest_reports = ("validation_report.html", "validation_results.json", "bdd_index.html")
    _archive_latest_reports(report_root, list(latest_reports), run_dir.name)
    for name in ("validation_report.html", "validation_results.json"):
        shutil.copy(run_dir / name, report_root / name)
    (report_root / "bdd_index.html").write_text(bdd_index(results, report_root, info), encoding="utf-8")
    (report_root / ".durations.json").write_text(json.dumps(history))
    return backup_report


def _allure_message(enabled: bool, results_dir: Path) -> str:
    """Generate Allure HTML when configured and return its user-facing location."""
    if not enabled:
        return ""
    config = allure_support.cfg()
    report_dir = BASE_DIR / config["report_dir"]
    if config["generate"] and allure_support.generate(results_dir, report_dir):
        return f"\nAllure report : {report_dir / 'index.html'}  (serve it: allure open {report_dir})"
    return (f"\nAllure results: {results_dir}  "
            f"(install the Allure CLI, then: allure serve {results_dir})")


# ------------------------------------------------------------------------------------ CLI
def parser() -> argparse.ArgumentParser:
    par = section("parallel")
    p = argparse.ArgumentParser(prog="run_tests.py", description=f"{NAME} parallel runner",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=(
        "examples:\n  python run_tests.py -w 4\n  python run_tests.py -w 8 --scheme scenario --retries 1 -t ui\n"
        "  python run_tests.py --allure                      # also write/generate the Allure report\n"
        "  python run_tests.py --shard 2/3                   # CI machine 2 of 3\n  python run_tests.py --list\n"))
    p.add_argument("paths", nargs="*", default=[FEATURES_DIR])
    p.add_argument("-w", "--workers", default=str(par["workers"]), help="processes or 'auto' (= min(4, CPUs))")
    p.add_argument("--scheme", choices=["feature", "scenario"], default=par["scheme"])
    p.add_argument("-t", "--tags", action="append", default=[], help="behave tag expression (repeat = AND, comma = OR)")
    p.add_argument("-D", "--define", action="append", default=[], metavar="KEY=VALUE")
    p.add_argument("--report-dir", default="reports")
    p.add_argument("--title", default="PyAssure Test Report")
    p.add_argument("--env", default="")
    p.add_argument("--retries", type=int, default=par["retries"], help="re-run a failed unit N times")
    p.add_argument("--timeout", type=int, default=None, help="kill a unit after N seconds")
    p.add_argument("--fail-fast", action="store_true")
    p.add_argument("--shard", metavar="I/N", help="run only shard I of N (split across CI machines)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--allure", action="store_true", help="force Allure on (overrides pyassure.yml)")
    g.add_argument("--no-allure", action="store_true", help="force Allure off")
    p.add_argument("--list", action="store_true")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    """Run selected Behave units, then merge and publish all run reports."""
    args = parser().parse_args(argv)
    os.chdir(BASE_DIR)
    sys.path.insert(0, str(BASE_DIR))  # so app.* session providers are importable
    tag_args = [f"--tags={t}" for t in args.tags]

    units = sorted(discover(args.paths, args.scheme, tag_args), key=lambda unit: unit.id)
    if args.shard:  # round-robin keeps shards balanced
        i, n = (int(x) for x in args.shard.split("/"))
        units = units[i - 1::n]
    if not units:
        print("No scenarios selected (check paths, --tags, --shard).", file=sys.stderr)
        return 2
    if args.list:
        print(f"{len(units)} unit(s) [{args.scheme}]:", *[f"  {u.id:45s} {u.label}" for u in units], sep="\n")
        return 0

    report_root = BASE_DIR / args.report_dir
    run_dir = report_root / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    history_file = report_root / ".durations.json"
    history: Dict[str, float] = json.loads(history_file.read_text()) if history_file.exists() else {}
    units.sort(key=lambda unit: -history.get(unit.id, 0))
    workers = _worker_count(args.workers, len(units))

    allure_on = bool(allure_support.cfg()["enabled"])
    if args.allure:
        allure_on = True
    elif args.no_allure:
        allure_on = False
    allure_results = run_dir / "allure-results"
    defines = [
        *args.define,
        f"report_title={args.title}",
        f"env={args.env}",
        f"allure={str(allure_on).lower()}",
        f"allure_dir={os.path.relpath(allure_results, BASE_DIR)}",
    ]

    print(BANNER, f"\nRunning {len(units)} unit(s) on {workers} worker(s) [{args.scheme}] -> {run_dir}\n", sep="")

    # Start shared services once; worker processes reuse them and never stop them.
    server = services.ensure()
    try:
        warm = section("sessions")["warmup"]
        if warm:
            from pyassure.web import runtime
            print(f"Warming up session(s): {', '.join(warm)}")
            runtime.warm_up(warm)
            runtime.shutdown()
        results, wall = _run_workers(
            units, run_dir, tag_args, defines, args.timeout, args.retries, workers, args.fail_fast)
    finally:
        services.stop(server)

    # Merge worker data and write the durable run artifacts plus latest copies.
    info = {"workers": str(workers), "scheme": args.scheme, "wall time": f"{wall:.1f}s"}
    if args.shard:
        info["shard"] = args.shard
    store = merge(results, args.title, args.env, info)
    history.update({result.unit.id: result.duration for result in results})
    backup_report = _write_run_outputs(
        report_root, run_dir, results, store, info, history, wall, workers, args.scheme)
    allure_msg = _allure_message(allure_on, allure_results)

    t = store.totals()
    print(f"\n{'=' * 72}\n{NAME}: {t['scenarios_passed']} scenario(s) passed, "
          f"{t['scenarios_failed']} failed | checkpoints {t['checkpoints_passed']} passed, "
          f"{t['checkpoints_failed']} failed, {t['checkpoints_warning']} warning")
    if wall:
        print(f"Wall time {wall:.1f}s (sum of units {sum(r.duration for r in results):.1f}s, "
              f"~{sum(r.duration for r in results) / wall:.1f}x speed-up)")
    print(f"Merged report : {report_root / 'validation_report.html'}\n"
          f"Backup report : {backup_report}\n"
          f"BDD reports   : {report_root / 'bdd_index.html'}{allure_msg}")
    failed = [result.unit.id for result in results if result.status != "passed"]
    if failed:
        print("Failed units  : " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
