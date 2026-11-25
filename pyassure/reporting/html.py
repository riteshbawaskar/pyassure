"""Render a compact, standalone HTML report for scenarios and validation checkpoints."""
from __future__ import annotations

import os
from html import escape as e
from pathlib import Path
from typing import Dict, List, Optional

from pyassure.branding import NAME, TAGLINE, VERSION
from pyassure.config import BASE_DIR
from pyassure.reporting.store import (FAILED, WARNING, Checkpoint, ResultStore,
                                      ScenarioRecord, Table)

_CSS = """
:root{color-scheme:light;--page:#f4f6fb;--surface:#fff;--ink:#1d2b45;--muted:#748198;--line:#e4e9f1;
--blue:#315ed1;--blue-soft:#eef3ff;--green:#18764b;--green-soft:#e9f7ef;--red:#b83c49;--red-soft:#fff0f1;
--amber:#99660a;--amber-soft:#fff7e5;--shadow:0 10px 28px rgba(31,48,77,.055)}
*{box-sizing:border-box}body{margin:0;color:var(--ink);background:radial-gradient(ellipse at 12% 0,#eaf0ff 0,transparent 37%),var(--page);
font:14px/1.5 "Segoe UI",Inter,system-ui,sans-serif}button,input,select{font:inherit}button{cursor:pointer}
.page{width:min(1180px,calc(100% - 40px));margin:0 auto;padding:26px 0 56px}
.masthead{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-bottom:18px}
.identity{display:flex;align-items:center;gap:12px;min-width:0}
.mark{display:grid;width:38px;height:38px;place-items:center;flex:0 0 auto;border-radius:12px;color:#fff;
background:linear-gradient(145deg,#4776e6,#274db5);box-shadow:0 5px 13px rgba(49,94,209,.24);font-size:15px;font-weight:800}
.eyebrow{color:var(--blue);font-size:10px;font-weight:800;letter-spacing:.12em;text-transform:uppercase}
h1{margin:0;font-size:22px;line-height:1.2;letter-spacing:-.035em}.tagline{margin:3px 0 0;color:var(--muted);font-size:11px}
.run-date{color:var(--muted);font-size:10px;text-align:right;white-space:nowrap}.run-date strong{display:block;color:var(--ink);font-size:12px;font-weight:650}
.run-facts{display:block;margin-top:3px;white-space:normal}
.summary{display:grid;grid-template-columns:repeat(8,minmax(0,1fr));gap:9px;margin-bottom:17px}
.metric{min-width:0;padding:11px 13px;border:1px solid var(--line);border-radius:12px;background:rgba(255,255,255,.92);box-shadow:var(--shadow)}
.metric-label{display:block;overflow:hidden;color:var(--muted);font-size:10px;text-overflow:ellipsis;white-space:nowrap}
.metric-value{display:block;margin-top:1px;color:var(--ink);font-size:21px;font-weight:750;line-height:1.2;letter-spacing:-.04em}
.metric.pass .metric-value{color:var(--green)}.metric.fail .metric-value{color:var(--red)}.metric.warn .metric-value{color:var(--amber)}
.toolbar{position:sticky;top:8px;z-index:5;display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-bottom:15px;padding:9px;
border:1px solid var(--line);border-radius:13px;background:rgba(255,255,255,.94);box-shadow:var(--shadow);backdrop-filter:blur(12px)}
.search{flex:1 1 190px;min-width:150px;height:36px;padding:0 11px;border:1px solid var(--line);border-radius:9px;outline:0;background:#fbfcfe;color:var(--ink)}
.search:focus,.filter-select:focus{border-color:#9bb2f1;box-shadow:0 0 0 3px #edf2ff}
.filters{display:flex;flex-wrap:wrap;gap:3px}.filter{padding:7px 9px;border:0;border-radius:8px;background:transparent;color:#627089;font-size:11px;font-weight:700}
.filter:hover{background:#f3f5f9}.filter.active{color:#294fae;background:var(--blue-soft)}.filter-count{margin-left:4px;opacity:.7}
.filter-select{max-width:190px;height:36px;padding:0 28px 0 10px;border:1px solid var(--line);border-radius:9px;outline:0;background:#fbfcfe;color:var(--ink);font-size:12px}
.toolbar-action{height:34px;padding:0 10px;border:1px solid var(--line);border-radius:8px;background:#fff;color:#56647a;font-size:11px;font-weight:700}
.toolbar-action:hover{border-color:#cbd5e4;background:#f9fbfe}.visible-count{margin-left:auto;color:var(--muted);font-size:11px;white-space:nowrap}
.feature-group{overflow:hidden;margin-bottom:14px;border:1px solid var(--line);border-radius:14px;background:var(--surface);box-shadow:var(--shadow)}
.feature-head{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:13px 16px;border-bottom:1px solid var(--line);background:linear-gradient(90deg,#f8faff,#fff)}
.feature-label{display:block;margin-bottom:1px;color:var(--blue);font-size:9px;font-weight:800;letter-spacing:.13em;text-transform:uppercase}
.feature-name{margin:0;font-size:15px;font-weight:750;letter-spacing:-.015em}
.feature-summary{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:6px;color:var(--muted);font-size:10px}
.feature-summary span{padding:3px 7px;border:1px solid var(--line);border-radius:999px;background:var(--surface)}
.scenario{margin:9px;border:1px solid var(--line);border-left:3px solid #c7d0de;border-radius:10px;background:var(--surface)}
.scenario[data-status="passed"]{border-left-color:var(--green)}.scenario[data-status="failed"],.scenario[data-status="error"]{border-left-color:var(--red)}
.scenario[data-warning="true"]{border-left-color:var(--amber)}
.scenario-head{display:grid;grid-template-columns:minmax(0,1fr) auto auto;align-items:center;gap:9px;padding:11px 12px;list-style:none;cursor:pointer}
.scenario-head::-webkit-details-marker{display:none}.scenario-head:hover{background:#fbfcff}
.scenario-main{min-width:0}.scenario-name{display:block;font-size:13px;font-weight:740;line-height:1.35}
.scenario-tags{display:inline-flex;flex-wrap:wrap;gap:4px;margin-top:4px}
.tag{padding:1px 6px;border-radius:999px;background:#f0f3f8;color:#63718a;font-size:9px}
.status{display:inline-flex;align-items:center;justify-content:center;min-width:48px;padding:3px 8px;border-radius:999px;
background:#edf0f5;color:#657187;font-size:9px;font-weight:850;letter-spacing:.04em;white-space:nowrap}
.status.pass,.status.passed{background:var(--green-soft);color:var(--green)}
.status.fail,.status.failed,.status.error{background:var(--red-soft);color:var(--red)}
.status.warning{background:var(--amber-soft);color:var(--amber)}
.scenario-meta{color:var(--muted);font-size:10px;text-align:right;white-space:nowrap}.scenario-status{justify-self:end}
.scenario-body{padding:0 14px 14px}
.info-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:6px;margin:0 0 14px}
.info-item{min-width:0;padding:7px 9px;border:1px solid #edf0f5;border-radius:8px;background:#fafbfd}
.info-key{display:block;color:var(--muted);font-size:9px;font-weight:750;letter-spacing:.035em;text-transform:uppercase}
.info-value{display:block;overflow-wrap:anywhere;margin-top:2px;color:#35435c;font-size:11px}
.info-value a{color:var(--blue);font-weight:650;text-decoration:none}.info-value a:hover{text-decoration:underline}
.steps-head{display:flex;align-items:baseline;justify-content:space-between;gap:10px;margin:5px 0 7px}
.steps-title{margin:0;font-size:12px;font-weight:800}.steps-hint{color:var(--muted);font-size:10px}
.steps{display:grid;gap:6px}.step{position:relative;overflow:hidden;border:1px solid var(--line);border-radius:9px;background:var(--surface)}
.step[data-status="FAILED"]{border-color:#f0c8cc}.step[data-status="WARNING"]{border-color:#f0dfb8}
.step-head{display:grid;grid-template-columns:26px minmax(0,1fr) minmax(0,40%) auto;align-items:center;gap:9px;padding:8px 10px}
summary.step-head{list-style:none;cursor:pointer}summary.step-head::-webkit-details-marker{display:none}
.step-index{display:grid;width:23px;height:23px;place-items:center;border-radius:7px;background:#f0f3f8;color:#64728a;font-size:10px;font-weight:800}
.step[data-status="PASSED"] .step-index{background:var(--green-soft);color:var(--green)}
.step[data-status="FAILED"] .step-index{background:var(--red-soft);color:var(--red)}
.step[data-status="WARNING"] .step-index{background:var(--amber-soft);color:var(--amber)}
.step-name{min-width:0;font-size:11px;font-weight:700}.step-values{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:5px;color:var(--muted);font-size:9px}
.step-values span{max-width:100%;padding:2px 6px;border-radius:5px;background:#f6f8fb;overflow-wrap:anywhere}.step-status{justify-self:end}
.step-content{padding:0 10px 10px 45px}.step-message{margin:0 0 7px;color:#58667d;font-size:10px}
.table-title{margin:7px 0 4px;color:#58667d;font-size:9px;font-weight:800;letter-spacing:.05em;text-transform:uppercase}
.table-note{margin:-2px 0 4px;color:var(--muted);font-size:9px}
.field-summary{display:flex;flex-wrap:wrap;align-items:center;gap:7px;margin:5px 0;color:var(--muted);font-size:10px}
.field-summary strong{color:var(--ink);font-weight:750}
.tw{overflow-x:auto;overflow-y:auto;max-height:270px;overscroll-behavior:contain;margin:6px 0;border:1px solid var(--line);border-radius:7px}
table{width:100%;border-collapse:collapse;font-size:10px}th,td{padding:6px 8px;border-bottom:1px solid #edf0f5;text-align:left;vertical-align:top;white-space:nowrap}
th{position:sticky;top:0;background:#f8faff;color:#627089;font-size:9px;font-weight:800}tr:last-child td{border-bottom:0}
.error-box{margin-top:10px;padding:10px 12px;border:1px solid #f0c8cc;border-radius:8px;background:#fff7f7;color:#8f3540;font-size:11px;white-space:pre-wrap;overflow-wrap:anywhere}
.supplemental{margin-top:7px;border:1px solid var(--line);border-radius:8px;background:var(--surface)}
.supplemental>summary{padding:8px 10px;cursor:pointer;font-size:11px;font-weight:700}
.supplemental-content{padding:0 10px 10px}.api-error td{color:var(--red)}
.shot{display:block;max-width:100%;height:auto;margin:8px 0;border:1px solid var(--line);border-radius:7px}
.empty{display:none;padding:36px 15px;color:var(--muted);text-align:center}.empty.show{display:block}
.footnote{margin:17px 0 0;color:#8590a3;font-size:10px;text-align:center}[hidden]{display:none!important}
@media(max-width:950px){.summary{grid-template-columns:repeat(4,minmax(0,1fr))}.toolbar{position:static}}
@media(max-width:650px){
 .page{width:min(100% - 22px,1180px);padding-top:16px}.masthead{align-items:flex-start;margin-bottom:13px}
 .mark{width:34px;height:34px;border-radius:10px}h1{font-size:19px}.run-date,.run-date strong{font-size:10px}
 .summary{grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}.metric{padding:8px 10px}.metric-value{font-size:18px}
 .toolbar{gap:6px;padding:7px}.search{flex-basis:100%}.filters{width:100%}.filter{flex:1 1 auto;padding:7px 5px}
 .filter-select{flex:1 1 130px;max-width:none}.visible-count{flex-basis:100%;margin-left:0}
 .feature-head{align-items:flex-start;padding:11px 12px}.feature-summary{max-width:48%}
 .scenario{margin:7px}.scenario-head{grid-template-columns:minmax(0,1fr) auto;gap:7px;padding:9px}
 .scenario-status{grid-column:2;grid-row:1}.scenario-meta{grid-column:1;grid-row:2;text-align:left}
 .scenario-body{padding:0 9px 10px}.info-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
 .step-head{grid-template-columns:23px minmax(0,1fr) auto;gap:7px}
 .step-values{grid-column:2/4;justify-content:flex-start}.step-content{padding-left:39px}
 .steps-head{align-items:flex-start;flex-direction:column;gap:0}
}
"""

