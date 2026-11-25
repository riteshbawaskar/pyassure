"""Reusable steps for DATABASE / FILE testing: dataset comparison, data validation, Excel cell compare.

Import in app/features/steps/framework_steps.py. Full step list: docs/04-writing-scenarios.md

  Data comparison     Given the compare config "app/datasets/compare/x.yml"
                      When  I run the data comparison
                      Then  the data comparison should pass              (or the granular steps below)
  Data validation     Given the validation config "app/datasets/validation/x.yml"
                      When  I run the data validations
                      Then  all validations should pass
  Excel cell compare  Given the excel compare config "app/datasets/cell_compare/x.yml"
                      When  I run the excel cell comparison
                      Then  there should be no cell differences

Every Then step asserts through `checkpoint()`, so each validation shows in the reports with expected
vs actual values and evidence (missing keys, mismatching values, invalid rows, differing cells).
"""
import math
import os
import re
import time
from datetime import datetime
from pathlib import Path

from behave import given, then, when

from pyassure.bdd import checkpoint
from pyassure.config import BASE_DIR, ConfigError, load_yaml, resolve_path, settings, substitute_params
from pyassure.data.compare import DatasetComparator
from pyassure.data.connectors import fetch_dataset
from pyassure.data.excel_cells import ExcelCellComparator
from pyassure.data.validate import DataValidator
from pyassure.excel_compare import compare_workbooks
from pyassure.reporting.store import Table


def _load_feature_config(context, path):
    """Load a config, applying an optional two-column name/value step table."""
    cfg = load_yaml(path)
    params = settings().get("parameters", {})
    if not isinstance(params, dict):
        raise ConfigError('The "parameters" setting must be a mapping of names to values')
    configured_params = {}
    for name, value in params.items():
        if not isinstance(name, str) or not name:
            raise ConfigError("Global config parameter names must be non-empty strings")
        if not isinstance(value, (str, int, float, bool)):
            raise ConfigError(f"Global config parameter '{name}' must have a scalar value")
        configured_params[name] = str(value)
    params = configured_params

    table = getattr(context, "table", None)
    if table is not None:
        headings = [str(heading).strip().lower() for heading in table.headings]
        if sorted(headings) != ["name", "value"]:
            raise ConfigError('Config parameter table must have "name" and "value" columns')

        feature_params = {}
        for row in table.rows:
            cells = dict(zip(headings, row.cells))
            name = cells["name"].strip()
            if not name:
                raise ConfigError("Config parameter names cannot be empty")
            if name in feature_params:
                raise ConfigError(f"Config parameter '{name}' is defined more than once")
            feature_params[name] = cells["value"]
        params.update(feature_params)
        for name, value in feature_params.items():
            context.store.info(f"Parameter: {name}", value)

    return substitute_params(cfg, params) if params else cfg


# ====================================================================================================
# DATA COMPARISON (primary-key based)
# ====================================================================================================
def _tbl(context, title, df):
    """Evidence table for the report (empty list when there is nothing to show)."""
    limit = context.comparator.opts.max_report_rows
    return [Table.from_df(title, df, limit)] if not df.empty else []


def _field_comparison_summary(context):
    """Report each compared field with its mismatch count, including fields that passed."""
    result = context.result
    rows = [[field, "Pass" if count == 0 else "Fail", str(count)]
            for field, count in result.column_mismatch_counts.items()]
    if not rows:
        return []
    limit = context.comparator.opts.max_report_rows
    return [Table("Field comparison summary", ["FIELD", "RESULT", "MISMATCHES"],
                  rows[:limit], len(rows))]


# ------------------------------------------------------------------------- Given / When
@given('the compare config "{path}"')
def step_load_compare_config(context, path):
    context.compare_cfg = _load_feature_config(context, path)
    context.store.info("Comparison", context.compare_cfg.get("name", path))
    context.store.info("Config", path)


