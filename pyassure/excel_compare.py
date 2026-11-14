#!/usr/bin/env python3
"""
compare_excel.py
================

Compare two Excel workbooks (source vs target), sheet by sheet and cell by
cell, and write the outcome into a third "result" workbook.

USAGE
-----
    python compare_excel.py <source.xlsx> <target.xlsx> [-o result.xlsx] [-t TOL]

    positional arguments:
        source          Path to the SOURCE (baseline) workbook
        target          Path to the TARGET (workbook being checked) workbook

    optional arguments:
        -o, --output    Path of the result workbook
                        (default: comparison_result_<YYYYmmdd_HHMMSS>.xlsx)
        -t, --tolerance Absolute tolerance for numeric comparison (default: 0,
                        i.e. numbers must be exactly equal). Example: 0.001

RESULT WORKBOOK RULES
---------------------
The result sheet is a copy of the SOURCE sheet's layout: cell formatting (font,
fill, borders, alignment, number format), column widths, row heights, hidden
rows/columns, merged cells and frozen panes are all carried over.

For every cell position (union of the used ranges of both sheets):

  1. MATCH
        The cell shows the SOURCE value with the SOURCE formatting exactly
        as it was, no highlighting.

  2. MISMATCH and BOTH values are numeric
        The cell shows the text   source=<value> | target=<value> | diff=<source - target>
        and gets a RED fill (text wrapped).

  3. MISMATCH and the values are NOT both numeric (text, dates, blank vs
     value, number vs text, etc.)
        The cell shows the text   source=<value> | target=<value>
        and gets an ORANGE fill (text wrapped). A blank cell is shown as
        <blank>.

  In cases 2 and 3 the fill is replaced and the number format is reset to
  General (the cell holds text); the rest of the source formatting (font,
  borders, ...) is kept.

  4. A sheet that exists in only one workbook gets a placeholder sheet with
     a highlighted note in A1.

A leading sheet called "_Comparison_Summary" lists every sheet, its status
and the number of mismatching cells, plus a colour legend.

NOTES / ASSUMPTIONS
-------------------
* Sheets are matched by NAME (case-sensitive).
* Formula cells are compared by their last CACHED (calculated) value, i.e.
  what Excel last saved. If a workbook was produced by a tool that never
  calculated its formulas (e.g. openpyxl), open and save it in Excel first.
* Booleans are treated as non-numeric (True is not 1).
* Only .xlsx / .xlsm files are supported (not legacy .xls).
* Not carried over from the source: conditional formatting, images, charts,
  data validation, comments. Non-anchor cells of source merged ranges are
  not compared (only the top-left cell of a merged range is).

EXIT CODES
----------
    0 = workbooks are identical
    1 = differences were found
    2 = error (bad path, unreadable file, ...)

REQUIREMENTS
------------
    pip install openpyxl
"""

import argparse
import math
import os
import sys
from copy import copy
from datetime import datetime
from typing import Any, Dict, List, Tuple

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.cell.cell import MergedCell
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

# --------------------------------------------------------------------------- #
# Styling constants
# --------------------------------------------------------------------------- #
FONT_NAME = "Arial"
FILL_NUMERIC_DIFF = PatternFill("solid", start_color="FF9999", end_color="FF9999")  # red
FILL_TEXT_DIFF = PatternFill("solid", start_color="FFC000", end_color="FFC000")     # orange
FILL_HEADER = PatternFill("solid", start_color="D9D9D9", end_color="D9D9D9")        # grey
SUMMARY_SHEET_NAME = "_Comparison_Summary"
BLANK_TEXT = "<blank>"

# Statuses used in the summary sheet
STATUS_MATCH = "MATCH"
STATUS_DIFF = "DIFFERENCES"
STATUS_ONLY_SOURCE = "ONLY IN SOURCE"
STATUS_ONLY_TARGET = "ONLY IN TARGET"


# --------------------------------------------------------------------------- #
# Value helpers
# --------------------------------------------------------------------------- #
def is_blank(value: Any) -> bool:
    """Return True for None or an empty / whitespace-only string."""
    return value is None or (isinstance(value, str) and value.strip() == "")


