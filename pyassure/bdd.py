"""`checkpoint()` - the one function every assertion step goes through.

A checkpoint is a named validation with expected/actual values and optional evidence tables. It is
recorded in the PyAssure HTML report (and as an Allure attachment when Allure is on) whether it passes
or fails, so reports show WHAT was verified, not just what broke.
"""
from __future__ import annotations

from typing import Any, List, Optional

from pyassure.reporting import allure_support
from pyassure.reporting.store import FAILED, PASSED, WARNING, Table


def checkpoint(context, name: str, passed: bool, expected: Any = "", actual: Any = "", message: str = "",
               tables: Optional[List[Table]] = None, severity: str = "error", raise_on_fail: bool = True) -> bool:
    """Record a checkpoint and (optionally) fail the step.

    severity="warning"    a failure is recorded as WARNING and never fails the scenario.
    raise_on_fail=False   record only - composite steps record ALL checkpoints, then fail once
                          (behave itself stops at the first failing step).
    @continue_on_failure   checkpoint failures are deferred until after the scenario's steps.
    Returns True when the checkpoint passed (or was only a warning).
    """
    status = PASSED if passed else (WARNING if severity == "warning" else FAILED)
    context.store.checkpoint(name, status, expected, actual, message, tables or [])

    # Mirror into Allure: one text attachment per checkpoint, evidence tables as tab-separated rows.
    lines = [f"{status}: {name}", f"expected: {expected}", f"actual:   {actual}"] + ([message] if message else [])
    for t in tables or []:
        lines += ["", f"{t.title} ({t.total_rows} rows)", "\t".join(t.columns)] + ["\t".join(r) for r in t.rows[:50]]
    allure_support.attach(f"[{status}] {name}", "\n".join(lines))

    if status == FAILED:
        if getattr(context, "continue_on_failure", False):
            context.deferred_checkpoint_failures.append(name)
        elif raise_on_fail:
            raise AssertionError(f"{name}: expected {expected}; actual {actual}. {message}".strip())
    return status != FAILED