@when("I run the data comparison")
def step_run_comparison(context):
    cfg = context.compare_cfg
    t0 = time.time()
    # Each endpoint can be any connector type (snowflake, oracle, databricks, excel, web_table ...)
    source = fetch_dataset(cfg["source"])
    target = fetch_dataset(cfg["target"])
    context.comparator = DatasetComparator(cfg)
    context.result = context.comparator.compare(source, target)

    # "Facts" shown at the top of the scenario in the report.
    r = context.result
    for key, value in (("Primary key", ", ".join(r.primary_key)),
                       ("Fields compared", f"{len(r.compared_fields)}"),
                       ("Source rows", r.source_count), ("Target rows", r.target_count),
                       ("Matched rows", r.matched_rows), ("Rows with differences", r.mismatched_rows),
                       ("Fetch+compare time", f"{time.time() - t0:.2f}s")):
        context.store.info(key, value)


# ------------------------------------------------------------------------------- Then
@then("the source and target row counts should match")
def step_counts(context):
    r = context.result
    checkpoint(context, "Row counts match", r.source_count == r.target_count,
               f"{r.source_count} (source)", f"{r.target_count} (target)")


@then("there should be no records missing in the target")
def step_missing_target(context):
    df = context.result.missing_in_target
    checkpoint(context, "No records missing in target", df.empty, "0", len(df),
               "Keys present in source but absent in target", _tbl(context, "Missing in target", df))


@then("there should be no records missing in the source")
def step_missing_source(context):
    df = context.result.missing_in_source
    checkpoint(context, "No records missing in source", df.empty, "0", len(df),
               "Keys present in target but absent in source", _tbl(context, "Missing in source", df))


@then("there should be no duplicate primary keys in the {side}")
def step_duplicates(context, side):
    # {side} is "source" or "target"
    df = context.result.duplicates_source if side == "source" else context.result.duplicates_target
    checkpoint(context, f"No duplicate primary keys in {side}", df.empty, "0", len(df),
               tables=_tbl(context, f"Duplicate keys in {side}", df))


@then("the source and target should have the same columns")
def step_same_columns(context):
    r = context.result
    only = {"only in source": r.columns_only_in_source, "only in target": r.columns_only_in_target}
    checkpoint(context, "Same column set", not (r.columns_only_in_source or r.columns_only_in_target),
               "identical columns", "; ".join(f"{k}: {v}" for k, v in only.items() if v) or "identical")


@then("there should be no field mismatches")
def step_no_mismatches(context):
    r = context.result
    tables = _field_comparison_summary(context)
    if r.mismatched_rows:
        tables += _tbl(context, "Field mismatches", r.mismatches)
    checkpoint(context, "No field mismatches", r.mismatched_rows == 0, "0 rows with differences",
               f"{r.mismatched_rows} of {r.common_rows} rows", tables=tables)


@then('the field "{field}" should have no mismatches')
def step_field_no_mismatch(context, field):
    r, col = context.result, field.strip().upper()
    if col not in r.column_mismatch_counts:      # field was not part of the comparison
        checkpoint(context, f"Field {col} has no mismatches", False, "field compared",
                   "field not compared", f"Compared fields: {r.compared_fields}")
        return
    n = r.column_mismatch_counts[col]
    sub = r.mismatches[r.mismatches["COLUMN"] == col]
    checkpoint(context, f"Field {col} has no mismatches", n == 0, "0", n,
               tables=_tbl(context, f"{col} mismatches", sub))


@then("the mismatch percentage should be at most {pct:g}")
def step_mismatch_pct(context, pct):
    # Percentage of COMMON rows that have at least one differing field.
    r = context.result
    checkpoint(context, f"Mismatch rate <= {pct:g}%", r.mismatch_pct <= pct, f"<= {pct:g}%",
               f"{r.mismatch_pct:.2f}%",
               tables=_field_comparison_summary(context) + _tbl(context, "Field mismatches", r.mismatches))