_JS = """
const scenarios=Array.from(document.querySelectorAll('.scenario'));
const groups=Array.from(document.querySelectorAll('.feature-group'));
const statusButtons=Array.from(document.querySelectorAll('.filter'));
const search=document.querySelector('#search');
const tagFilter=document.querySelector('#tag-filter');
const featureFilter=document.querySelector('#feature-filter');
const visibleCount=document.querySelector('#visible-count');
const empty=document.querySelector('#empty');
let activeState='all';
function hasWarning(s){return s.dataset.warning==='true'}
function matchesStatus(s,state){
  if(state==='all')return true;
  if(state==='warning')return hasWarning(s);
  return s.dataset.status===state;
}
function applyFilters(){
  const query=search.value.trim().toLowerCase(),tag=tagFilter.value,feature=featureFilter.value;
  let visible=0;
  groups.forEach(group=>{
    const featureMatches=!feature||group.dataset.feature===feature;
    const featureName=group.querySelector('.feature-name').textContent.toLowerCase();
    group.querySelectorAll('.scenario').forEach(s=>{
      const tags=s.dataset.tags.split(' ').filter(Boolean);
      const searchMatches=!query||s.textContent.toLowerCase().includes(query)||featureName.includes(query);
      s.hidden=!(featureMatches&&matchesStatus(s,activeState)&&(!tag||tags.includes(tag))&&searchMatches);
      if(!s.hidden)visible++;
    });
    group.hidden=!Array.from(group.querySelectorAll('.scenario')).some(s=>!s.hidden);
  });
  visibleCount.textContent=`Showing ${visible} of ${scenarios.length} scenarios`;
  empty.classList.toggle('show',visible===0);
}
statusButtons.forEach(button=>button.addEventListener('click',()=>{
  activeState=button.dataset.state;
  statusButtons.forEach(item=>item.classList.toggle('active',item===button));
  applyFilters();
}));
search.addEventListener('input',applyFilters);
tagFilter.addEventListener('change',applyFilters);
featureFilter.addEventListener('change',applyFilters);
document.querySelector('#expand-all').addEventListener('click',()=>{
  document.querySelectorAll('details.scenario:not([hidden]),details.step,details.supplemental')
    .forEach(item=>{item.open=true});
});
document.querySelector('#collapse-all').addEventListener('click',()=>{
  document.querySelectorAll('details.scenario,details.step,details.supplemental')
    .forEach(item=>{item.open=false});
});
applyFilters();
"""


