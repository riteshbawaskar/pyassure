"""Self-tests for PyAssure's own building blocks (run with:  pytest -q).

These do not need Snowflake/Oracle/Databricks or a browser; they cover the logic that every
scenario relies on: encryption, config handling, comparison rules, validations, table parsing,
Excel cell comparison, unit discovery and report merging.
"""
import json

import pandas as pd
import pytest
import yaml
from cryptography.fernet import Fernet

from pyassure.data.compare import DatasetComparator
from pyassure.data.excel_cells import ExcelCellComparator
from pyassure.config import ConfigError, substitute_params
from pyassure.data import connectors as factory
from pyassure.reporting.store import PASSED, ResultStore
from pyassure.runner import discover
from pyassure import secrets as crypto
from pyassure.web.pages import parse_html_table
from pyassure.data.validate import DataValidator


@pytest.fixture()
def key(monkeypatch):
    """A throw-away master key supplied through the environment."""
    k = Fernet.generate_key().decode()
    monkeypatch.setenv(crypto.ENV_KEY, k)
    return k


# ----------------------------------------------------------------------- encryption
def test_encrypt_roundtrip_and_randomised_tokens(key):
    t1, t2 = crypto.encrypt_value("s3cret"), crypto.encrypt_value("s3cret")
    assert t1 != t2 and t1.startswith("ENC(")          # random IV: same input, different token
    assert crypto.decrypt_value(t1) == "s3cret"
    assert crypto.decrypt_value("plain") == "plain"    # non-ENC values pass through


def test_wrong_key_is_rejected(key, monkeypatch):
    token = crypto.encrypt_value("x")
    monkeypatch.setenv(crypto.ENV_KEY, Fernet.generate_key().decode())
    with pytest.raises(crypto.SecurityError):
        crypto.decrypt_value(token)


def test_decrypt_tree_is_recursive_and_lazy(key):
    tree = {"a": {"password": crypto.encrypt_value("pw")}, "b": [crypto.encrypt_value("x"), 5]}
    assert crypto.decrypt_tree(tree) == {"a": {"password": "pw"}, "b": ["x", 5]}


def test_no_key_needed_without_encrypted_values(monkeypatch, tmp_path):
    monkeypatch.delenv(crypto.ENV_KEY, raising=False)
    monkeypatch.setenv(crypto.ENV_KEY_FILE, str(tmp_path / "missing.key"))
    assert crypto.decrypt_tree({"password": "plain"}) == {"password": "plain"}
    with pytest.raises(crypto.SecurityError):
        crypto.decrypt_tree({"password": "ENC(abc)"})   # key required only when needed


def test_encrypt_config_file_keeps_comments_and_skips_env(key, tmp_path):
    f = tmp_path / "c.yml"
    f.write_text("a:\n  password: tiger  # keep me\n  token: ${TOK}\n  user: scott\n")
    assert len(crypto.encrypt_config_file(f)) == 1
    text = f.read_text()
    assert "# keep me" in text and "${TOK}" in text and "tiger" not in text
    assert yaml.safe_load(text.split("#")[0] + "")  # still valid YAML
    assert crypto.decrypt_tree(yaml.safe_load(f.read_text()))["a"]["password"] == "tiger"


def test_factory_decrypts_only_when_building_connector(key, tmp_path):
    ep = {"type": "sqlite", "path": str(tmp_path / "x.db"), "password": crypto.encrypt_value("pw1")}
    assert ep["password"].startswith("ENC(")                     # config still encrypted
    assert factory.build_connector(ep).config["password"] == "pw1"   # decrypted in memory


def test_continue_on_failure_runs_later_steps_and_fails_scenario(monkeypatch):
    from types import SimpleNamespace
    from pyassure import hooks
    from pyassure.bdd import checkpoint

    class Scenario:
        feature = SimpleNamespace(name="Feature")
        name = "deferred assertions"
        tags = ["continue_on_failure"]

        def __init__(self):
            self.status = SimpleNamespace(name="passed")

        def set_status(self, status):
            self.status = SimpleNamespace(name=status)

    scenario = Scenario()
    context = SimpleNamespace(store=ResultStore("test"), worker="")
    ran = []
    monkeypatch.setattr(hooks.runtime, "end_scenario", lambda *args: [])
    monkeypatch.setattr(hooks.api, "close_all", lambda context: None)

    hooks.before_scenario(context, scenario)
    assert not checkpoint(context, "first check", False)
    ran.append("next step")
    assert checkpoint(context, "second check", True)
    assert not checkpoint(context, "composite check", False, raise_on_fail=False)
    hooks.after_scenario(context, scenario)

    assert ran == ["next step"]
    assert scenario.status.name == "failed"
    assert context.store.scenarios[0].status == "failed"
    assert context.store.scenarios[0].count("FAILED") == 2

    with pytest.raises(AssertionError):
        checkpoint(SimpleNamespace(store=ResultStore()), "immediate check", False)