@then("the data comparison should pass")
def step_overall(context):
    """Composite assertion: records EVERY checkpoint, then fails once if any failed.

    behave stops a scenario at the first failing step, so a chain of Then/And steps reports only
    the first problem. This single step reports them all in one run.
    """
    r, ok = context.result, []
    ok.append(checkpoint(context, "Row counts match", r.source_count == r.target_count,
                         r.source_count, r.target_count, raise_on_fail=False))
    ok.append(checkpoint(context, "No records missing in target", r.missing_in_target.empty, "0",
                         len(r.missing_in_target), tables=_tbl(context, "Missing in target", r.missing_in_target),
                         raise_on_fail=False))
    ok.append(checkpoint(context, "No records missing in source", r.missing_in_source.empty, "0",
                         len(r.missing_in_source), tables=_tbl(context, "Missing in source", r.missing_in_source),
                         raise_on_fail=False))
    dups = len(r.duplicates_source) + len(r.duplicates_target)
    ok.append(checkpoint(context, "No duplicate primary keys", dups == 0, "0", dups, raise_on_fail=False))
    ok.append(checkpoint(context, "No field mismatches", r.mismatched_rows == 0, "0 rows with differences",
                         f"{r.mismatched_rows} of {r.common_rows} rows",
                         tables=_field_comparison_summary(context)
                         + _tbl(context, "Field mismatches", r.mismatches), raise_on_fail=False))
    if not all(ok) and not getattr(context, "continue_on_failure", False):
        raise AssertionError("One or more comparison checkpoints failed - see report")

# ====================================================================================================
# DATA VALIDATION (sanity checks on one dataset)
# ====================================================================================================
# Keys of a validation config that describe the checks, not the connection.
_NON_CONN = {"query", "query_file", "table", "checks", "name", "description"}


def _record(context, outcomes):
    """Write outcomes as checkpoints; fail the step if any error-severity check failed.

    All outcomes are recorded BEFORE failing, so the report shows every problem at once.
    Warning-severity outcomes are recorded but never make the step fail.
    """
    flags = []
    for o in outcomes:
        if o.sample is not None:
            title = "SQL query results" if o.check_type == "sql_report" else "Sample of invalid rows"
            tables = [Table.from_df(title, o.sample, context.validator.max_sample_rows)]
        else:
            tables = []
        flags.append(checkpoint(context, o.label, o.passed, o.expected, o.actual, o.message, tables,
                                severity=o.severity, raise_on_fail=False))
    if not all(flags) and not getattr(context, "continue_on_failure", False):
        raise AssertionError(f"{flags.count(False)} validation(s) failed - see report")


@given('the validation config "{path}"')
def step_load_validation_config(context, path):
    context.validation_cfg = _load_feature_config(context, path)
    context.store.info("Validation", context.validation_cfg.get("name", path))
    context.store.info("Config", path)


@when("I run the data validations")
def step_run_validations(context):
    cfg = context.validation_cfg
    df = fetch_dataset(cfg)                      # the config's top level IS the endpoint

    def sql_runner(spec):
        """Lets a `type: sql` check run its own query on the same (or an overridden) connection."""
        endpoint = {k: v for k, v in cfg.items() if k not in _NON_CONN}
        if spec.get("connection"):
            endpoint["connection"] = spec["connection"]
        endpoint["query"] = spec["query"]
        return fetch_dataset(endpoint)

    context.validator = DataValidator(cfg, sql_runner, cfg.get("max_report_rows", 200))
    context.outcomes = context.validator.run(df)
    context.store.info("Rows validated", len(df))
    context.store.info("Checks executed", len(context.outcomes))


@then("all validations should pass")
def step_all_pass(context):
    _record(context, context.outcomes)


@then('all "{check_type}" validations should pass')
def step_type_pass(context, check_type):
    selected = [o for o in context.outcomes if o.check_type == check_type]
    assert selected, f"No '{check_type}' checks are defined in the validation config"
    _record(context, selected)


@then('the validation "{name}" should pass')
def step_named_pass(context, name):
    # Matches either the check's name ("no_orphan_orders") or its full label ("sql: no_orphan_orders").
    selected = [o for o in context.outcomes if name in (o.name, o.label)]
    assert selected, f"No validation named '{name}' found"
    _record(context, selected)

# ====================================================================================================
# EXCEL CELL-TO-CELL COMPARISON
# ====================================================================================================
def _diff_table(context, title, diffs):
    """Evidence table (sheet, cell, source value, target value), capped for report size."""
    if not diffs:
        return []
    limit = context.comparator.opts.max_report_rows
    rows = [[d["sheet"], d["cell"], d["source"], d["target"]] for d in diffs]
    return [Table(title, ["SHEET", "CELL", "SOURCE", "TARGET"],
                  [[("NULL" if v is None else str(v)) for v in r] for r in rows[:limit]], len(rows))]