def _status_label(status: str) -> str:
    labels = {
        "passed": "Pass",
        "failed": "Fail",
        "error": "Error",
        "warning": "Warning",
        "skipped": "Skipped",
    }
    return labels.get(status.lower(), status.capitalize())


def _table_cell(value: str) -> str:
    status = value.strip().upper()
    if status in ("PASS", "PASSED", "FAIL", "FAILED"):
        label = "Pass" if status in ("PASS", "PASSED") else "Fail"
        css_class = "pass" if label == "Pass" else "fail"
        return f'<span class="status {css_class}">{label}</span>'
    return e(value)


def _field_summary(t: Table) -> str:
    if t.title != "Field comparison summary" or "RESULT" not in t.columns:
        return ""
    result_index = t.columns.index("RESULT")
    results = [row[result_index].strip().lower() for row in t.rows if len(row) > result_index]
    passed = sum(result in ("pass", "passed") for result in results)
    failed = sum(result in ("fail", "failed") for result in results)
    shown_note = (
        f" <span>Counts for {len(t.rows)} shown</span>" if t.total_rows > len(t.rows) else ""
    )
    return (
        f'<div class="field-summary"><strong>Total fields: {t.total_rows}</strong>'
        f'<span class="status pass">Pass: {passed}</span>'
        f'<span class="status fail">Fail: {failed}</span>{shown_note}</div>'
    )