def is_numeric(value: Any) -> bool:
    """Return True for int/float values (bool is deliberately excluded)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def values_match(src: Any, tgt: Any, tolerance: float) -> bool:
    """
    Decide whether two cell values are considered equal.

    * blank vs blank            -> equal
    * number vs number          -> equal if |src - tgt| <= tolerance
    * anything else             -> equal if Python equality holds
    """
    if is_blank(src) and is_blank(tgt):
        return True
    if is_numeric(src) and is_numeric(tgt):
        return math.isclose(src, tgt, rel_tol=0.0, abs_tol=tolerance) if tolerance \
            else src == tgt
    return src == tgt


def display(value: Any) -> str:
    """Human-readable text for a value, used inside 'source=... | target=...'."""
    if is_blank(value):
        return BLANK_TEXT
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return str(value)


def set_cell_value(cell, value: Any) -> None:
    """
    Write a value to a cell. Strings that start with '=' would otherwise be
    stored as formulas by openpyxl, so force them to be plain text.
    """
    cell.value = value
    if isinstance(value, str) and value.startswith("="):
        cell.data_type = "s"


def style_cell(cell, fill: PatternFill = None, wrap: bool = False) -> None:
    """Apply the standard font and optional fill / wrapping to a cell."""
    cell.font = Font(name=FONT_NAME, size=10)
    if fill is not None:
        cell.fill = fill
    if wrap:
        cell.alignment = Alignment(wrap_text=True, vertical="top")


# --------------------------------------------------------------------------- #
# Sheet-level comparison
# --------------------------------------------------------------------------- #
def copy_style(src_cell, out_cell) -> None:
    """Copy all formatting from a source cell to a result cell."""
    if src_cell.has_style:
        out_cell.font = copy(src_cell.font)
        out_cell.fill = copy(src_cell.fill)
        out_cell.border = copy(src_cell.border)
        out_cell.alignment = copy(src_cell.alignment)
        out_cell.protection = copy(src_cell.protection)
        out_cell.number_format = src_cell.number_format


def highlight(out_cell, fill: PatternFill, wrap: bool = False) -> None:
    """Replace only the fill of a (source-styled) cell; optionally wrap text."""
    out_cell.fill = fill
    if wrap:
        alignment = copy(out_cell.alignment)
        alignment.wrap_text = True
        alignment.vertical = alignment.vertical or "top"
        out_cell.alignment = alignment


def copy_sheet_layout(src_ws: Worksheet, tgt_ws: Worksheet, out_ws: Worksheet,
                      max_col: int, wrapped_rows: set) -> None:
    """
    Copy sheet-level layout from the source: column widths, row heights,
    hidden rows/columns, merged ranges, freeze panes and tab colour.
    Columns that exist only in the target fall back to the target's width.
    Rows holding wrapped mismatch text keep an automatic height so the text
    is not cut off.
    """
    for c in range(1, max_col + 1):
        letter = get_column_letter(c)
        for ws in (src_ws, tgt_ws):
            dim = ws.column_dimensions.get(letter)
            if dim is not None and dim.width:
                out_ws.column_dimensions[letter].width = dim.width
                out_ws.column_dimensions[letter].hidden = dim.hidden
                break
        else:
            out_ws.column_dimensions[letter].width = 14

    for r, dim in src_ws.row_dimensions.items():
        if r not in wrapped_rows and dim.height:
            out_ws.row_dimensions[r].height = dim.height
        if dim.hidden:
            out_ws.row_dimensions[r].hidden = True

    for merged in src_ws.merged_cells.ranges:
        out_ws.merge_cells(str(merged))

    out_ws.freeze_panes = src_ws.freeze_panes
    if src_ws.sheet_properties.tabColor is not None:
        out_ws.sheet_properties.tabColor = copy(src_ws.sheet_properties.tabColor)


def compare_sheets(src_ws: Worksheet, tgt_ws: Worksheet,
                   out_ws: Worksheet, tolerance: float) -> int:
    """
    Compare two worksheets cell by cell and write the result into `out_ws`,
    preserving the source sheet's formatting.

    Returns the number of mismatching cells.
    """
    max_row = max(src_ws.max_row, tgt_ws.max_row)
    max_col = max(src_ws.max_column, tgt_ws.max_column)
    mismatches = 0
    wrapped_rows = set()

    for r in range(1, max_row + 1):
        for c in range(1, max_col + 1):
            src_cell = src_ws.cell(row=r, column=c)
            tgt_cell = tgt_ws.cell(row=r, column=c)
            out_cell = out_ws.cell(row=r, column=c)

            # Always carry the source formatting over (even for empty cells,
            # so coloured / bordered blanks look like the original).
            copy_style(src_cell, out_cell)

            # Non-anchor cell of a merged range: nothing to compare.
            if isinstance(src_cell, MergedCell):
                continue

            src_val, tgt_val = src_cell.value, tgt_cell.value

            # Nothing in either sheet -> leave the value empty.
            if is_blank(src_val) and is_blank(tgt_val):
                continue

            if values_match(src_val, tgt_val, tolerance):
                # RULE 1: match -> source value, untouched source formatting.
                set_cell_value(out_cell, src_val)
                continue

            mismatches += 1
            if is_numeric(src_val) and is_numeric(tgt_val):
                # RULE 2: numeric mismatch -> "source=... | target=... | diff=...", red.
                # round() removes floating-point noise such as 0.30000000000000004
                diff = round(src_val - tgt_val, 10)
                text = f"source={src_val} | target={tgt_val} | diff={diff}"
                fill = FILL_NUMERIC_DIFF
            else:
                # RULE 3: any other mismatch -> "source=... | target=...", orange.
                text = f"source={display(src_val)} | target={display(tgt_val)}"
                fill = FILL_TEXT_DIFF

            # The cell now holds text, so reset the number format to General
            # so a source format (date, currency...) cannot garble it.
            set_cell_value(out_cell, text)
            out_cell.number_format = "General"
            highlight(out_cell, fill, wrap=True)
            wrapped_rows.add(r)

    copy_sheet_layout(src_ws, tgt_ws, out_ws, max_col, wrapped_rows)
    return mismatches


def write_placeholder_sheet(out_ws: Worksheet, status: str) -> None:
    """Create a one-cell sheet explaining that the sheet is missing on one side."""
    side = "SOURCE" if status == STATUS_ONLY_SOURCE else "TARGET"
    cell = out_ws["A1"]
    cell.value = f"Sheet exists only in the {side} workbook - nothing to compare."
    style_cell(cell, FILL_TEXT_DIFF)
    out_ws.column_dimensions["A"].width = 60


# --------------------------------------------------------------------------- #
# Summary sheet
# --------------------------------------------------------------------------- #
def write_summary(wb: Workbook, results: List[Tuple[str, str, int]],
                  src_path: str, tgt_path: str, tolerance: float) -> None:
    """
    Create the first sheet of the result workbook with an overview of the run.

    `results` is a list of (sheet_name, status, mismatch_count).
    """
    ws = wb.create_sheet(SUMMARY_SHEET_NAME, 0)
    bold = Font(name=FONT_NAME, size=10, bold=True)

    info = [
        ("Source file", src_path),
        ("Target file", tgt_path),
        ("Compared on", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ("Numeric tolerance", tolerance),
    ]
    for i, (label, value) in enumerate(info, start=1):
        ws.cell(row=i, column=1, value=label).font = bold
        cell = ws.cell(row=i, column=2)
        set_cell_value(cell, value)
        cell.font = Font(name=FONT_NAME, size=10)

    # Table of per-sheet results
    header_row = len(info) + 2
    for col, title in enumerate(["Sheet", "Status", "Mismatching cells"], start=1):
        cell = ws.cell(row=header_row, column=col, value=title)
        cell.font = bold
        cell.fill = FILL_HEADER

    for i, (name, status, count) in enumerate(results, start=header_row + 1):
        for col, value in enumerate([name, status, count], start=1):
            cell = ws.cell(row=i, column=col)
            set_cell_value(cell, value)
            cell.font = Font(name=FONT_NAME, size=10)
        if status != STATUS_MATCH:
            ws.cell(row=i, column=2).fill = FILL_TEXT_DIFF

    # Legend
    legend_row = header_row + len(results) + 3
    ws.cell(row=legend_row, column=1, value="Legend").font = bold
    legend = [
        (None, "No colour = values match (source value shown)"),
        (FILL_NUMERIC_DIFF, "Red = numeric mismatch, shows source=... | target=... | diff=(source - target)"),
        (FILL_TEXT_DIFF, "Orange = non-numeric mismatch, shows source=... | target=..."),
    ]
    for i, (fill, text) in enumerate(legend, start=legend_row + 1):
        swatch = ws.cell(row=i, column=1)
        if fill:
            swatch.fill = fill
        label = ws.cell(row=i, column=2, value=text)
        label.font = Font(name=FONT_NAME, size=10)

    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 70
    ws.column_dimensions["C"].width = 20


# --------------------------------------------------------------------------- #
# Workbook-level comparison
# --------------------------------------------------------------------------- #
def compare_workbooks(src_path: str, tgt_path: str, out_path: str,
                      tolerance: float = 0.0) -> Dict[str, int]:
    """
    Compare every sheet of two workbooks and save the result workbook.

    Sheets are matched by name. Order: all source sheets (in source order)
    followed by sheets that exist only in the target.

    Returns a dict {sheet_name: mismatch_count}.
    """
    # data_only=True -> read cached formula results instead of formula text.
    src_wb = load_workbook(src_path, data_only=True)
    tgt_wb = load_workbook(tgt_path, data_only=True)

    out_wb = Workbook()
    out_wb.remove(out_wb.active)  # drop the default empty sheet

    src_names = src_wb.sheetnames
    tgt_names = tgt_wb.sheetnames
    all_names = src_names + [n for n in tgt_names if n not in src_names]

    results: List[Tuple[str, str, int]] = []

    for name in all_names:
        out_ws = out_wb.create_sheet(name)

        if name not in tgt_names:
            write_placeholder_sheet(out_ws, STATUS_ONLY_SOURCE)
            results.append((name, STATUS_ONLY_SOURCE, 0))
        elif name not in src_names:
            write_placeholder_sheet(out_ws, STATUS_ONLY_TARGET)
            results.append((name, STATUS_ONLY_TARGET, 0))
        else:
            count = compare_sheets(src_wb[name], tgt_wb[name], out_ws, tolerance)
            results.append((name, STATUS_DIFF if count else STATUS_MATCH, count))

    write_summary(out_wb, results, src_path, tgt_path, tolerance)
    out_wb.save(out_path)
    return {name: count for name, _, count in results}


# --------------------------------------------------------------------------- #
# Command-line interface
# --------------------------------------------------------------------------- #
def parse_args() -> argparse.Namespace:
    """Define and parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Compare two Excel workbooks (all sheets) and write a result workbook.")
    parser.add_argument("source", help="Path to the SOURCE Excel file")
    parser.add_argument("target", help="Path to the TARGET Excel file")
    parser.add_argument("-o", "--output",
                        help="Path of the result Excel file "
                             "(default: comparison_result_<timestamp>.xlsx)")
    parser.add_argument("-t", "--tolerance", type=float, default=0.0,
                        help="Absolute tolerance for numeric comparison (default: 0)")
    return parser.parse_args()


