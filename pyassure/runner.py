"""Parallel runner:  python run_tests.py -w 4          (installed command: pyassure-run)

behave is single-process, so PyAssure parallelises with several INDEPENDENT behave processes, each with its
own report folder, and merges the results afterwards:

    discover units -> [shard] -> start app server -> warm up sessions -> N workers -> merge reports

Schemes   feature  = one unit per .feature file (default, fewest start-ups)
          scenario = one unit per scenario/outline row (best balance for many slow scenarios)

Playwright-specific design (see docs/05-parallel-execution.md)
  * processes, not threads: Playwright's sync API allows one driver per process; each worker owns its
    browser and gives every scenario a fresh context -> perfect isolation, no locking in tests.
  * login ONCE: `sessions.warmup` in pyassure.yml creates storageState files before the workers start; they
    all reuse them (a file lock protects against races if a worker needs a session that was not warmed up).
  * app server ONCE: `web_server` is started by the runner; workers find it running and leave it alone.
  * --shard i/n splits the units across CI machines (each machine runs `--shard k/n`).
  * Allure: all workers write into one shared results folder; the runner builds the HTML when the CLI exists.

Outputs under <report-dir>/runs/<timestamp>/ (latest copies in <report-dir>/):
  validation_report.html (merged) | validation_results.json | bdd_index.html | junit/ | allure-results/ |
  run_summary.json | workers/<unit>/ (that worker's reports + behave.log)
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
from typing import Dict, List, Optional

from pyassure import services
from pyassure.branding import BANNER, NAME, TAGLINE, VERSION
from pyassure.config import BASE_DIR, section
from pyassure.reporting import allure_support
from pyassure.reporting.html import write_html_report
from pyassure.reporting.store import ResultStore, ScenarioRecord

BDD_FORMATTER = "pyassure.reporting.bdd_html:BrandedHTMLFormatter"
FEATURES_DIR = "app/features"


@dataclass
class Unit:
    id: str          # unique + filesystem safe (worker folder name)
    target: str      # handed to behave: "app/features/a.feature" or "...:14"
    label: str       # shown to humans


@dataclass
class Result:
    unit: Unit
    status: str = "pending"            # passed | failed | timeout
    duration: float = 0.0
    attempts: int = 1
    worker_dir: str = ""
    has_results: bool = False
    log_tail: List[str] = field(default_factory=list)


def _slug(t: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", t).strip("-")[:70] or "unit"


# ------------------------------------------------------------------------------ discovery
def discover(paths: List[str], scheme: str, tag_args: List[str]) -> List[Unit]:
    """Find units using behave's own tag rules (behave.ini default_tags, overridden by --tags)."""
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
def run_unit(unit: Unit, run_dir: Path, tag_args: List[str], defines: List[str],
             timeout: Optional[int], retries: int) -> Result:
    """One behave subprocess (+ retries). Everything it writes goes to its own folder."""
    wdir = run_dir / "workers" / unit.id
    wdir.mkdir(parents=True, exist_ok=True)
    res = Result(unit, worker_dir=str(wdir))
    cmd = [sys.executable, "-m", "behave", unit.target, "--no-color",
           "-f", BDD_FORMATTER, "-o", str(wdir / "behave_report.html"),     # standard BDD report
           "-f", "plain",                                                   # console text -> behave.log
           "--junit", "--junit-directory", str(wdir / "junit"),
           "-D", f"report_dir={os.path.relpath(wdir, BASE_DIR)}", "-D", f"worker={unit.id}",
           *[a for d in defines for a in ("-D", d)], *tag_args]
    t0 = time.time()
    for attempt in range(1, retries + 2):                                   # 1 try + N retries
        res.attempts = attempt
        try:
            with open(wdir / "behave.log", "w", encoding="utf-8") as log:
                rc = subprocess.run(cmd, cwd=BASE_DIR, stdout=log, stderr=subprocess.STDOUT, timeout=timeout,
                                    env={**os.environ, "PYTHONPATH": str(BASE_DIR)}).returncode
            res.status = "passed" if rc == 0 else "failed"
        except subprocess.TimeoutExpired:
            res.status = "timeout"
        if res.status == "passed":
            break
    res.duration = time.time() - t0
    res.has_results = (wdir / "validation_results.json").exists()
    if res.status != "passed":
        res.log_tail = (wdir / "behave.log").read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
    return res


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
    rows = "".join(
        f'<tr><td class="{"ok" if r.status == "passed" else "bad"}">{r.status.upper()}</td>'
        f'<td><a href="{e(os.path.relpath(Path(r.worker_dir) / "behave_report.html", where).replace(os.sep, "/"))}">'
        f'{e(r.unit.label)}</a></td><td>{r.duration:.1f}s</td><td>{r.attempts}</td></tr>'
        for r in sorted(results, key=lambda x: x.unit.id))
    return (f'<!doctype html><meta charset="utf-8"><title>{e(NAME)} - BDD reports</title><style>body{{font:13px system-ui;'
            f'margin:16px}}td,th{{padding:3px 12px;text-align:left;border-bottom:1px solid #ddd}}.ok{{color:#15803d;font-weight:600}}'
            f'.bad{{color:#b91c1c;font-weight:600}}</style><h2>Py<b style="color:#15803d">Assure</b> v{e(VERSION)} &mdash; standard BDD reports</h2>'
            f'<p>{e(TAGLINE)}<br>{" &middot; ".join(f"{e(k)}: {e(v)}" for k, v in info.items())}</p>'
            f'<table><tr><th>Status</th><th>Unit</th><th>Time</th><th>Attempts</th></tr>{rows}</table>')