def _table(t: Table) -> str:
    head = "".join(f"<th scope=\"col\">{e(column)}</th>" for column in t.columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{_table_cell(value)}</td>" for value in row) + "</tr>"
        for row in t.rows
    )
    note = (
        f'<div class="table-note">Showing {len(t.rows)} of {t.total_rows} rows</div>'
        if t.total_rows > len(t.rows) else ""
    )
    return (
        f'<div class="table-title">{e(t.title)}</div>{_field_summary(t)}{note}'
        f'<div class="tw"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'
    )


def _checkpoint(c: Checkpoint, index: int) -> str:
    status = e(c.status)
    status_class = e(c.status.lower())
    status_text = e(_status_label(c.status))
    values = ""
    if c.expected:
        values += f'<span>Expected: {e(c.expected)}</span>'
    if c.actual:
        values += f'<span>Actual: {e(c.actual)}</span>'
    head = (
        f'<span class="step-index">{index + 1}</span>'
        f'<span class="step-name">{e(c.name)}</span>'
        f'<span class="step-values">{values}</span>'
        f'<span class="step-status"><span class="status {status_class}">{status_text}</span></span>'
    )
    content = ""
    if c.message:
        content += f'<p class="step-message">{e(c.message)}</p>'
    content += "".join(_table(table) for table in c.tables)
    if not content:
        return f'<div class="step" data-status="{status}"><div class="step-head">{head}</div></div>'
    open_attr = (
        " open" if c.status == FAILED or any(table.title == "SQL query results" for table in c.tables)
        else ""
    )
    return (
        f'<details class="step" data-status="{status}"{open_attr}>'
        f'<summary class="step-head">{head}</summary>'
        f'<div class="step-content">{content}</div></details>'
    )