def main() -> int:
    """Entry point. Returns the process exit code (0 same, 1 differs, 2 error)."""
    args = parse_args()

    for label, path in (("Source", args.source), ("Target", args.target)):
        if not os.path.isfile(path):
            print(f"ERROR: {label} file not found: {path}", file=sys.stderr)
            return 2
        if not path.lower().endswith((".xlsx", ".xlsm")):
            print(f"ERROR: {label} file must be .xlsx or .xlsm: {path}", file=sys.stderr)
            return 2

    out_path = args.output or \
        f"comparison_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

    try:
        results = compare_workbooks(args.source, args.target, out_path, args.tolerance)
    except Exception as exc:  # noqa: BLE001 - report any read/write failure cleanly
        print(f"ERROR: comparison failed: {exc}", file=sys.stderr)
        return 2

    total = sum(results.values())
    print(f"Result written to: {out_path}")
    for name, count in results.items():
        print(f"  {name}: {count} mismatching cell(s)")
    print(f"Total mismatching cells: {total}")
    return 1 if (total or _has_missing_sheets(args.source, args.target)) else 0


def _has_missing_sheets(src_path: str, tgt_path: str) -> bool:
    """True if the two workbooks do not contain the same set of sheet names."""
    src = set(load_workbook(src_path, read_only=True).sheetnames)
    tgt = set(load_workbook(tgt_path, read_only=True).sheetnames)
    return src != tgt


if __name__ == "__main__":
    sys.exit(main())