@given('the excel compare config "{path}"')
def step_load(context, path):
    context.cell_cfg = _load_feature_config(context, path)
    context.store.info("Comparison", context.cell_cfg.get("name", path))
    context.store.info("Source", context.cell_cfg["source"]["path"])
    context.store.info("Target", context.cell_cfg["target"]["path"])


@when("I run the excel cell comparison")
def step_run(context):
    t0 = time.time()
    context.comparator = ExcelCellComparator(context.cell_cfg)
    context.cell_result = context.comparator.compare()
    r = context.cell_result
    context.store.info("Sheets compared", ", ".join(s.label for s in r.sheets) or "none")
    context.store.info("Cells compared", r.cells_compared)
    context.store.info("Cells different", r.total_diffs)
    context.store.info("Compare time", f"{time.time() - t0:.2f}s")


@when("I generate the Excel comparison workbook")
def step_generate_excel_comparison_workbook(context):
    cfg = context.cell_cfg
    source = resolve_path(cfg["source"]["path"])
    target = resolve_path(cfg["target"]["path"])
    for label, path in (("Source", source), ("Target", target)):
        if not path.is_file():
            raise ConfigError(f"{label} Excel workbook not found: {path}")
        if path.suffix.lower() not in (".xlsx", ".xlsm"):
            raise ConfigError(f"{label} Excel workbook must be .xlsx or .xlsm: {path}")

    options = cfg.get("options", {})
    if not isinstance(options, dict):
        raise ConfigError('Excel compare "options" must be a mapping')
    tolerance = options.get("numeric_tolerance", 0.0)
    if (isinstance(tolerance, bool) or not isinstance(tolerance, (int, float))
            or not math.isfinite(tolerance) or tolerance < 0):
        raise ConfigError('Excel compare "options.numeric_tolerance" must be a non-negative number')

    scenario = (re.sub(r"[^A-Za-z0-9_-]+", "_", context.scenario.name).strip("_") or "comparison")[:80]
    filename = f"excel_comparison_{scenario}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.xlsx"
    output = Path(context.report_dir) / filename
    output.parent.mkdir(parents=True, exist_ok=True)
    results = compare_workbooks(str(source), str(target), str(output), float(tolerance))

    context.excel_compare_workbook = output
    context.store.info("Workbook mismatches", sum(results.values()))
    try:
        report_path = os.path.relpath(output, BASE_DIR).replace(os.sep, "/")
    except ValueError:
        report_path = output.as_uri()
    context.store.link_file(
        "Download Excel comparison workbook",
        report_path,
    )


@then("both workbooks should contain the same sheets")
def step_same_sheets(context):
    r = context.cell_result
    problems = [f"missing in target: {r.missing_in_target}"] if r.missing_in_target else []
    problems += [f"missing in source: {r.missing_in_source}"] if r.missing_in_source else []
    checkpoint(context, "Same sheets in both workbooks", not problems, "identical sheet set",
               "; ".join(problems) or "identical sheet set")


@then("there should be no cell differences")
def step_no_diffs(context):
    r = context.cell_result
    all_diffs = [d for s in r.sheets for d in s.diffs]
    checkpoint(context, "No cell differences", r.total_diffs == 0, "0 cells different",
               f"{r.total_diffs} of {r.cells_compared} cells",
               tables=_diff_table(context, "Cell differences", all_diffs))


@then('the sheet "{sheet}" should have no cell differences')
def step_sheet_no_diffs(context, sheet):
    s = context.cell_result.sheet(sheet)
    if s is None:   # the sheet was not part of the comparison (missing or filtered out)
        checkpoint(context, f"Sheet {sheet} has no cell differences", False, "sheet compared", "sheet not compared")
        return
    checkpoint(context, f"Sheet {sheet} has no cell differences", s.diff_count == 0, "0",
               f"{s.diff_count} of {s.cells_compared} cells",
               tables=_diff_table(context, f"{sheet} differences", s.diffs))


@then('the cell "{cell}" in sheet "{sheet}" should match')
def step_cell_match(context, cell, sheet):
    s = context.cell_result.sheet(sheet)
    assert s is not None, f"Sheet '{sheet}' was not compared"
    hit = [d for d in s.diffs if d["cell"].upper() == cell.upper()]
    checkpoint(context, f"{sheet}!{cell.upper()} matches", not hit, "source == target",
               f"{hit[0]['source']!r} vs {hit[0]['target']!r}" if hit else "equal")