def _scenario(s: ScenarioRecord, report_path: Path) -> str:
    status = e(s.status)
    status_class = e(s.status.lower())
    status_text = e(_status_label(s.status))
    warning = str(any(checkpoint.status == WARNING for checkpoint in s.checkpoints)).lower()
    tags = "".join(f'<span class="tag">@{e(tag)}</span>' for tag in s.tags)
    tag_values = e(" ".join(s.tags))
    info = "".join(
        f'<div class="info-item"><span class="info-key">{e(key)}</span>'
        f'<span class="info-value">{e(value)}</span></div>'
        for key, value in s.info.items()
    )
    for link in s.report_links:
        href = (link.path if link.path.startswith("file://") else
                os.path.relpath(BASE_DIR / link.path, report_path.parent).replace(os.sep, "/"))
        info += (
            f'<div class="info-item"><span class="info-key">Report artifact</span>'
            f'<span class="info-value"><a href="{e(href)}" download>{e(link.title)}</a></span></div>'
        )
    errors = "".join(f'<div class="error-box">{e(error)}</div>' for error in s.errors)
    validations = "".join(_checkpoint(checkpoint, index) for index, checkpoint in enumerate(s.checkpoints))
    if not validations:
        validations = '<div class="no-checkpoints">No validation checkpoints were recorded for this scenario.</div>'
    checkpoint_count = len(s.checkpoints)
    checkpoint_label = f"{checkpoint_count} validation" + ("" if checkpoint_count == 1 else "s")
    open_attr = " open" if s.status in ("failed", "error") else ""
    info_html = f'<div class="info-grid">{info}</div>' if info else ""

    api = ""
    if s.api_calls:
        rows = "".join(
            f'<tr class="{"api-error" if call.status >= 400 else ""}">'
            f'<td>{e(call.method)}</td><td>{e(call.url)}</td><td>{call.status}</td>'
            f'<td>{call.ms:.0f} ms</td><td title="{e(call.response)}">{e(call.response[:60])}</td></tr>'
            for call in s.api_calls
        )
        api = (
            f'<details class="supplemental"><summary>API calls ({len(s.api_calls)})</summary>'
            f'<div class="supplemental-content"><div class="tw"><table><thead><tr>'
            f'<th>Method</th><th>URL</th><th>Status</th><th>Time</th><th>Response</th>'
            f'</tr></thead><tbody>{rows}</tbody></table></div></div></details>'
        )
    screenshots = "".join(
        f'<details class="supplemental"><summary>Screenshot: {e(image.title)}</summary>'
        f'<div class="supplemental-content"><img class="shot" alt="{e(image.title)}" '
        f'src="data:image/png;base64,{e(image.png_b64)}"></div></details>'
        for image in s.images
    )

    return (
        f'<details class="scenario" data-status="{status}" data-warning="{warning}" '
        f'data-tags="{tag_values}"{open_attr}><summary class="scenario-head">'
        f'<span class="scenario-main"><span class="scenario-name">{e(s.name)}</span>'
        f'<span class="scenario-tags">{tags}</span></span>'
        f'<span class="scenario-meta">{e(checkpoint_label)} &nbsp;·&nbsp; {s.duration:.2f}s</span>'
        f'<span class="scenario-status"><span class="status {status_class}">{status_text}</span></span>'
        f'</summary><div class="scenario-body">'
        f'{info_html}'
        f'{errors}<div class="steps-head"><h3 class="steps-title">Validation steps</h3>'
        f'<span class="steps-hint">{e(checkpoint_label)} recorded for this scenario</span></div>'
        f'<div class="steps">{validations}</div>{api}{screenshots}</div></details>'
    )