@pytest.mark.parametrize("use_arrow", [False, True])
def test_snowflake_executes_all_statements_and_returns_last_result(use_arrow):
    class Cursor:
        def __init__(self):
            self.results = [
                ([("status",)], [("Statement executed successfully.",)]),
                ([("ID",)], [(42,)]),
            ]
            self.index = 0

        @property
        def description(self):
            return self.results[self.index][0]

        def execute(self, query, num_statements=None):
            self.query = query
            self.num_statements = num_statements

        def fetchall(self):
            return self.results[self.index][1]

        def fetch_pandas_all(self):
            return pd.DataFrame({"ID": [42]})

        def nextset(self):
            self.index += 1
            return self.index < len(self.results)

        def close(self):
            pass

    cursor = Cursor()
    connector = factory.SnowflakeConnector({"use_arrow": use_arrow})
    connector._conn = type("Connection", (), {"cursor": lambda self: cursor})()

    result = connector.fetch_df("SET cutoff = 1; WITH rows AS (SELECT $cutoff AS ID) SELECT ID FROM rows")

    assert cursor.num_statements == 0
    assert result.to_dict("records") == [{"ID": 42}]


# --------------------------------------------------------------------- comparison
def _cmp(src, tgt, **cfg):
    base = {"primary_key": ["ID"], "compare_fields": "all"}
    return DatasetComparator({**base, **cfg}).compare(pd.DataFrame(src), pd.DataFrame(tgt))


def test_compare_detects_missing_extra_and_mismatch():
    r = _cmp({"ID": [1, 2, 3], "V": ["a", "b", "c"]}, {"ID": [2, 3, 4], "V": ["b", "X", "d"]})
    assert list(r.missing_in_target["ID"]) == ["1"] and list(r.missing_in_source["ID"]) == ["4"]
    assert r.mismatched_rows == 1 and r.column_mismatch_counts == {"V": 1}


def test_compare_rules_tolerance_case_trim_null_and_key_types():
    r = _cmp({"ID": [1, 2, 3], "N": [1.0, 2.0, 3.0], "T": ["A", "B ", None]},
             {"ID": ["1", "2.0", 3], "N": [1.0004, 2.5, 3.0], "T": ["a", "B", ""]},
             options={"numeric_tolerance": 0.001, "ignore_case": True})
    assert r.matched_rows == 2 and r.mismatched_rows == 1      # only ID 2 (N: 2.0 vs 2.5)
    assert r.column_mismatch_counts == {"N": 1, "T": 0}


def test_compare_duplicates_mapping_and_exclude():
    r = _cmp({"ID": [1, 1, 2], "A": [1, 1, 2], "SKIP": [1, 2, 3]},
             {"CUST": [1, 2], "A": [1, 2], "SKIP": [9, 9]},
             column_mapping={"CUST": "ID"}, exclude_fields=["SKIP"])
    assert len(r.duplicates_source) == 1 and r.duplicates_source.iloc[0]["OCCURRENCES"] == 2
    assert r.compared_fields == ["A"] and r.mismatched_rows == 0