def _archive_latest_reports(report_root: Path, names: List[str], timestamp: str) -> None:
    """Move existing latest-run report files under log/ before replacing them."""
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
    args = parser().parse_args(argv)
    os.chdir(BASE_DIR)
    sys.path.insert(0, str(BASE_DIR))                       # so `app.*` (session providers) is importable
    tag_args = [f"--tags={t}" for t in args.tags]
    workers = max(1, min(4, os.cpu_count() or 1)) if args.workers == "auto" else max(1, int(args.workers))

    units = sorted(discover(args.paths, args.scheme, tag_args), key=lambda u: u.id)   # deterministic order
    if args.shard:                                           # round-robin keeps shards balanced
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
    hist_file = report_root / ".durations.json"             # slowest units first = shorter wall time
    hist: Dict[str, float] = json.loads(hist_file.read_text()) if hist_file.exists() else {}
    units.sort(key=lambda u: -hist.get(u.id, 0))
    workers = min(workers, len(units))

    allure_on = True if args.allure else False if args.no_allure else bool(allure_support.cfg()["enabled"])
    allure_dir = run_dir / "allure-results"
    defines = args.define + [f"report_title={args.title}", f"env={args.env}", f"allure={str(allure_on).lower()}",
                             f"allure_dir={os.path.relpath(allure_dir, BASE_DIR)}"]

    print(BANNER, f"\nRunning {len(units)} unit(s) on {workers} worker(s) [{args.scheme}] -> {run_dir}\n", sep="")

    # ---- once-per-run preparation, shared by all workers
    server = services.ensure()                              # app under test
    try:
        warm = section("sessions")["warmup"]
        if warm:
            from pyassure.web import runtime
            print(f"Warming up session(s): {', '.join(warm)}")
            runtime.warm_up(warm)                            # login once -> storageState files
            runtime.shutdown()

        lock, done, stop = threading.Lock(), [0], threading.Event()

        def job(u: Unit) -> Optional[Result]:
            if stop.is_set():
                return None
            r = run_unit(u, run_dir, tag_args, defines, args.timeout, args.retries)
            with lock:                                       # keep progress lines un-interleaved
                done[0] += 1
                print(f"[{done[0]}/{len(units)}] {r.status.upper():7s} {u.label}  ({r.duration:.1f}s)"
                      + (f" (attempt {r.attempts})" if r.attempts > 1 else ""))
            if r.status != "passed" and args.fail_fast:
                stop.set()
            return r

        t0 = time.time()
        with ThreadPoolExecutor(max_workers=workers) as pool:   # threads only supervise the subprocesses
            results = [r for r in pool.map(job, units) if r]
        wall = time.time() - t0
    finally:
        services.stop(server)

    # ---- merge + write outputs
    info = {"workers": str(workers), "scheme": args.scheme, "wall time": f"{wall:.1f}s"}
    if args.shard:
        info["shard"] = args.shard
    store = merge(results, args.title, args.env, info)
    write_html_report(store, run_dir / "validation_report.html")
    store.write_json(run_dir / "validation_results.json")
    (run_dir / "bdd_index.html").write_text(bdd_index(results, run_dir, info), encoding="utf-8")
    (run_dir / "run_summary.json").write_text(json.dumps({
        "framework": NAME, "version": VERSION, "workers": workers, "scheme": args.scheme, "wall_seconds": wall,
        "units": [{**asdict(r), "unit": asdict(r.unit)} for r in results]}, indent=2), encoding="utf-8")
    (run_dir / "junit").mkdir(exist_ok=True)
    for r in results:
        for x in (Path(r.worker_dir) / "junit").glob("*.xml"):
            shutil.copy(x, run_dir / "junit" / f"{r.unit.id}__{x.name}")
    latest_reports = ("validation_report.html", "validation_results.json", "bdd_index.html")
    _archive_latest_reports(report_root, list(latest_reports), run_dir.name)
    for name in ("validation_report.html", "validation_results.json"):           # latest-run copies
        shutil.copy(run_dir / name, report_root / name)
    (report_root / "bdd_index.html").write_text(bdd_index(results, report_root, info), encoding="utf-8")
    hist_file.write_text(json.dumps({**hist, **{r.unit.id: r.duration for r in results}}))

    allure_msg = ""
    if allure_on:
        report = BASE_DIR / allure_support.cfg()["report_dir"]
        if allure_support.cfg()["generate"] and allure_support.generate(allure_dir, report):
            allure_msg = f"\nAllure report : {report / 'index.html'}  (serve it: allure open {report})"
        else:
            allure_msg = f"\nAllure results: {allure_dir}  (install the Allure CLI, then: allure serve {allure_dir})"

    t = store.totals()
    print(f"\n{'=' * 72}\n{NAME}: {t['scenarios_passed']} scenario(s) passed, {t['scenarios_failed']} failed | checkpoints "
          f"{t['checkpoints_passed']} passed, {t['checkpoints_failed']} failed, {t['checkpoints_warning']} warning")
    if wall:
        print(f"Wall time {wall:.1f}s (sum of units {sum(r.duration for r in results):.1f}s, "
              f"~{sum(r.duration for r in results) / wall:.1f}x speed-up)")
    print(f"Merged report : {report_root / 'validation_report.html'}\nBDD reports   : {report_root / 'bdd_index.html'}{allure_msg}")
    failed = [r.unit.id for r in results if r.status != "passed"]
    if failed:
        print("Failed units  : " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