def render_html(store: ResultStore, report_path: Optional[Path] = None) -> str:
    report_path = Path(report_path) if report_path else BASE_DIR / "reports" / "validation_report.html"
    totals = store.totals()
    scenario_passed = sum(scenario.status == "passed" for scenario in store.scenarios)
    scenario_failed = sum(scenario.status == "failed" for scenario in store.scenarios)
    scenario_errors = sum(scenario.status == "error" for scenario in store.scenarios)

    def card(label: str, value: int, style: str = "") -> str:
        return (
            f'<div class="metric {style}"><span class="metric-label">{e(label)}</span>'
            f'<strong class="metric-value">{value}</strong></div>'
        )

    cards = "".join((
        card("Scenarios", totals["scenarios"]),
        card("Pass", scenario_passed, "pass"),
        card("Fail", scenario_failed, "fail"),
        card("Errors", scenario_errors, "fail"),
        card("Validations", totals["checkpoints"]),
        card("Pass", totals["checkpoints_passed"], "pass"),
        card("Fail", totals["checkpoints_failed"], "fail"),
        card("Warnings", totals["checkpoints_warning"], "warn"),
    ))

    by_feature: Dict[str, List[ScenarioRecord]] = {}
    for scenario in store.scenarios:
        by_feature.setdefault(scenario.feature, []).append(scenario)
    features = list(by_feature)
    feature_groups = []
    for index, feature in enumerate(features):
        scenarios = by_feature[feature]
        passed = sum(scenario.status == "passed" for scenario in scenarios)
        failed = sum(scenario.status == "failed" for scenario in scenarios)
        errors = sum(scenario.status == "error" for scenario in scenarios)
        failed_summary = f'<span class="status fail">{failed} Fail</span>' if failed else ""
        error_summary = f"<span>{errors} errors</span>" if errors else ""
        feature_summary = (
            f'<span>{len(scenarios)} scenarios</span><span class="status pass">{passed} Pass</span>'
            f'{failed_summary}{error_summary}'
        )
        feature_groups.append(
            f'<section class="feature-group" data-feature="{index}"><header class="feature-head">'
            f'<div><span class="feature-label">Feature</span><h2 class="feature-name">{e(feature)}</h2></div>'
            f'<div class="feature-summary">{feature_summary}</div></header>'
            f'{"".join(_scenario(scenario, report_path) for scenario in scenarios)}</section>'
        )
    body = "".join(feature_groups)

    tags = sorted({tag for scenario in store.scenarios for tag in scenario.tags})
    tag_options = "".join(f'<option value="{e(tag)}">@{e(tag)}</option>' for tag in tags)
    feature_options = "".join(
        f'<option value="{index}">{e(feature)}</option>' for index, feature in enumerate(features)
    )
    run_facts = []
    if store.environment:
        run_facts.append(store.environment)
    run_facts.extend(f"{key}: {value}" for key, value in store.run_info.items())
    run_facts_html = (
        f'<span class="run-facts">{" &middot; ".join(e(fact) for fact in run_facts)}</span>'
        if run_facts else ""
    )
    filters = "".join(
        f'<button class="filter{" active" if state == "all" else ""}" type="button" '
        f'data-state="{state}">{label}<span class="filter-count">{count}</span></button>'
        for state, label, count in (
            ("all", "All", len(store.scenarios)),
            ("passed", "Pass", scenario_passed),
            ("failed", "Fail", scenario_failed),
            ("error", "Error", scenario_errors),
            ("warning", "Warning", sum(
                any(checkpoint.status == WARNING for checkpoint in scenario.checkpoints)
                for scenario in store.scenarios
            )),
        )
    )
    started = store.started.strftime("%Y-%m-%d %H:%M:%S")

    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{e(NAME)} | {e(store.title)}</title><style>{_CSS}</style></head>'
        '<body><main class="page">'
        '<header class="masthead"><div class="identity"><div class="mark" aria-hidden="true">P</div>'
        '<div><div class="eyebrow">'
        f'{e(NAME)} v{e(VERSION)}</div><h1>{e(store.title)}</h1>'
        f'<p class="tagline">{e(TAGLINE)}</p></div></div>'
        f'<div class="run-date"><span>EXECUTION TIME</span><strong>{e(started)}</strong>{run_facts_html}</div></header>'
        f'<section class="summary" aria-label="Execution summary">{cards}</section>'
        '<nav class="toolbar" aria-label="Report filters">'
        '<input class="search" id="search" type="search" placeholder="Search scenarios, validations, or evidence…" '
        'aria-label="Search report"><div class="filters" id="filters" role="group" aria-label="Filter by status">'
        f'{filters}</div><select class="filter-select" id="feature-filter" aria-label="Filter by feature">'
        f'<option value="">All features</option>{feature_options}</select>'
        '<select class="filter-select" id="tag-filter" aria-label="Filter by tag">'
        f'<option value="">All tags</option>{tag_options}</select>'
        '<button class="toolbar-action" id="expand-all" type="button">Expand all</button>'
        '<button class="toolbar-action" id="collapse-all" type="button">Collapse all</button>'
        '<span class="visible-count" id="visible-count" aria-live="polite"></span></nav>'
        f'<div id="report-content">{body}</div><div class="empty" id="empty">No scenarios match these filters.</div>'
        '<p class="footnote">Validation steps shown here are the checkpoints recorded in this report; '
        'Given/When steps are not included in the saved data.</p>'
        f'</main><script>{_JS}</script></body></html>'
    )


def write_html_report(store: ResultStore, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(store, path), encoding="utf-8")
    return path