@pytest.mark.parametrize(("target_a", "status"), [("different", "FAILED"), ("same", "PASSED")])
def test_field_comparison_report_lists_pass_and_fail_fields(target_a, status):
    from types import SimpleNamespace
    from pyassure.reporting.store import ResultStore
    from pyassure.steps.data_steps import step_no_mismatches, step_overall

    result = _cmp({"ID": [1, 2], "A": ["same", "same"], "B": ["ok", "ok"]},
                  {"ID": [1, 2], "A": [target_a, "same"], "B": ["ok", "ok"]})
    store = ResultStore("field comparison")
    store.start_scenario("Feature", "Scenario", [])
    context = SimpleNamespace(
        result=result,
        comparator=SimpleNamespace(opts=SimpleNamespace(max_report_rows=200)),
        store=store,
    )

    if status == "FAILED":
        with pytest.raises(AssertionError):
            step_no_mismatches(context)
    else:
        step_no_mismatches(context)

    summary = store.scenarios[0].checkpoints[0].tables[0]
    assert summary.title == "Field comparison summary"
    assert summary.rows == [
        ["A", "Fail" if status == "FAILED" else "Pass", "1" if status == "FAILED" else "0"],
        ["B", "Pass", "0"],
    ]

    composite_store = ResultStore("composite field comparison")
    composite_store.start_scenario("Feature", "Scenario", [])
    composite_context = SimpleNamespace(
        result=result,
        comparator=SimpleNamespace(opts=SimpleNamespace(max_report_rows=200)),
        store=composite_store,
    )
    if status == "FAILED":
        with pytest.raises(AssertionError):
            step_overall(composite_context)
    else:
        step_overall(composite_context)
    composite_checkpoint = next(
        cp for cp in composite_store.scenarios[0].checkpoints if cp.name == "No field mismatches")
    assert composite_checkpoint.tables[0].rows == summary.rows


def test_compare_reports_config_errors():
    with pytest.raises(ConfigError):
        _cmp({"ID": [1]}, {"ID": [1]}, compare_fields=["NOPE"])
    with pytest.raises(ConfigError):
        DatasetComparator({"primary_key": ["ID"], "options": {"numeric_tolerence": 1}})


# --------------------------------------------------------------------- validation
def test_validator_checks_and_severity():
    df = pd.DataFrame({"ID": [1, 1, 3], "AMT": ["5", "x", "-1"], "EMAIL": ["a@b.co", None, "bad"]})
    cfg = {"checks": [
        {"type": "unique", "columns": ["ID"]},
        {"type": "numeric", "columns": ["AMT"]},
        {"type": "range", "column": "AMT", "min": 0},
        {"type": "not_null", "columns": ["EMAIL"]},
        {"type": "regex", "column": "EMAIL", "pattern": r"[^@]+@[^@]+\.\w+", "severity": "warning"},
        {"type": "nonsense"}, {"type": "not_null", "column": "MISSING"}]}
    out = {o.label: o for o in DataValidator(cfg).run(df)}
    assert not out["unique: ID"].passed and out["unique: ID"].failed_rows == 2
    assert out["numeric: AMT"].failed_rows == 1
    assert out["regex: EMAIL"].failed_rows == 1 and out["regex: EMAIL"].severity == "warning"
    assert any("Unknown check type" in o.message for o in out.values())
    assert any("column(s) not found" in o.message for o in out.values())   # reported, not raised


def test_sql_validation_compares_text_and_numeric_results():
    query_results = {
        "SELECT status": pd.DataFrame({"status": ["READY"]}),
        "SELECT count": pd.DataFrame({"count": [3]}),
        "SELECT invalid_status": pd.DataFrame({"status": ["BLOCKED"]}),
    }
    cfg = {"checks": [
        {"type": "sql", "name": "text result", "query": "SELECT status", "expect": "READY"},
        {"type": "sql", "name": "numeric result", "query": "SELECT count", "expect": 3},
        {"type": "sql", "name": "numeric bounds", "query": "SELECT count", "min": 2, "max": 4},
        {"type": "sql", "name": "text mismatch", "query": "SELECT invalid_status", "expect": "READY"},
        {"type": "sql", "name": "numeric bounds on text", "query": "SELECT status", "min": 1},
    ]}
    outcomes = DataValidator(cfg, lambda spec: query_results[spec["query"]]).run(pd.DataFrame())
    assert [o.passed for o in outcomes] == [True, True, True, False, False]
    assert outcomes[0].actual == "READY" and outcomes[3].actual == "BLOCKED"
    assert "must be numeric" in outcomes[4].message


def test_sql_report_check_includes_query_and_rows_in_standalone_html():
    from types import SimpleNamespace
    from pyassure.reporting.html import render_html
    from pyassure.steps.data_steps import _record

    query = "SELECT STATUS, COUNT(*) AS ORDER_COUNT FROM orders GROUP BY STATUS"
    result = pd.DataFrame({"STATUS": ["NEW", "SHIPPED"], "ORDER_COUNT": [4, 2]})
    validator = DataValidator({"checks": [{"type": "sql_report", "name": "orders_by_status", "query": query}]},
                              lambda spec: result)
    outcomes = validator.run(pd.DataFrame())
    store = ResultStore("SQL report")
    store.start_scenario("Validation", "Query output is attached", [])
    _record(SimpleNamespace(validator=validator, continue_on_failure=False,
                            store=store), outcomes)

    html = render_html(store)
    assert outcomes[0].passed is True
    assert f"Query: {query}" in html
    assert "SQL query results" in html and "SHIPPED" in html and "ORDER_COUNT" in html
    assert '<details class="step" data-status="PASSED" open>' in html


def test_sql_report_accepts_empty_result_sets():
    validator = DataValidator({"checks": [{"type": "sql_report", "query": "SELECT value WHERE 1=0"}]},
                              lambda spec: pd.DataFrame(columns=["value"]))
    outcome = validator.run(pd.DataFrame())[0]
    assert outcome.passed is True and outcome.actual == "0 row(s), 1 column(s)"


# ------------------------------------------------------------------ table parsing
def test_table_parser_handles_whitespace_ragged_rows_and_nesting():
    html = ("<table><tr><th>A</th><th>B</th></tr><tr><td> 1\n </td><td>x&nbsp;<b>y</b></td></tr>"
            "<tr><td>2</td><td><table><tr><td>inner</td></tr></table>z</td></tr><tr><td>3</td></tr></table>")
    assert parse_html_table(html).to_dict("records") == [
        {"A": "1", "B": "x y"}, {"A": "2", "B": "z"}, {"A": "3", "B": ""}]
    assert list(parse_html_table(html, header=False).columns) == ["COL_1", "COL_2"]


# --------------------------------------------------------------- excel cell compare
def _book(path, cells):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "S"
    for ref, v in cells.items():
        ws[ref] = v
    wb.save(path)


def test_excel_cell_compare_locates_differences(tmp_path):
    _book(tmp_path / "a.xlsx", {"A1": "x", "B1": 1.0, "C1": "same", "D1": " t "})
    _book(tmp_path / "b.xlsx", {"A1": "y", "B1": 1.0004, "C1": "same", "D1": "T"})
    cfg = {"source": {"path": str(tmp_path / "a.xlsx")}, "target": {"path": str(tmp_path / "b.xlsx")},
           "options": {"numeric_tolerance": 0.001, "ignore_case": True}}
    r = ExcelCellComparator(cfg).compare()
    assert [d["cell"] for d in r.sheets[0].diffs] == ["A1"] and r.total_diffs == 1
    cfg["options"]["ignore_ranges"] = ["A1"]
    assert ExcelCellComparator(cfg).compare().passed


def test_excel_comparison_step_writes_formatted_workbook_and_report_link(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from openpyxl import load_workbook

    import pyassure.reporting.html as report_html
    import pyassure.steps.data_steps as data_steps

    source, target = tmp_path / "source.xlsx", tmp_path / "target.xlsx"
    _book(source, {"A1": "before", "B1": 1})
    _book(target, {"A1": "after", "B1": 3})
    monkeypatch.setattr(data_steps, "BASE_DIR", tmp_path)
    monkeypatch.setattr(report_html, "BASE_DIR", tmp_path)
    report_dir = tmp_path / "reports" / "runs" / "run-001" / "workers" / "excel"
    store = ResultStore("Excel compare")
    store.start_scenario("Excel", "Formatted workbook", [])
    context = SimpleNamespace(
        cell_cfg={"source": {"path": str(source)}, "target": {"path": str(target)},
                  "options": {"numeric_tolerance": 0.0}},
        report_dir=report_dir,
        scenario=SimpleNamespace(name="Formatted workbook"),
        store=store,
    )

    data_steps.step_generate_excel_comparison_workbook(context)
    workbook = load_workbook(context.excel_compare_workbook)
    assert workbook["_Comparison_Summary"]["B1"].value == str(source)
    assert workbook["S"]["A1"].value == "source=before | target=after"
    assert workbook["S"]["A1"].fill.fgColor.rgb.endswith("FFC000")
    assert workbook["S"]["B1"].value == "source=1 | target=3 | diff=-2"
    assert workbook["S"]["B1"].fill.fgColor.rgb.endswith("FF9999")

    run_report = tmp_path / "reports" / "runs" / "run-001" / "validation_report.html"
    report_html.write_html_report(store, run_report)
    run_html = run_report.read_text(encoding="utf-8")
    assert f'href="workers/excel/{context.excel_compare_workbook.name}"' in run_html
    latest_report = tmp_path / "reports" / "validation_report.html"
    report_html.write_html_report(store, latest_report)
    latest_html = latest_report.read_text(encoding="utf-8")
    assert f'href="runs/run-001/workers/excel/{context.excel_compare_workbook.name}"' in latest_html
    assert "Download Excel comparison workbook" in run_html


# --------------------------------------------------------- discovery + report merge
def test_discover_units_respects_tags_and_scheme():
    feats = discover(["app/features"], "feature", [])
    scen = discover(["app/features"], "scenario", [])
    assert 0 < len(feats) <= len(scen)
    # --tags on the command line REPLACES default_tags, so @demo_fail scenarios (excluded by
    # default) become selectable.
    fail = discover(["app/features"], "scenario", ["--tags=demo_fail"])
    assert fail and not {u.id for u in fail} & {u.id for u in scen}


def test_result_store_json_roundtrip_and_merge(tmp_path):
    paths = []
    for i in range(2):
        s = ResultStore("t")
        s.start_scenario("F", f"sc{i}", ["demo"])
        s.checkpoint("c", PASSED, 1, 1)
        s.attach_image("shot", b"\x89PNG")
        s.link_file("Workbook", "reports/result.xlsx")
        s.end_scenario("passed")
        p = tmp_path / f"{i}.json"
        s.write_json(p)
        paths.append(p)
    merged = ResultStore.merge(paths, "Merged", run_info={"workers": "2"})
    assert [x.name for x in merged.scenarios] == ["sc0", "sc1"]
    assert merged.totals()["checkpoints_passed"] == 2 and merged.scenarios[0].images[0].title == "shot"
    assert merged.scenarios[0].report_links[0].path == "reports/result.xlsx"
    assert json.loads((paths[0]).read_text())["framework"] == "PyAssure"


def test_standalone_report_has_feature_scenario_filters_and_right_aligned_status():
    from pyassure.reporting.html import render_html
    from pyassure.reporting.store import Table

    store = ResultStore("t")
    store.start_scenario("Feature A", "Scenario A", ["smoke", "api"])
    store.checkpoint("Rows match", PASSED, 2, 2, tables=[
        Table("Evidence", ["ID"], [["1"], ["2"]], 2),
        Table("Field comparison summary", ["FIELD", "RESULT"], [["NAME", "Pass"], ["EMAIL", "Fail"]], 2),
    ])
    store.end_scenario("passed")
    html = render_html(store)

    assert '<span class="feature-label">Feature</span><h2 class="feature-name">Feature A</h2>' in html
    assert '<select class="filter-select" id="tag-filter"' in html
    assert '<select class="filter-select" id="feature-filter"' in html
    assert '<option value="api">@api</option>' in html and '<option value="smoke">@smoke</option>' in html
    assert 'data-tags="smoke api"' in html
    assert 'data-state="warning"' in html
    assert "function applyFilters()" in html and "tags.includes(tag)" in html
    scenario_summary = html.split('<summary class="scenario-head">', 1)[1].split("</summary>", 1)[0]
    assert scenario_summary.index("scenario-main") < scenario_summary.index("scenario-meta")
    assert scenario_summary.index("scenario-meta") < scenario_summary.index("scenario-status")
    step_summary = html.split('<summary class="step-head">', 1)[1].split("</summary>", 1)[0]
    assert step_summary.index("step-name") < step_summary.index("step-values")
    assert step_summary.index("step-values") < step_summary.index("step-status")
    assert ".tw{overflow-x:auto;overflow-y:auto;max-height:270px;" in html
    assert '<span class="status pass">Pass</span>' in html
    assert '<span class="status fail">Fail</span>' in html
    assert '<strong>Total fields: 2</strong>' in html
    assert '<span class="status pass">Pass: 1</span>' in html
    assert '<span class="status fail">Fail: 1</span>' in html
    assert '<span class="metric-label">Pass</span>' in html
    assert '<span class="metric-label">Fail</span>' in html
    assert '<span class="status passed">Pass</span>' in html
    assert "color-scheme:light" in html
    assert "prefers-color-scheme:dark" not in html


# ============================================================== new in PyAssure 2.0
def test_settings_merge_defaults_and_allure_switch(monkeypatch, tmp_path):
    """pyassure.yml only lists overrides; everything else comes from DEFAULTS (deep merge)."""
    from pyassure import config
    f = tmp_path / "p.yml"
    f.write_text("ui: {headless: false}\nreports: {allure: {enabled: true}}\n")
    monkeypatch.setenv("PYASSURE_CONFIG", str(f))
    config.settings.cache_clear()
    try:
        s = config.settings()
        assert s["ui"]["headless"] is False and s["ui"]["timeout_ms"] == 5000       # override + default kept
        assert s["reports"]["allure"]["enabled"] is True and s["reports"]["allure"]["generate"] is True
    finally:
        config.settings.cache_clear()


def test_config_parameters_substitute_recursively_and_require_values():
    config = {"query": "SELECT * FROM orders WHERE batch_id = '{{batch_id}}'",
              "checks": [{"min": "{{min_date}}"}], "keep": 3}
    assert substitute_params(config, {"batch_id": "B-42", "min_date": "2025-01-01"}) == {
        "query": "SELECT * FROM orders WHERE batch_id = 'B-42'",
        "checks": [{"min": "2025-01-01"}], "keep": 3}
    with pytest.raises(ConfigError, match="missing"):
        substitute_params({"query": "{{missing}}"}, {})


def test_feature_config_table_applies_optional_name_value_params(tmp_path):
    from types import SimpleNamespace
    from pyassure.steps.data_steps import _load_feature_config

    path = tmp_path / "config.yml"
    path.write_text("query: SELECT * FROM orders WHERE batch_id = '{{batch_id}}'\n")
    table = SimpleNamespace(headings=["name", "value"],
                            rows=[SimpleNamespace(cells=["batch_id", "B-42"])])
    store = ResultStore("parameter test")
    store.start_scenario("Feature", "Scenario", [])
    assert _load_feature_config(SimpleNamespace(table=table, store=store), str(path)) == {
        "query": "SELECT * FROM orders WHERE batch_id = 'B-42'"}
    assert store.scenarios[0].info == {"Parameter: batch_id": "B-42"}
    assert _load_feature_config(SimpleNamespace(), str(path)) == {
        "query": "SELECT * FROM orders WHERE batch_id = '{{batch_id}}'"}


def test_global_config_params_apply_at_runtime_and_feature_values_override(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from pyassure import config
    from pyassure.steps.data_steps import _load_feature_config

    settings_path = tmp_path / "pyassure.yml"
    settings_path.write_text('parameters:\n  batch_id: "${BATCH_ID}"\n')
    config_path = tmp_path / "compare.yml"
    config_path.write_text("query: SELECT * FROM orders WHERE batch_id = '{{batch_id}}'\n")
    monkeypatch.setenv("PYASSURE_CONFIG", str(settings_path))
    monkeypatch.setenv("BATCH_ID", "global-batch")
    config.settings.cache_clear()
    try:
        assert _load_feature_config(SimpleNamespace(), str(config_path)) == {
            "query": "SELECT * FROM orders WHERE batch_id = 'global-batch'"}

        table = SimpleNamespace(
            headings=["name", "value"],
            rows=[SimpleNamespace(cells=["batch_id", "scenario-batch"])])
        store = ResultStore("parameter test")
        store.start_scenario("Feature", "Scenario", [])
        assert _load_feature_config(
            SimpleNamespace(table=table, store=store), str(config_path)) == {
                "query": "SELECT * FROM orders WHERE batch_id = 'scenario-batch'"}
        assert store.scenarios[0].info == {"Parameter: batch_id": "scenario-batch"}
    finally:
        config.settings.cache_clear()


def test_archive_latest_reports_moves_existing_files_with_timestamp(tmp_path):
    from pyassure.runner import _archive_latest_reports

    report = tmp_path / "validation_report.html"
    report.write_text("old report")
    _archive_latest_reports(tmp_path, ["validation_report.html", "bdd_index.html"], "20261121-132745")
    assert not report.exists()
    assert (tmp_path / "log" / "validation_report_20261121-132745.html").read_text() == "old report"
    assert not (tmp_path / "log" / "bdd_index_20261121-132745.html").exists()


def test_backup_validation_report_copies_timestamped_reports_without_overwriting(tmp_path):
    from pyassure.reporting.archive import backup_validation_report

    report = tmp_path / "runs" / "validation_report.html"
    report.parent.mkdir()
    report.write_text("completed report")

    first = backup_validation_report(tmp_path / "reports", report, "20261124-153500")
    second = backup_validation_report(tmp_path / "reports", report, "20261124-153500")

    assert first.read_text() == second.read_text() == "completed report"
    assert first.name == "validation_report_20261124-153500.html"
    assert second.name == "validation_report_20261124-153500-1.html"
    assert report.exists()


def test_plain_behave_after_all_creates_timestamped_report_backup(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from pyassure import hooks

    monkeypatch.setattr(hooks, "section", lambda name: {"standalone_html": True})
    monkeypatch.setattr(hooks.runtime, "shutdown", lambda: None)
    monkeypatch.setattr(hooks.services, "stop", lambda server: None)
    store = ResultStore("behave report")
    context = SimpleNamespace(report_dir=tmp_path, worker="", store=store)

    hooks.after_all(context)

    backups = list((tmp_path / "backup").glob("validation_report_*.html"))
    assert len(backups) == 1
    assert "PyAssure" in backups[0].read_text(encoding="utf-8")


def test_project_root_can_be_overridden(monkeypatch, tmp_path):
    from pyassure import config

    root = tmp_path / "mapped-project"
    monkeypatch.setenv("PYASSURE_ROOT", str(root))
    assert config._project_root() == root


def test_app_dir_can_be_overridden(monkeypatch, tmp_path):
    from pyassure import config

    app_dir = tmp_path / "custom-app"
    monkeypatch.setenv("PYASSURE_APP_DIR", str(app_dir))
    assert config._app_dir() == app_dir
    monkeypatch.setenv("PYASSURE_APP_DIR", "alternate-app")
    assert config._app_dir() == config.BASE_DIR / "alternate-app"


def test_allure_switch_and_cli_override(monkeypatch):
    from types import SimpleNamespace
    from pyassure.reporting import allure_support as a

    def ctx(**ud):
        return SimpleNamespace(config=SimpleNamespace(userdata=ud))
    assert a.is_enabled(ctx()) is False                      # default from pyassure.yml (off)
    assert a.is_enabled(ctx(allure="true")) is True          # -D allure=true wins
    monkeypatch.setitem(a.cfg(), "enabled", True)
    assert a.is_enabled(ctx()) is True and a.is_enabled(ctx(allure="false")) is False


def test_json_path():
    from pyassure.web.api import MISSING, json_path
    data = {"customers": [{"id": 1, "tags": ["a", "b"]}], "total": 1, "n": None}
    assert json_path(data, "customers[0].id") == 1 and json_path(data, "customers.0.tags[1]") == "b"
    assert json_path(data, "n") is None                       # JSON null is a value ...
    assert json_path(data, "nope") is MISSING and json_path(data, "customers[5]") is MISSING   # ... missing is not
    assert json_path(data, "$") is data


def test_storage_state_session_roundtrip_and_ttl(tmp_path, monkeypatch):
    """An API-kind session provider logs in ONCE, saves storageState, and is reused until the TTL expires.
    Needs the demo app (started on demand) and the Playwright driver - but no browser."""
    pytest.importorskip("playwright")
    from pyassure import services
    from pyassure.web import runtime as rt

    server = services.ensure()
    monkeypatch.setitem(rt.section("sessions"), "dir", str(tmp_path))
    try:
        path1 = rt.get_storage_state("admin")
        stamp = rt.state_file("admin").stat().st_mtime
        state = json.loads(open(path1).read())
        assert any(c["name"] == "session" for c in state["cookies"])      # the login cookie was captured
        assert rt.get_storage_state("admin") == path1 and rt.state_file("admin").stat().st_mtime == stamp  # reused
        monkeypatch.setitem(rt.section("sessions"), "ttl_minutes", 0)     # expired -> logs in again
        rt.get_storage_state("admin")
        assert rt.state_file("admin").stat().st_mtime >= stamp
    finally:
        rt.shutdown()
        services.stop(server)


def test_api_client_against_demo_app():
    pytest.importorskip("playwright")
    from app.api.customers_api import CustomersApi
    from pyassure import services
    from pyassure.web import runtime as rt

    server = services.ensure()
    try:
        anon = CustomersApi.open()
        assert anon.list().status == 401
        authed = CustomersApi.open(session="admin")
        r = authed.list(name="Customer 1")
        assert r.ok and r.json()["total"] == 11 and authed.last is r and len(authed.calls) == 1
        anon.close(); authed.close()
    finally:
        rt.shutdown()
        services.stop(server)


def test_web_server_command_preserves_windows_python_path(monkeypatch):
    from pyassure import services

    python = r"C:\Program Files\Python\python.exe"
    process = type("Process", (), {"poll": lambda self: None})()
    calls = []
    availability = iter([False, True])
    monkeypatch.setattr(services, "section", lambda name: {
        "command": "{python} app/demo_app/server.py",
        "url": "http://127.0.0.1:8765/api/health",
        "startup_timeout_s": 1,
    })
    monkeypatch.setattr(services.sys, "executable", python)
    monkeypatch.setattr(services, "is_up", lambda url: next(availability))
    monkeypatch.setattr(services.subprocess, "Popen", lambda command, **kwargs: calls.append(command) or process)

    assert services.ensure() is process
    assert calls == [[python, "app/demo_app/server.py"]]


def test_runner_shard_split_is_a_partition():
    units = sorted(discover(["app/features"], "scenario", []), key=lambda u: u.id)
    parts = [units[i::3] for i in range(3)]                   # what `--shard i/3` does
    assert sorted(u.id for p in parts for u in p) == [u.id for u in units]
    assert max(map(len, parts)) - min(map(len, parts)) <= 1   # balanced


def test_runner_worker_count_uses_auto_cap_and_available_units(monkeypatch):
    from pyassure import runner

    monkeypatch.setattr(runner.os, "cpu_count", lambda: 8)
    assert runner._worker_count("auto", 10) == 4
    assert runner._worker_count("auto", 2) == 2
    assert runner._worker_count("0", 2) == 1


def test_runner_writes_run_outputs_and_preserves_summary_types(tmp_path):
    from pyassure.runner import Result, Unit, _write_run_outputs

    report_root = tmp_path / "reports"
    run_dir = report_root / "runs" / "20261124-160000"
    worker_dir = run_dir / "workers" / "feature-a"
    junit_dir = worker_dir / "junit"
    junit_dir.mkdir(parents=True)
    (junit_dir / "TEST-feature.xml").write_text("<testsuite/>")
    result = Result(Unit("feature-a", "feature-a.feature", "Feature A"),
                    status="passed", duration=0.25, worker_dir=str(worker_dir))
    store = ResultStore("Runner output test")
    info = {"workers": "1", "scheme": "feature", "wall time": "0.3s"}

    backup = _write_run_outputs(report_root, run_dir, [result], store, info,
                                {"feature-a": 0.25}, 0.25, 1, "feature")

    summary = json.loads((run_dir / "run_summary.json").read_text())
    assert summary["workers"] == 1 and summary["scheme"] == "feature"
    assert (run_dir / "junit" / "feature-a__TEST-feature.xml").exists()
    assert (report_root / "validation_report.html").exists()
    assert backup.read_bytes() == (run_dir / "validation_report.html").read_bytes()


def test_api_calls_are_recorded_and_merged(tmp_path):
    from types import SimpleNamespace
    s = ResultStore("t")
    s.start_scenario("F", "api", [])
    s.api_call(SimpleNamespace(method="GET", url="http://x/api", status=200, elapsed_ms=12.34,
                               request_body=None, text='{"ok": true}'))
    s.end_scenario("passed")
    p = tmp_path / "r.json"
    s.write_json(p)
    merged = ResultStore.merge([p], "m")
    assert merged.scenarios[0].api_calls[0].status == 200 and merged.scenarios[0].api_calls[0].ms == 12.3
