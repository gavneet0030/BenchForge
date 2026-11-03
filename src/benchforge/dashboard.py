"""Local, loopback-first light-theme analytics dashboard."""

from __future__ import annotations

import csv
import io
import ipaddress
import json
import logging
import re
import signal
import sqlite3
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import perf_counter
from urllib.parse import parse_qs, unquote, urlparse, urlsplit

from .documents import (
    MAX_PDF_BYTES,
    public_document,
    read_document,
    search_document,
    store_pdf,
    validate_max_pdf_bytes,
)
from .exports import (
    MAX_EXPORT_BYTES,
    spreadsheet_safe_cell,
    validate_export_size,
)
from .history import compare_historical_runs
from .stats import (
    MINIMUM_PAIRED_OBSERVATIONS,
    bootstrap_paired_median_ratio_ci,
    classify_regression,
    paired_median_ratio,
    wilcoxon_paired_pvalue,
)

_DATASETS = {"bike", "retail", "taxi", "household_power", "wine_quality"}
_MAX_DASHBOARD_RUNS = 100
_MAX_DASHBOARD_SAMPLES_PER_RUN = 200
_MAX_DASHBOARD_SAMPLES_TOTAL = 500
_MAX_DASHBOARD_TEXT_CELL_CHARS = 1_024
_COLUMNS = (
    "id", "timestamp_utc", "workload", "sample_index", "elapsed_ns",
    "extra_work", "os", "python_version", "schema_version", "run_id",
    "variant", "binary", "config_json", "pair_index", "execution_order", "seed",
)
_LOGGER = logging.getLogger(__name__)


def _is_loopback_host_header(host_header: str, expected_port: int) -> bool:
    if not host_header:
        return False
    try:
        authority = urlsplit(f"//{host_header}")
        if (
            authority.username is not None
            or authority.password is not None
            or authority.path
            or authority.query
            or authority.fragment
            or authority.port != expected_port
        ):
            return False
    except ValueError:
        return False

    hostname = authority.hostname
    if hostname is None:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False

_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>BenchForge | Systems Performance &amp; Reliability Intelligence</title>
<style>
:root{font-family:Inter,"Segoe UI",Arial,sans-serif;color:#14243b;background:#f4f7fb;font-synthesis:none}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:#f4f7fb;color:#14243b}
a{color:#1858b8}button,select{font:inherit;color:#22354d}button{cursor:pointer}
.shell{display:grid;grid-template-columns:244px minmax(0,1fr);min-height:100vh}
aside{padding:25px 16px;background:#fff;border-right:1px solid #dfe6ef}
.brand{font-size:22px;font-weight:760;letter-spacing:-.6px;padding:8px 12px 3px;color:#10243e}
.tagline{font-size:11px;color:#53657c;padding:0 12px 28px}
.nav-label{font-size:10px;font-weight:700;color:#77869a;text-transform:uppercase;letter-spacing:1.2px;padding:0 12px 9px}
nav{display:grid;gap:5px}nav button{border:0;background:transparent;text-align:left;padding:11px 12px;border-radius:8px;color:#33465e;font-size:13px}
nav button:hover{background:#f1f5fa}nav button.active{background:#e8f1ff;color:#174e9e;font-weight:700}
.side-note{margin:35px 9px 0;padding:13px;border:1px solid #e0e8f1;border-radius:10px;font-size:11px;line-height:1.55;color:#52647a;background:#fbfcfe}
main{min-width:0;padding:28px clamp(18px,4vw,54px) 48px}
header{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;margin-bottom:27px}
.eyebrow{font-size:11px;text-transform:uppercase;letter-spacing:1.2px;color:#55718f;font-weight:700}
h1{font-size:27px;margin:6px 0 5px;letter-spacing:-.6px;color:#12243a}
.subtitle{font-size:13px;color:#61738a}
.local-badge{background:#e6f5ee;color:#17663f;border:1px solid #c8e8d7;padding:7px 10px;border-radius:99px;font-size:11px;white-space:nowrap;font-weight:650}
.page{display:none}.page.active{display:block}.page-intro{margin:0 0 18px;font-size:13px;color:#5a6d84;line-height:1.55}
.grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:16px;margin-bottom:17px}
.card{background:#fff;border:1px solid #e0e7f0;border-radius:12px;padding:18px 19px;box-shadow:0 3px 12px #203c5a0a;min-width:0}
.kpi{grid-column:span 3}.wide{grid-column:span 8}.half{grid-column:span 6}.third{grid-column:span 4}.full{grid-column:1/-1}
.kpi-label{font-size:11px;color:#60728a;font-weight:600}.kpi-value{font-size:25px;font-weight:740;color:#142d4b;margin:9px 0 5px;letter-spacing:-.4px}.kpi-note{font-size:11px;color:#728198}
h2{font-size:15px;margin:0 0 5px;color:#192f4a}.card-desc{font-size:11px;line-height:1.5;color:#687a90;margin-bottom:12px}
.chart{width:100%;height:230px;display:block;overflow:visible}.chart text{font-family:inherit;fill:#52647c;font-size:10px}.chart .gridline{stroke:#e9eef4;stroke-width:1}.chart .axis{stroke:#9aabba;stroke-width:1}
.chart .line{fill:none;stroke:#2373cb;stroke-width:2.5;stroke-linecap:round;stroke-linejoin:round}.chart .area{fill:#4a92e31c}
.chart .bar{fill:#3183cf}.chart .bar:nth-of-type(even){fill:#29a69b}.chart .point{fill:#258f91;stroke:white;stroke-width:1.5}.chart .baseline{fill:#2674c8;stroke:white;stroke-width:1.5}.chart .candidate{fill:#1b9a89;stroke:white;stroke-width:1.5}
.table-wrap{overflow:auto;max-height:360px}table{border-collapse:collapse;width:100%;font-size:12px}th,td{text-align:left;padding:9px 10px;border-bottom:1px solid #edf1f5;white-space:nowrap}th{position:sticky;top:0;background:#f6f8fb;color:#53667d;font-size:10px;text-transform:uppercase;letter-spacing:.5px}
.controls{display:flex;gap:10px;align-items:center;margin-bottom:14px;flex-wrap:wrap}.controls label{font-size:12px;font-weight:650}.controls select{background:#fff;border:1px solid #cbd6e2;border-radius:7px;padding:8px 10px}
.controls input[type=file],.controls input[type=search]{max-width:100%;background:#fff;border:1px solid #cbd6e2;border-radius:7px;padding:8px 10px}
.controls button{border:1px solid #cbd6e2;background:#f7f9fc;border-radius:7px;padding:8px 11px}.controls button:hover{background:#edf3fa}
.decision{display:inline-block;padding:5px 9px;border-radius:99px;font-size:10px;font-weight:750;background:#edf2f7;color:#52647b}
.decision.PASS{background:#e5f5ec;color:#176340}.decision.REGRESSION{background:#fff0eb;color:#a33c23}.decision.INCONCLUSIVE{background:#fff4db;color:#815b0c}
.notice{padding:12px 14px;background:#f4f8fc;border-left:3px solid #5b8fca;border-radius:4px;color:#50647c;font-size:12px;line-height:1.55}
.empty{padding:30px;text-align:center;border:1px dashed #c9d5e2;border-radius:10px;color:#62738a;background:#fbfcfe;font-size:13px}.source{font-size:11px;color:#566a80;line-height:1.65;overflow-wrap:anywhere}
.source strong{color:#263b55}.source a{display:inline-block;margin:4px 0}
.code{font:12px Consolas,monospace;color:#1d456d;background:#f2f6fa;padding:10px;border-radius:7px;overflow-wrap:anywhere}
.loading{color:#657790;font-size:12px;padding:14px}.chart-tooltip{font-size:10px}
@media(max-width:900px){.shell{grid-template-columns:190px minmax(0,1fr)}.kpi{grid-column:span 6}.wide,.half,.third{grid-column:span 12}}
@media(max-width:620px){.shell{display:block}aside{border-right:0;border-bottom:1px solid #dfe6ef;padding:14px}.brand{display:inline-block;padding-bottom:0}.tagline{display:inline-block;padding:0 0 0 8px}nav{display:flex;overflow-x:auto;margin-top:12px}nav button{white-space:nowrap;padding:9px}.nav-label,.side-note{display:none}main{padding:20px 14px}.kpi{grid-column:span 6}.local-badge{font-size:9px}h1{font-size:23px}}
</style>
</head>
<body>
<div class="shell">
<aside><div class="brand">BenchForge</div><div class="tagline">Systems Performance &amp; Reliability Intelligence</div>
<div class="nav-label">Workspace</div><nav>
<button class="active" data-page="overview">Overview</button>
<button data-page="explorer">Benchmark Explorer</button>
<button data-page="ab">A/B Comparison</button>
<button data-page="reliability">Reliability &amp; Regression</button>
<button data-page="public">Public Data Analytics</button>
<button data-page="quality">Data Quality &amp; Provenance</button>
<button data-page="pipelines">Pipeline Runs</button>
<button data-page="documents">PDF Analysis</button>
<button data-page="export">Run / Export</button>
</nav><div class="side-note">Local dashboard · Benchmark and pipeline history is read-only. PDF uploads are size-limited, stored locally, and never transmitted.</div></aside>
<main><header><div><div class="eyebrow">Engineering intelligence</div><h1 id="page-title">Overview</h1><div class="subtitle">Evidence-led performance analysis and public-data case studies</div></div><div class="local-badge">● Local-only · PDF uploads stay local</div></header>
<section id="overview" class="page active"><p class="page-intro">A live view of persisted benchmark history, pipeline runs, and processed case studies. Missing records are shown as empty states, never estimated.</p><div id="history-limit-note" class="notice" hidden>The dashboard limits history to the most recent 100 runs and 500 sample records. Use the CLI for complete history and exports.</div><div id="pipeline-overview" class="loading">Loading recent pipeline status…</div><div id="overview-content" class="loading">Loading local experiment history…</div></section>
<section id="explorer" class="page"><p class="page-intro">Inspect actual paired measurements, the statistical decision, and the recorded workload configuration.</p><div id="explorer-content" class="loading">Loading benchmark records…</div></section>
<section id="ab" class="page"><p class="page-intro">Compare stored baseline and candidate observations within their original matched pairs. Timing and checksums are taken from persisted samples.</p><div id="ab-content" class="loading">Loading A/B comparisons…</div></section>
<section id="reliability" class="page"><p class="page-intro">Regression decisions use the same paired comparison functions as the CLI. A stored failure count is shown only when failure records exist.</p><div id="reliability-content" class="loading">Loading reliability evidence…</div></section>
<section id="public" class="page"><p class="page-intro">Explore aggregated records from the selected public case study. Each visual is derived from the downloaded source file and its documented processing method.</p><div class="controls"><label for="dataset-select">Case study</label><select id="dataset-select"><option value="bike">UCI Bike Sharing</option><option value="retail">UCI Online Retail</option><option value="taxi">NYC Yellow Taxi · Jan 2024</option><option value="household_power">UCI Household Power</option><option value="wine_quality">UCI Wine Quality</option></select></div><div id="public-filters" class="controls"></div><div id="public-content" class="loading">Loading processed public data…</div></section>
<section id="quality" class="page"><p class="page-intro">Source provenance, schema/cleaning notes, and invalid-record counts for experiments and public data.</p><div id="quality-content" class="loading">Loading provenance…</div></section>
<section id="pipelines" class="page"><p class="page-intro">Inspect persisted local pipeline execution metrics, quality checks, and source-to-table lineage. File paths and document contents are not shown.</p><div id="pipeline-content" class="loading">Loading pipeline runs…</div></section>
<section id="documents" class="page"><p class="page-intro">PDFs are analyzed locally and are not sent to an external service. Text-based extraction only; scanned/image-only documents do not receive OCR. The upload limit is configured by the dashboard operator; analysis is also capped at 200 pages.</p><article class="card full"><h2>Upload a PDF</h2><div class="card-desc">Encrypted, malformed, oversized, and non-PDF files are rejected. Uploaded files are stored under generated names in the configured local data directory.</div><form id="pdf-upload-form" class="controls"><label for="pdf-file">PDF file</label><input id="pdf-file" type="file" data-max-bytes="__MAX_PDF_BYTES__" accept=".pdf,application/pdf" required><button type="submit">Analyze PDF</button></form><p id="pdf-status" role="status" aria-live="polite">Choose a PDF to see its page count, extracted-text preview, and statistics.</p><div id="pdf-content"></div></article></section>
<section id="export" class="page"><p class="page-intro">Export persisted data for offline analysis. The dashboard never accepts executable paths or starts benchmark processes.</p><div id="export-content" class="loading">Loading export options…</div></section>
</main></div>
<script>
const $=s=>document.querySelector(s), fmt=n=>n==null?'—':Number(n).toLocaleString(undefined,{maximumFractionDigits:2}), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])), safeUrl=s=>{try{const u=new URL(String(s));return u.protocol==='https:'?esc(u.href):'#'}catch{return '#'}};
const titles={overview:'Overview',explorer:'Benchmark Explorer',ab:'A/B Comparison',reliability:'Reliability & Regression',public:'Public Data Analytics',quality:'Data Quality & Provenance',pipelines:'Pipeline Runs',documents:'PDF Analysis',export:'Run / Export'};
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{document.querySelectorAll('nav button,.page').forEach(x=>x.classList.remove('active'));b.classList.add('active');$('#'+b.dataset.page).classList.add('active');$('#page-title').textContent=titles[b.dataset.page]});
async function api(url){const r=await fetch(url);if(!r.ok)throw Error(await r.text());return r.json()}
const publicFilterState={};
const baseApi=api;
api=async url=>{const data=await baseApi(url);if(!url.startsWith('/api/public?dataset=')||!data.available)return data;const state=publicFilterState[data.dataset]||(publicFilterState[data.dataset]={});const charts={...data.charts},months=charts.monthly_demand||charts.monthly_revenue||charts.monthly_active_power||[];let controls='';if(months.length){if(!months.some(r=>r.month===state.from))state.from=months[0].month;if(!months.some(r=>r.month===state.to))state.to=months.at(-1).month;const options=months.map(r=>`<option value="${esc(r.month)}">${esc(r.month)}</option>`).join('');controls+=`<label for="month-from">From</label><select id="month-from">${options}</select><label for="month-to">Through</label><select id="month-to">${options}</select>`;if(charts.monthly_demand)charts.monthly_demand=months.filter(r=>r.month>=state.from&&r.month<=state.to);if(charts.monthly_revenue)charts.monthly_revenue=months.filter(r=>r.month>=state.from&&r.month<=state.to);if(charts.monthly_active_power)charts.monthly_active_power=months.filter(r=>r.month>=state.from&&r.month<=state.to)}if(data.dataset==='retail'&&charts.country_revenue?.length){const countries=charts.country_revenue;if(!countries.some(r=>r.country===state.country))state.country='';controls+=`<label for="country-filter">Country (top 12)</label><select id="country-filter"><option value="">All countries</option>${countries.map(r=>`<option value="${esc(r.country)}">${esc(r.country)}</option>`).join('')}</select>`;if(state.country)charts.country_revenue=countries.filter(r=>r.country===state.country)}$('#public-filters').innerHTML=controls;const from=$('#month-from'),to=$('#month-to'),country=$('#country-filter');if(from)from.value=state.from;if(to)to.value=state.to;if(country)country.value=state.country||'';[from,to,country].filter(Boolean).forEach(control=>control.onchange=()=>{if(from)state.from=from.value;if(to)state.to=to.value;if(country)state.country=country.value;loadPublic()});return {...data,charts}}
function empty(msg){return `<div class="empty">${esc(msg)}</div>`}
function card(title,desc,chart){return `<article class="card half"><h2>${title}</h2><div class="card-desc">${desc}</div>${chart}</article>`}
function kpi(label,value,note){return `<article class="card kpi"><div class="kpi-label">${label}</div><div class="kpi-value">${value}</div><div class="kpi-note">${note||''}</div></article>`}
function svgBase(label){return `<svg class="chart" role="img" aria-label="${esc(label)}" viewBox="0 0 640 230" preserveAspectRatio="xMidYMid meet">`}
function lineChart(items,xKey,yKey,yLabel){if(!items?.length)return empty('No observations available for this chart.');let W=640,H=230,L=48,R=12,T=12,B=38;let ys=items.map(d=>+d[yKey]),min=Math.min(0,...ys),max=Math.max(...ys)||1;let x=i=>L+i*(W-L-R)/Math.max(items.length-1,1),y=v=>T+(max-v)*(H-T-B)/(max-min||1);let points=items.map((d,i)=>`${x(i)},${y(+d[yKey])}`).join(' ');return svgBase(yLabel)+`<line class="axis" x1="${L}" y1="${H-B}" x2="${W-R}" y2="${H-B}"/><line class="axis" x1="${L}" y1="${T}" x2="${L}" y2="${H-B}"/><line class="gridline" x1="${L}" y1="${y(max/2)}" x2="${W-R}" y2="${y(max/2)}"/><polyline class="line" points="${points}"/>${items.map((d,i)=>`<circle class="point" cx="${x(i)}" cy="${y(+d[yKey])}" r="3"><title>${esc(d[xKey])} · ${esc(yLabel)}: ${fmt(d[yKey])}</title></circle>`).join('')}<text x="${L}" y="${H-10}">${esc(items[0][xKey])}</text><text x="${W-R}" y="${H-10}" text-anchor="end">${esc(items.at(-1)[xKey])}</text><text x="5" y="15">${esc(yLabel)}</text><text x="${L-5}" y="${T+4}" text-anchor="end">${fmt(max)}</text><text x="${L-5}" y="${H-B}" text-anchor="end">${fmt(min)}</text></svg>`}
function barChart(items,labelKey,valueKey,yLabel){if(!items?.length)return empty('No observations available for this chart.');let W=640,H=230,L=52,R=12,T=14,B=54;let values=items.map(d=>+d[valueKey]),max=Math.max(...values)||1,gap=8,bw=Math.max(8,(W-L-R)/items.length-gap);return svgBase(yLabel)+`<line class="axis" x1="${L}" y1="${H-B}" x2="${W-R}" y2="${H-B}"/>${items.map((d,i)=>{let x=L+i*(W-L-R)/items.length+gap/2,h=(+d[valueKey])*(H-T-B)/max;let name=String(d[labelKey]);return `<rect class="bar" x="${x}" y="${H-B-h}" width="${bw}" height="${h}" rx="3"><title>${esc(name)}: ${fmt(d[valueKey])} ${esc(yLabel)}</title></rect><text x="${x+bw/2}" y="${H-B+14}" text-anchor="middle" transform="${name.length>9?`rotate(-27 ${x+bw/2} ${H-B+14})`:''}">${esc(name.slice(0,16))}</text>`}).join('')}<text x="4" y="14">${esc(yLabel)}</text></svg>`}
function scatterChart(items,xKey,yKey,xLabel,yLabel){if(!items?.length)return empty('No observations available for this chart.');let W=640,H=230,L=54,R=12,T=12,B=38,xs=items.map(d=>+d[xKey]),ys=items.map(d=>+d[yKey]),xmin=Math.min(...xs),xmax=Math.max(...xs)||1,ymin=Math.min(...ys),ymax=Math.max(...ys)||1,x=v=>L+(v-xmin)*(W-L-R)/(xmax-xmin||1),y=v=>T+(ymax-v)*(H-T-B)/(ymax-ymin||1);return svgBase(`${esc(xLabel)} versus ${esc(yLabel)}`)+`<line class="axis" x1="${L}" y1="${H-B}" x2="${W-R}" y2="${H-B}"/><line class="axis" x1="${L}" y1="${T}" x2="${L}" y2="${H-B}"/>${items.map(d=>`<circle class="point" cx="${x(+d[xKey])}" cy="${y(+d[yKey])}" r="4"><title>${esc(xLabel)}: ${fmt(d[xKey])} · ${esc(yLabel)}: ${fmt(d[yKey])} · records: ${fmt(d.records)}</title></circle>`).join('')}<text x="${(W+L-R)/2}" y="${H-5}" text-anchor="middle">${esc(xLabel)}</text><text x="3" y="14">${esc(yLabel)}</text></svg>`}
function table(rows){if(!rows?.length)return empty('No rows available.');let keys=Object.keys(rows[0]);return `<div class="table-wrap"><table><thead><tr>${keys.map(k=>`<th>${esc(k.replaceAll('_',' '))}</th>`).join('')}</tr></thead><tbody>${rows.slice(0,100).map(row=>`<tr>${keys.map(k=>`<td>${typeof row[k]==='number'?fmt(row[k]):esc(row[k]??'—')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`}
function status(decision){return `<span class="decision ${decision}">${decision}</span>`}
function metricCards(metrics){return `<div class="grid">${metrics.map(m=>kpi(m[0],m[1],m[2])).join('')}</div>`}
let benchmarkData;
async function loadOverview(){try{const d=await api('/api/benchmarks'),p=await api('/api/provenance');benchmarkData=d;$('#history-limit-note').hidden=!d.history_truncated&&!d.samples_truncated;let caseCards=p.datasets.map(x=>`<article class="card third"><h2>${esc(x.label)}</h2><div class="card-desc">${x.available?fmt(x.rows_processed)+' rows processed':esc(x.message)}</div><span class="decision ${x.available?'PASS':'INCONCLUSIVE'}">${x.available?'AVAILABLE':'MISSING'}</span></article>`).join('');$('#overview-content').innerHTML=d.run_count?metricCards([['Experiment runs',fmt(d.run_count),'Persisted SQLite run IDs'],['Valid paired comparisons',fmt(d.valid_comparisons),'At least five matched pairs'],['Regression decisions',fmt(d.regressions),'Derived by the CLI decision engine'],['Latest decision',d.latest?status(d.latest.decision):'—',d.latest?d.latest.run_id:'No eligible comparison']])+`<div class="grid">${card('Recent experiment history','Observed medians and currently derivable decision labels.',table(d.runs.slice(0,20).map(r=>({run_id:r.run_id,workload:r.workload,pairs:r.pairs,baseline_median_ns:r.baseline_median_ns,candidate_median_ns:r.candidate_median_ns,decision:r.decision}))))}</div><h2>Public case studies</h2><div class="grid">${caseCards}</div>`:`${metricCards([['Experiment runs','0','No database history found'],['Valid paired comparisons','0','No eligible comparisons'],['Regression decisions','0','No recorded failure states'],['Latest decision','—','No eligible comparison']])}${empty('No benchmark history yet. Run `benchforge ab` to create local observations.')}<h2>Public case studies</h2><div class="grid">${caseCards}</div>`;await loadExplorer();await loadAbComparison();await loadReliability();await loadExport()}catch(e){$('#overview-content').innerHTML=empty(`Could not read local history: ${e.message}`)}}
function buildSeries(rows){let variants={baseline:[],candidate:[]};for(const r of rows)variants[r.variant]?.push(r);return variants}
function renderExplorer(data){if(!data.runs.length)return empty('No benchmark runs are available.');const options=data.runs.map(r=>`<option value="${esc(r.run_id)}">${esc(r.run_id)} · ${fmt(r.pairs)} pairs · ${esc(r.workload)}</option>`).join('');return `<div class="controls"><label for="run-search">Search runs</label><input id="run-search" type="search" aria-label="Search runs by ID or workload" placeholder="Run ID or workload"><label for="run-select">Experiment</label><select id="run-select">${options}</select></div><div id="run-detail"></div>`}
function comparisonChart(rows){if(!rows.length)return empty('No paired measurements available.');let W=640,H=230,L=50,R=12,T=16,B=36,all=rows.flatMap(r=>[r.baseline_ns,r.candidate_ns]),min=Math.min(...all),max=Math.max(...all)||1,x=i=>L+i*(W-L-R)/Math.max(rows.length-1,1),y=v=>T+(max-v)*(H-T-B)/(max-min||1);return svgBase('Paired baseline and candidate latency in nanoseconds')+`<line class="axis" x1="${L}" y1="${H-B}" x2="${W-R}" y2="${H-B}"/><line class="axis" x1="${L}" y1="${T}" x2="${L}" y2="${H-B}"/>${rows.map((r,i)=>`<line x1="${x(i)}" y1="${y(r.baseline_ns)}" x2="${x(i)}" y2="${y(r.candidate_ns)}" stroke="#aab8c7"/><circle class="baseline" cx="${x(i)}" cy="${y(r.baseline_ns)}" r="4"><title>Pair ${r.pair_index} · baseline: ${fmt(r.baseline_ns)} ns</title></circle><circle class="candidate" cx="${x(i)}" cy="${y(r.candidate_ns)}" r="4"><title>Pair ${r.pair_index} · candidate: ${fmt(r.candidate_ns)} ns</title></circle>`).join('')}<text x="${L}" y="${H-8}">Pair ${rows[0].pair_index}</text><text x="${W-R}" y="${H-8}" text-anchor="end">Pair ${rows.at(-1).pair_index}</text><text x="6" y="15">Latency (ns)</text><circle class="baseline" cx="450" cy="12" r="4"/><text x="459" y="16">Baseline</text><circle class="candidate" cx="530" cy="12" r="4"/><text x="539" y="16">Candidate</text></svg>`}
function renderRun(id,targetSelector='#run-detail'){const run=benchmarkData.runs.find(r=>r.run_id===id),target=$(targetSelector);if(!run||!target)return;let rows=run.samples||[];let pairMap=new Map();rows.forEach(r=>{if(r.pair_index==null)return;let p=pairMap.get(r.pair_index)||{};p[r.variant]=r.elapsed_ns;pairMap.set(r.pair_index,p)});let pairRows=[...pairMap.entries()].sort((a,b)=>a[0]-b[0]).filter(([,p])=>p.baseline&&p.candidate).map(([pair_index,p])=>({pair_index,baseline_ns:p.baseline,candidate_ns:p.candidate}));let decision=run.decision?status(run.decision):'<span class="decision INCONCLUSIVE">INCONCLUSIVE</span>';let config='—';try{config=JSON.stringify(JSON.parse(rows[0]?.config_json||'{}'),null,2)}catch{};let evidence=run.samples_truncated?'Dashboard sample display limit reached. Use the CLI for complete analysis and export.':run.decision?`Paired ratio ${fmt(run.ratio)} · 95% CI [${fmt(run.ci_low)}, ${fmt(run.ci_high)}] · Wilcoxon p=${fmt(run.p_value)}`:'Inferential decision unavailable; at least five valid pairs are required.';target.innerHTML=`${metricCards([['Matched pairs',fmt(pairRows.length),'Observations paired by pair index'],['Baseline median',fmt(run.baseline_median_ns)+' ns','Observed elapsed time'],['Candidate median',fmt(run.candidate_median_ns)+' ns','Observed elapsed time'],['Decision',decision,run.samples_truncated?'Dashboard display limit reached':run.decision?'Actual stored-sample analysis':'Insufficient or incompatible data']])}<div class="grid">${card('Paired observation distribution',`${esc(evidence)} · elapsed time is shown in nanoseconds.`,comparisonChart(pairRows))}${card('Paired observations','Exact observed times; each row is matched by pair index.',table(pairRows))}${card('Run provenance','Workload and configuration recorded with samples.',`<pre class="code">${esc(config)}</pre>`)}</div>`}
async function loadExplorer(){try{const d=benchmarkData||await api('/api/benchmarks');benchmarkData=d;$('#explorer-content').innerHTML=renderExplorer(d);const sel=$('#run-select'),search=$('#run-search');if(sel){sel.onchange=()=>renderRun(sel.value);renderRun(sel.value)}if(search&&sel){search.oninput=()=>{const query=search.value.toLowerCase();const filtered=d.runs.filter(r=>`${r.run_id} ${r.workload}`.toLowerCase().includes(query));sel.innerHTML=filtered.map(r=>`<option value="${esc(r.run_id)}">${esc(r.run_id)} · ${fmt(r.pairs)} pairs · ${esc(r.workload)}</option>`).join('');if(filtered.length){renderRun(sel.value)}else{$('#run-detail').innerHTML=empty('No runs match that search.')}}}}catch(e){$('#explorer-content').innerHTML=empty(e.message)}}
async function loadAbComparison(){try{const d=benchmarkData||await api('/api/benchmarks');benchmarkData=d;if(!d.runs.length){$('#ab-content').innerHTML=empty('No paired A/B runs are available.');return}const options=d.runs.map(r=>`<option value="${esc(r.run_id)}">${esc(r.run_id)} · ${fmt(r.pairs)} matched pairs</option>`).join('');$('#ab-content').innerHTML=`<div class="controls"><label for="ab-run-select">A/B experiment</label><select id="ab-run-select">${options}</select></div><div id="ab-run-detail"></div>`;const selector=$('#ab-run-select');selector.onchange=()=>renderRun(selector.value,'#ab-run-detail');renderRun(selector.value,'#ab-run-detail')}catch(e){$('#ab-content').innerHTML=empty(e.message)}}
async function loadHistoric(){let current=$('#history-current')?.value,reference=$('#history-reference')?.value;if(!current||!reference||current===reference){$('#history-result').innerHTML=empty('Choose two different runs to compare.');return}try{const d=await api(`/api/history?run_id=${encodeURIComponent(current)}&reference_run_id=${encodeURIComponent(reference)}&variant=candidate`);$('#history-result').innerHTML=`<div class="notice">Current/reference median ratio: ${fmt(d.ratio)} · 95% independent bootstrap CI [${fmt(d.ci_low)}, ${fmt(d.ci_high)}] · Mann–Whitney p=${fmt(d.p_value)} · ${status(d.decision)}. Current ${fmt(d.current_count)} samples; reference ${fmt(d.reference_count)} samples.</div>`}catch(e){$('#history-result').innerHTML=empty(e.message)}}
async function loadReliability(){if(!benchmarkData) return;let eligible=benchmarkData.runs.filter(r=>r.decision);let histories=benchmarkData.runs.filter(r=>r.samples.some(s=>s.variant==='candidate'));let historyPanel=histories.length>1?`<article class="card full"><h2>Historical regression check</h2><div class="card-desc">Select a current candidate run and a reference. Workload, iterations, extra work, schema and OS must match; this is an independent-sample analysis.</div><div class="controls"><label>Current run</label><select id="history-current">${histories.map(r=>`<option value="${esc(r.run_id)}">${esc(r.run_id)}</option>`).join('')}</select><label>Reference</label><select id="history-reference">${histories.map((r,i)=>`<option value="${esc(r.run_id)}" ${i===1?'selected':''}>${esc(r.run_id)}</option>`).join('')}</select><button id="history-run">Compare history</button></div><div id="history-result"></div></article>`:empty('Historical comparison needs two stored candidate runs; no reference is chosen automatically.');$('#reliability-content').innerHTML=`<div class="grid">${eligible.length?card('Decision evidence','Actual paired CI and Wilcoxon p-value for eligible runs.',table(eligible.map(r=>({run_id:r.run_id,matched_pairs:r.pairs,ratio:r.ratio,ci_low:r.ci_low,ci_high:r.ci_high,wilcoxon_p:r.p_value,decision:r.decision})))):empty('No eligible paired comparisons. An inference requires at least five matched pairs.')}${card('Policy and caveat','PASS means the paired bootstrap CI is below the configured practical regression boundary. It is not proof of equivalence or future performance.',`<div class="notice">The dashboard uses five pairs as a minimum guardrail, not a universal sample-size recommendation. Power and uncertainty depend on variability and effect size. Failure counts are unavailable because native process failures are not persisted as experiment records.</div>`)}${historyPanel}</div>`;const button=$('#history-run');if(button){button.onclick=loadHistoric;loadHistoric()}}
async function loadPublic(){
  const which=$('#dataset-select').value;
  try{
    const d=await api('/api/public?dataset='+encodeURIComponent(which));
    if(!d.available){$('#public-content').innerHTML=empty(d.message);return}
    const c=d.charts||{},blocks=[];
    if(which==='bike'){
      blocks.push(card('Hourly demand','Mean rentals for each recorded hour across the two-year dataset.',barChart(c.hourly_demand,'hour','average_rentals','mean rentals')));
      blocks.push(card('Monthly rentals','Total recorded bike rentals by calendar month.',lineChart(c.monthly_demand,'month','rentals','rentals')));
      blocks.push(card('Temperature and demand','Average rentals by 3°C temperature band; association is not causation.',scatterChart(c.temperature_demand,'temperature_c','average_rentals','temperature °C','mean rentals')));
      blocks.push(card('Rider type','Registered and casual rental counts.',barChart(c.user_types,'type','rentals','rentals')));
    }
    if(which==='retail'){
      blocks.push(card('Monthly gross sales','Positive sales lines, GBP; not profit.',lineChart(c.monthly_revenue,'month','revenue_gbp','GBP')));
      blocks.push(card('Top products','Ten highest gross-sales descriptions.',barChart(c.top_products,'product','revenue_gbp','GBP')));
      blocks.push(card('Country sales','Gross sales by country.',barChart(c.country_revenue,'country','revenue_gbp','GBP')));
      blocks.push(card('Order value','Distribution by positive invoice value, GBP.',barChart(c.order_value_distribution,'range','orders','orders')));
    }
    if(which==='taxi'){
      blocks.push(card('Pickups by hour','Valid sample trips grouped by pickup hour in local record timestamps.',barChart(c.pickup_hour,'hour','trips','trips')));
      blocks.push(card('Trip-distance distribution','Valid sample trips by recorded mileage band.',barChart(c.trip_distance_distribution,'range','trips','trips')));
      blocks.push(card('Fare and distance','Average base fare in USD per distance band; descriptive only.',scatterChart(c.fare_distance,'distance_miles','base_fare_usd','miles','base fare USD')));
    }
    if(which==='household_power'){
      blocks.push(card('Monthly active power','Mean one-minute household active power by month, in kilowatts.',lineChart(c.monthly_active_power,'month','mean_kw','mean kW')));
      blocks.push(card('Hourly active power','Mean active power by hour of day, in kilowatts.',barChart(c.hourly_active_power,'hour','mean_kw','mean kW')));
    }
    if(which==='wine_quality'){
      blocks.push(card('Quality score distribution','Sensory quality scores across the red and white wine samples.',barChart(c.quality_distribution,'quality','samples','samples')));
      blocks.push(card('Quality and alcohol','Mean alcohol percentage by sensory score; descriptive only.',lineChart(c.alcohol_by_quality,'quality','mean_alcohol_percent','mean alcohol %')));
      blocks.push(card('Color comparison','Mean score by wine color; samples are not a consumer preference study.',barChart(c.quality_by_color,'color','mean_quality','mean score')));
    }
    const kpis=Object.entries(d.kpis||{}).map(([key,val])=>[esc(key.replaceAll('_',' ')),typeof val==='number'?fmt(val):'—','source-derived summary']);
    const notes=(Array.isArray(d.notes)?d.notes:[]).map(note=>`<li>${esc(note)}</li>`).join('');
    const source=`<div class="source"><strong>${esc(d.label)}</strong><br>Rows processed: ${fmt(d.rows_processed)}<br>Accessed: ${esc(d.access_date)}<br>${d.sample_method?esc(d.sample_method)+'<br>':''}License/terms: ${esc(d.license)}<br><a href="${safeUrl(d.source_url)}" target="_blank" rel="noreferrer">Authoritative dataset page</a><br><ul>${notes}</ul></div>`;
    $('#public-content').innerHTML=`${metricCards(kpis)}<div class="grid">${blocks.join('')}${card('Source & processing','Access date, license terms and transformations.',source)}</div>`;
  }catch(e){$('#public-content').innerHTML=empty(`Dataset summary is unavailable: ${e.message}`)}
}
async function loadQuality(){try{const d=await api('/api/provenance');$('#quality-content').innerHTML=`<div class="grid">${d.datasets.map(x=>`<article class="card third"><h2>${esc(x.label)}</h2><div class="card-desc">${x.available?fmt(x.rows_processed)+' rows processed':esc(x.message)}</div><div class="source"><strong>Source:</strong> <a href="${safeUrl(x.source_url)}" target="_blank" rel="noreferrer">${esc(x.source_url)}</a><br><strong>License/terms:</strong> ${esc(x.license)}<br><strong>Quality checks:</strong> ${x.quality?Object.entries(x.quality).map(([k,v])=>`${esc(k.replaceAll('_',' '))}: ${fmt(v)}`).join('<br>'):'Not run'}</div></article>`).join('')}${card('Benchmark data quality','Experiment records are read from the configured SQLite database.',d.database_exists?`${fmt(d.experiment_count)} run IDs are stored.`:'No local database found; the dashboard stays in an empty state.')}</div>`}catch(e){$('#quality-content').innerHTML=empty(e.message)}}
async function loadPipelines(){try{const d=await api('/api/pipelines');if(!d.runs.length){$('#pipeline-content').innerHTML=empty('No pipeline runs are stored yet. Run a local CSV ingestion, normalization, or aggregation pipeline from the CLI.');return}const rows=d.runs.map(r=>`<tr><td>${esc(r.started_at_utc)}</td><td>${esc(r.pipeline_name)}</td><td>${esc(r.source_name)}</td><td>${esc(r.destination)}</td><td>${esc(r.status)}</td><td>${fmt(r.input_rows)}</td><td>${fmt(r.output_rows)}</td><td>${fmt(r.duration_ns/1e6)} ms</td><td>${fmt(r.rows_per_second)} rows/s</td><td>${r.quality.map(q=>`${esc(q.passed?'PASS':'FAIL')}: ${esc(q.name)}`).join('<br>')||'—'}</td></tr>`).join('');$('#pipeline-content').innerHTML=`<div class="card full"><h2>Recent local pipeline runs</h2><div class="card-desc">Showing up to ${fmt(d.runs.length)} persisted runs, newest first. Source labels omit local paths.</div><div class="table-wrap"><table><thead><tr><th>Started (UTC)</th><th>Pipeline</th><th>Source</th><th>Destination</th><th>Status</th><th>Input</th><th>Output</th><th>Duration</th><th>Throughput</th><th>Quality</th></tr></thead><tbody>${rows}</tbody></table></div></div>`}catch(e){$('#pipeline-content').innerHTML=empty(`Pipeline history is unavailable: ${e.message}`)}}
async function loadExport(){try{const d=await api('/api/benchmarks');$('#export-content').innerHTML=d.runs.length?`<div class="grid">${card('Download persisted samples','CSV contains stable raw observation columns for the selected run.',`<div class="controls"><label for="csv-run">Run ID</label><select id="csv-run">${d.runs.map(r=>`<option value="${esc(r.run_id)}">${esc(r.run_id)}</option>`).join('')}</select><a id="csv-link" href="/api/export.csv?run_id=${encodeURIComponent(d.runs[0].run_id)}">Download CSV</a></div><p class="card-desc">CSV export is read-only and generated from SQLite records. Markdown reports remain available through the CLI.</p>`)}</div>`:empty('No stored rows are available for export.');const s=$('#csv-run');if(s)s.onchange=()=>$('#csv-link').href='/api/export.csv?run_id='+encodeURIComponent(s.value)}catch(e){$('#export-content').innerHTML=empty(e.message)}}
$('#dataset-select').onchange=loadPublic;document.querySelector('[data-page="public"]').addEventListener('click',loadPublic);document.querySelector('[data-page="quality"]').addEventListener('click',loadQuality);document.querySelector('[data-page="pipelines"]').addEventListener('click',loadPipelines);
async function loadPipelineOverview(){try{const d=await api('/api/pipelines'),runs=d.runs,failures=runs.filter(r=>r.status==='failed').length,qualityFailures=runs.reduce((n,r)=>n+r.quality.filter(q=>!q.passed).length,0),latest=runs[0];$('#pipeline-overview').innerHTML=metricCards([['Recent pipeline runs',fmt(runs.length),'Latest 100 stored runs'],['Recent failures',fmt(failures),'Within the displayed run window'],['Failed quality checks',fmt(qualityFailures),'Within the displayed run window'],['Latest pipeline duration',latest?fmt(latest.duration_ns/1e6)+' ms':'—',latest?esc(latest.pipeline_name):'No pipeline run recorded']])}catch(e){$('#pipeline-overview').innerHTML=empty(`Pipeline status is unavailable: ${e.message}`)}}
let activeDocumentId=null;
function renderDocument(d){$('#pdf-content').innerHTML=`<div class="grid"><article class="card half"><h2>Document analysis</h2><div class="source">Status: ${esc(d.status)}<br>Pages: ${fmt(d.page_count)}<br>Words: ${fmt(d.word_count)}<br>Extracted characters: ${fmt(d.character_count)}<br>Extraction: ${esc(d.extraction_method)}${d.text_truncated?'<br>Text was truncated at the configured extraction limit.':''}</div><pre class="code">${esc(d.text_preview||'No extractable text was found. This may be a scanned or image-only PDF; OCR is not performed.')}</pre></article><article class="card half"><h2>Search extracted text</h2><form id="pdf-search-form" class="controls"><label for="pdf-search">Keyword</label><input id="pdf-search" type="search" maxlength="128"><button type="submit">Search</button></form><div id="pdf-search-results" class="source">Enter a keyword to search this document.</div><a href="/api/documents/${encodeURIComponent(d.document_id)}/text">Download extracted text</a></article></div>`;$('#pdf-search-form').addEventListener('submit',searchDocument)}
async function searchDocument(event){event.preventDefault();const query=$('#pdf-search').value;const target=$('#pdf-search-results');target.textContent='Searching…';try{const d=await api(`/api/documents/${encodeURIComponent(activeDocumentId)}?q=${encodeURIComponent(query)}`);target.innerHTML=d.matches.length?d.matches.map(m=>`<p><strong>Page ${fmt(m.page)}:</strong> ${esc(m.snippet)}</p>`).join(''):empty('No matching text found.')}catch(e){target.textContent=`Search failed: ${e.message}`}}
$('#pdf-upload-form').addEventListener('submit',async event=>{event.preventDefault();const input=$('#pdf-file'),file=input.files[0],status=$('#pdf-status');$('#pdf-content').innerHTML='';if(!file){status.textContent='Choose a PDF file first.';return}if(!file.name.toLowerCase().endsWith('.pdf')){status.textContent='Choose a file with a .pdf extension.';return}if(file.size>Number(input.dataset.maxBytes)){status.textContent='The file exceeds the configured PDF upload limit.';return}status.textContent='Uploading and analyzing locally…';try{const response=await fetch('/api/documents',{method:'POST',headers:{'Content-Type':'application/pdf','X-File-Name':encodeURIComponent(file.name)},body:file});const result=await response.json();if(!response.ok)throw Error(result.error||'PDF analysis failed.');activeDocumentId=result.document_id;renderDocument(result);status.textContent='PDF analysis completed. The uploaded file and extracted text are stored locally.'}catch(error){status.textContent=`PDF analysis failed: ${error.message}`}})
loadOverview();loadPipelineOverview();
</script></body></html>"""


def _read_dataset(data_dir: Path, dataset: str) -> dict:
    path = data_dir / f"{dataset}.json"
    if not path.is_file():
        return {
            "available": False,
            "message": "No processed case-study data found. Follow the documented offline processing command.",
        }
    if path.is_symlink() or path.resolve().parent != data_dir.resolve():
        raise ValueError("Processed dataset files must be regular files in the configured data directory.")
    if path.stat().st_size > 2_000_000:
        raise ValueError("Processed dataset summary exceeds the 2 MB safety limit.")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("dataset") != dataset:
        raise ValueError("Processed dataset summary has an invalid shape or dataset identifier.")
    data["available"] = True
    return data


def _readonly_connection(database: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)


def _dashboard_storage_metrics(database: Path) -> dict[str, int]:
    metrics = {
        "database_ready": 0,
        "benchmark_samples": 0,
        "pipeline_runs": 0,
    }
    if not database.is_file():
        return metrics
    try:
        with _readonly_connection(database) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if "benchmark_samples" in tables:
                metrics["benchmark_samples"] = connection.execute(
                    "SELECT COUNT(*) FROM benchmark_samples"
                ).fetchone()[0]
            if "pipeline_runs" in tables:
                metrics["pipeline_runs"] = connection.execute(
                    "SELECT COUNT(*) FROM pipeline_runs"
                ).fetchone()[0]
            metrics["database_ready"] = 1
    except (OSError, sqlite3.Error):
        return metrics
    return metrics


def _prometheus_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _read_runs(database: Path) -> tuple[list[dict], bool, bool, int]:
    if not database.is_file():
        return [], False, False, 0
    with _readonly_connection(database) as connection:
        connection.row_factory = sqlite3.Row
        total_runs = connection.execute(
            "SELECT COUNT(DISTINCT run_id) FROM benchmark_samples"
        ).fetchone()[0]
        run_rows = connection.execute(
            """
            SELECT
                CASE WHEN length(run_id) <= 256
                    THEN run_id
                    ELSE '[oversized run ID #' || MIN(id) || ']' END AS run_id,
                length(run_id) > 256 AS run_id_truncated,
                substr(MIN(timestamp_utc), 1, 256) AS started_at,
                MIN(substr(workload, 1, 128)) AS workload
            FROM benchmark_samples
            GROUP BY run_id
            ORDER BY started_at DESC, run_id
            LIMIT ?
            """,
            (_MAX_DASHBOARD_RUNS + 1,),
        ).fetchall()
        history_truncated = total_runs > _MAX_DASHBOARD_RUNS
        run_rows = run_rows[:_MAX_DASHBOARD_RUNS]
        runs = []
        samples_remaining = _MAX_DASHBOARD_SAMPLES_TOTAL
        any_samples_truncated = False
        for item in run_rows:
            if item["run_id_truncated"]:
                samples = []
                samples_truncated = True
            else:
                row_count = connection.execute(
                    "SELECT COUNT(*) FROM benchmark_samples WHERE run_id = ?",
                    (item["run_id"],),
                ).fetchone()[0]
                unsafe_text = connection.execute(
                    """
                    SELECT 1 FROM benchmark_samples
                    WHERE run_id = ?
                    AND MAX(
                        length(COALESCE(timestamp_utc, '')),
                        length(COALESCE(workload, '')),
                        length(COALESCE(os, '')),
                        length(COALESCE(python_version, '')),
                        length(COALESCE(variant, '')),
                        length(COALESCE(binary, '')),
                        length(COALESCE(config_json, ''))
                    ) > ?
                    LIMIT 1
                    """,
                    (item["run_id"], _MAX_DASHBOARD_TEXT_CELL_CHARS),
                ).fetchone()
                run_sample_limit = min(
                    _MAX_DASHBOARD_SAMPLES_PER_RUN,
                    samples_remaining,
                )
                samples_truncated = (
                    row_count > run_sample_limit or unsafe_text is not None
                )
                samples = []
                if not samples_truncated:
                    samples = [
                        dict(row)
                        for row in connection.execute(
                            """
                            SELECT variant, pair_index, elapsed_ns, config_json
                            FROM benchmark_samples
                            WHERE run_id = ?
                            ORDER BY variant, sample_index, id
                            LIMIT ?
                            """,
                            (item["run_id"], run_sample_limit + 1),
                        ).fetchall()
                    ]
                    samples_truncated = len(samples) > run_sample_limit
                    if samples_truncated:
                        samples = []
                    else:
                        samples_remaining -= len(samples)
            any_samples_truncated = any_samples_truncated or samples_truncated
            if samples_truncated:
                samples = []
            variants: dict[str, list[dict]] = {"baseline": [], "candidate": []}
            for sample in samples:
                variants.setdefault(sample["variant"], []).append(sample)
            baselines = variants["baseline"]
            candidates = variants["candidate"]
            common = {
                "run_id": item["run_id"],
                "started_at": item["started_at"],
                "workload": item["workload"] or "Unknown",
                "pairs": 0,
                "decision": None,
                "ratio": None,
                "ci_low": None,
                "ci_high": None,
                "p_value": None,
                "baseline_median_ns": None,
                "candidate_median_ns": None,
                "samples": samples,
                "samples_truncated": samples_truncated,
            }
            if not samples_truncated and baselines and candidates:
                base_by_pair = {
                    row["pair_index"]: row["elapsed_ns"]
                    for row in baselines
                    if row["pair_index"] is not None
                }
                candidate_by_pair = {
                    row["pair_index"]: row["elapsed_ns"]
                    for row in candidates
                    if row["pair_index"] is not None
                }
                pair_ids = sorted(set(base_by_pair) & set(candidate_by_pair))
                common["pairs"] = len(pair_ids)
                if pair_ids:
                    import statistics

                    base_values = [base_by_pair[index] for index in pair_ids]
                    candidate_values = [candidate_by_pair[index] for index in pair_ids]
                    common["baseline_median_ns"] = statistics.median(base_values)
                    common["candidate_median_ns"] = statistics.median(candidate_values)
                    if len(pair_ids) >= MINIMUM_PAIRED_OBSERVATIONS:
                        try:
                            low, high = bootstrap_paired_median_ratio_ci(
                                base_values, candidate_values, n_resamples=500
                            )
                            p_value = wilcoxon_paired_pvalue(
                                base_values, candidate_values
                            )
                            common.update(
                                ratio=paired_median_ratio(base_values, candidate_values),
                                ci_low=low,
                                ci_high=high,
                                p_value=p_value,
                                decision=classify_regression(
                                    low, high, p_value=p_value
                                ),
                            )
                        except (TypeError, ValueError):
                            common["decision"] = "INCONCLUSIVE"
            runs.append(common)
    return runs, history_truncated, any_samples_truncated, total_runs


def _json_response(
    handler: BaseHTTPRequestHandler,
    payload: dict,
    *,
    status: int = 200,
) -> None:
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def create_handler(
    database: Path,
    data_dir: Path,
    documents_dir: Path | None = None,
    max_pdf_bytes: int = MAX_PDF_BYTES,
    *,
    allow_remote_hosts: bool = False,
):
    max_pdf_bytes = validate_max_pdf_bytes(max_pdf_bytes)
    database = Path(database).resolve()
    data_dir = Path(data_dir).resolve()
    document_directory = (
        Path(documents_dir)
        if documents_dir is not None
        else database.parent / "documents"
    ).resolve()
    request_counts: Counter[tuple[str, str, int]] = Counter()
    request_duration = {"count": 0, "total_seconds": 0.0, "max_seconds": 0.0}
    metrics_lock = threading.Lock()

    def metric_route(path: str) -> str:
        if path.startswith("/api/documents/"):
            if path.endswith("/text"):
                return "/api/documents/{id}/text"
            return "/api/documents/{id}"
        if path in {
            "/",
            "/healthz",
            "/readyz",
            "/metrics",
            "/metrics/prometheus",
            "/api/benchmarks",
            "/api/public",
            "/api/provenance",
            "/api/pipelines",
            "/api/history",
            "/api/export.csv",
            "/api/documents",
        }:
            return path
        return "other"

    class DashboardHandler(BaseHTTPRequestHandler):
        def parse_request(self) -> bool:
            if not super().parse_request():
                return False
            if not allow_remote_hosts and not _is_loopback_host_header(
                self.headers.get("Host", ""),
                self.server.server_address[1],
            ):
                self.send_error(421, "Request Host must be a loopback address.")
                return False
            return True

        def handle_one_request(self) -> None:
            started = perf_counter()
            self._response_status = 0
            try:
                super().handle_one_request()
            finally:
                if self.command:
                    request_method = self.command.upper()
                    method = (
                        request_method
                        if request_method
                        in {"GET", "HEAD", "POST", "PUT", "DELETE", "CONNECT", "OPTIONS", "TRACE", "PATCH"}
                        else "OTHER"
                    )
                    route = metric_route(urlparse(self.path).path)
                    status = self._response_status
                    elapsed = perf_counter() - started
                    with metrics_lock:
                        request_counts[(method, route, status)] += 1
                        request_duration["count"] += 1
                        request_duration["total_seconds"] += elapsed
                        request_duration["max_seconds"] = max(
                            request_duration["max_seconds"], elapsed
                        )
                    level = (
                        logging.ERROR if status >= 500 else
                        logging.WARNING if status >= 400 else
                        logging.INFO
                    )
                    _LOGGER.log(
                        level,
                        "dashboard_request_completed method=%s route=%s "
                        "status=%d duration_seconds=%.6f",
                        method,
                        route,
                        status,
                        elapsed,
                        extra={
                            "http_method": method,
                            "route": route,
                            "status_code": status,
                            "duration_seconds": elapsed,
                        },
                    )

        def send_response(self, code: int, message: str | None = None) -> None:
            self._response_status = code
            super().send_response(code, message)

        def do_GET(self) -> None:
            request = urlparse(self.path)
            if request.path == "/":
                body = _PAGE.replace(
                    "__MAX_PDF_BYTES__", str(max_pdf_bytes)
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if request.path == "/healthz":
                _json_response(self, {"status": "ok"})
                return
            if request.path == "/readyz":
                try:
                    if database.exists():
                        if not database.is_file():
                            raise sqlite3.OperationalError(
                                "Configured database path is not a file."
                            )
                        with _readonly_connection(database) as connection:
                            connection.execute("SELECT 1").fetchone()
                except (OSError, sqlite3.Error):
                    _json_response(
                        self,
                        {"status": "not_ready"},
                        status=503,
                    )
                    return
                _json_response(self, {"status": "ready"})
                return
            if request.path == "/metrics":
                with metrics_lock:
                    counts = [
                        {
                            "method": method,
                            "route": route,
                            "status": status,
                            "count": count,
                        }
                        for (method, route, status), count in sorted(
                            request_counts.items()
                        )
                    ]
                    duration = dict(request_duration)
                storage_metrics = _dashboard_storage_metrics(database)
                _json_response(
                    self,
                    {
                        "requests": counts,
                        "request_duration_seconds": duration,
                        "storage": storage_metrics,
                    },
                )
                return
            if request.path == "/metrics/prometheus":
                with metrics_lock:
                    counts = list(request_counts.items())
                    duration = dict(request_duration)
                storage_metrics = _dashboard_storage_metrics(database)
                error_count = sum(
                    count for (_, _, status), count in counts if status >= 400
                )
                server_error_count = sum(
                    count for (_, _, status), count in counts if status >= 500
                )
                lines = [
                    "# HELP benchforge_http_requests_total Completed HTTP requests.",
                    "# TYPE benchforge_http_requests_total counter",
                ]
                for (method, route, status), count in sorted(counts):
                    lines.append(
                        "benchforge_http_requests_total"
                        f'{{method="{_prometheus_label(method)}",'
                        f'route="{_prometheus_label(route)}",status="{status}"}} {count}'
                    )
                lines.extend(
                    [
                        "# HELP benchforge_http_errors_total Completed 4xx and 5xx requests.",
                        "# TYPE benchforge_http_errors_total counter",
                        f"benchforge_http_errors_total {error_count}",
                        "# HELP benchforge_http_server_errors_total Completed 5xx requests.",
                        "# TYPE benchforge_http_server_errors_total counter",
                        f"benchforge_http_server_errors_total {server_error_count}",
                        "# HELP benchforge_http_request_duration_seconds Request duration summary.",
                        "# TYPE benchforge_http_request_duration_seconds summary",
                        (
                            "benchforge_http_request_duration_seconds_count "
                            f"{duration['count']}"
                        ),
                        (
                            "benchforge_http_request_duration_seconds_sum "
                            f"{duration['total_seconds']:.9g}"
                        ),
                        "# HELP benchforge_http_request_duration_seconds_max Maximum observed request duration.",
                        "# TYPE benchforge_http_request_duration_seconds_max gauge",
                        (
                            "benchforge_http_request_duration_seconds_max "
                            f"{duration['max_seconds']:.9g}"
                        ),
                        "# HELP benchforge_storage_database_ready Whether the local history database is readable.",
                        "# TYPE benchforge_storage_database_ready gauge",
                        f"benchforge_storage_database_ready {storage_metrics['database_ready']}",
                        "# HELP benchforge_benchmark_samples Number of stored benchmark samples.",
                        "# TYPE benchforge_benchmark_samples gauge",
                        f"benchforge_benchmark_samples {storage_metrics['benchmark_samples']}",
                        "# HELP benchforge_pipeline_runs Number of stored pipeline runs.",
                        "# TYPE benchforge_pipeline_runs gauge",
                        f"benchforge_pipeline_runs {storage_metrics['pipeline_runs']}",
                        "",
                    ]
                )
                body = "\n".join(lines).encode("utf-8")
                self.send_response(200)
                self.send_header(
                    "Content-Type", "text/plain; version=0.0.4; charset=utf-8"
                )
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if request.path == "/api/benchmarks":
                try:
                    (
                        runs,
                        history_truncated,
                        samples_truncated,
                        total_runs,
                    ) = _read_runs(database)
                except (OSError, sqlite3.Error) as exc:
                    _json_response(self, {"error": str(exc)}, status=500)
                    return
                _json_response(
                    self,
                    {
                        "runs": runs,
                        "history_truncated": history_truncated,
                        "samples_truncated": samples_truncated,
                        "run_count": total_runs,
                        "valid_comparisons": sum(run["decision"] is not None for run in runs),
                        "regressions": sum(run["decision"] == "REGRESSION" for run in runs),
                        "latest": next(
                            (run for run in runs if run["decision"] is not None), None
                        ),
                    },
                )
                return
            if request.path == "/api/public":
                dataset = parse_qs(request.query).get("dataset", [""])[0]
                if dataset not in _DATASETS:
                    self.send_error(400, "Unknown public dataset.")
                    return
                try:
                    payload = _read_dataset(data_dir, dataset)
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    _json_response(self, {"error": str(exc)}, status=500)
                    return
                _json_response(self, payload)
                return
            if request.path == "/api/provenance":
                datasets = []
                try:
                    for key in sorted(_DATASETS):
                        data = _read_dataset(data_dir, key)
                        datasets.append(
                            {
                                "dataset": key,
                                "label": data.get("label", key.title()),
                                "source_url": data.get(
                                    "source_url",
                                    "https://archive.uci.edu/",
                                ),
                                "license": data.get("license", "Check the authoritative source."),
                                "available": data.get("available", False),
                                "rows_processed": data.get("rows_processed"),
                                "quality": data.get("quality"),
                                "message": data.get("message"),
                            }
                        )
                    _, _, _, total_runs = _read_runs(database)
                except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
                    _json_response(self, {"error": str(exc)}, status=500)
                    return
                _json_response(
                    self,
                    {
                        "datasets": datasets,
                        "database_exists": database.is_file(),
                        "experiment_count": total_runs,
                    },
                )
                return
            if request.path == "/api/pipelines":
                try:
                    from .pipeline import get_pipeline_runs

                    runs = get_pipeline_runs(database)
                except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
                    _json_response(self, {"error": str(exc)}, status=500)
                    return
                _json_response(self, {"runs": runs, "limit": 100})
                return
            if request.path.startswith("/api/documents/"):
                try:
                    peer_is_loopback = ipaddress.ip_address(
                        self.client_address[0]
                    ).is_loopback
                except ValueError:
                    peer_is_loopback = False
                if not peer_is_loopback:
                    _json_response(
                        self,
                        {"error": "PDF documents are available only to loopback clients."},
                        status=403,
                    )
                    return
                path = request.path.removeprefix("/api/documents/")
                if path.endswith("/text"):
                    document_id = path.removesuffix("/text")
                    if re.fullmatch(r"[0-9a-f]{32}", document_id) is None:
                        _json_response(self, {"error": "Invalid document ID."}, status=400)
                        return
                    try:
                        document = read_document(document_directory, document_id)
                    except FileNotFoundError:
                        _json_response(self, {"error": "Document not found."}, status=404)
                        return
                    except (OSError, ValueError, json.JSONDecodeError) as exc:
                        self.log_error("Could not read stored PDF analysis: %s", type(exc).__name__)
                        _json_response(
                            self,
                            {"error": "Stored PDF analysis could not be read."},
                            status=500,
                        )
                        return
                    body = document["text"].encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header(
                        "Content-Disposition",
                        'attachment; filename="benchforge-extracted-text.txt"',
                    )
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                document_id = path
                if re.fullmatch(r"[0-9a-f]{32}", document_id) is None:
                    _json_response(self, {"error": "Invalid document ID."}, status=400)
                    return
                try:
                    document = read_document(document_directory, document_id)
                except FileNotFoundError:
                    _json_response(self, {"error": "Document not found."}, status=404)
                    return
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    self.log_error("Could not read stored PDF analysis: %s", type(exc).__name__)
                    _json_response(
                        self,
                        {"error": "Stored PDF analysis could not be read."},
                        status=500,
                    )
                    return
                try:
                    matches = search_document(
                        document,
                        parse_qs(request.query).get("q", [""])[0],
                    )
                except ValueError as exc:
                    _json_response(self, {"error": str(exc)}, status=400)
                    return
                _json_response(self, {**public_document(document), "matches": matches})
                return
            if request.path == "/api/history":
                query = parse_qs(request.query)
                run_id = query.get("run_id", [""])[0]
                reference_run_id = query.get("reference_run_id", [""])[0]
                variant = query.get("variant", ["candidate"])[0]
                if not database.is_file():
                    _json_response(
                        self,
                        {"error": "No benchmark database is available."},
                        status=404,
                    )
                    return
                try:
                    result = compare_historical_runs(
                        database,
                        run_id=run_id,
                        reference_run_id=reference_run_id,
                        variant=variant,
                        n_resamples=1000,
                    )
                except ValueError as exc:
                    _json_response(self, {"error": str(exc)}, status=409)
                    return
                except sqlite3.Error as exc:
                    _json_response(self, {"error": str(exc)}, status=500)
                    return
                _json_response(self, result)
                return
            if request.path == "/api/export.csv":
                run_id = parse_qs(request.query).get("run_id", [""])[0]
                if not database.is_file() or not run_id:
                    self.send_error(404, "Run ID not found.")
                    return
                output = io.StringIO(newline="")
                writer = csv.writer(output)
                writer.writerow(_COLUMNS)
                try:
                    with _readonly_connection(database) as connection:
                        validate_export_size(connection, run_id=run_id)
                        rows = connection.execute(
                            "SELECT "
                            + ", ".join(_COLUMNS)
                            + " FROM benchmark_samples WHERE run_id = ? "
                            "ORDER BY variant, sample_index, id",
                            (run_id,),
                        )
                        found = False
                        for row in rows:
                            found = True
                            writer.writerow(
                                [spreadsheet_safe_cell(value) for value in row]
                            )
                            if output.tell() * 4 > MAX_EXPORT_BYTES:
                                _json_response(
                                    self,
                                    {
                                        "error": (
                                            "CSV export exceeds the "
                                            f"{MAX_EXPORT_BYTES:,}-byte limit."
                                        )
                                    },
                                    status=413,
                                )
                                return
                except ValueError as exc:
                    _json_response(self, {"error": str(exc)}, status=413)
                    return
                except sqlite3.Error as exc:
                    _json_response(self, {"error": str(exc)}, status=500)
                    return
                if not found:
                    self.send_error(404, "Run ID not found.")
                    return
                body = output.getvalue().encode("utf-8-sig")
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header(
                    "Content-Disposition",
                    'attachment; filename="benchforge-export.csv"',
                )
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_error(404, "Not found.")

        def do_POST(self) -> None:
            request = urlparse(self.path)
            if request.path != "/api/documents":
                _json_response(self, {"error": "Not found."}, status=404)
                return
            try:
                peer_is_loopback = ipaddress.ip_address(
                    self.client_address[0]
                ).is_loopback
            except ValueError:
                peer_is_loopback = False
            if not peer_is_loopback:
                _json_response(
                    self,
                    {"error": "PDF uploads are available only to loopback clients."},
                    status=403,
                )
                return
            if self.headers.get("Transfer-Encoding"):
                _json_response(
                    self,
                    {"error": "Transfer-encoded upload requests are not supported."},
                    status=400,
                )
                return
            if self.headers.get_content_type().lower() != "application/pdf":
                _json_response(
                    self,
                    {"error": "Content-Type must be application/pdf."},
                    status=415,
                )
                return
            encoded_filename = self.headers.get("X-File-Name", "")
            if not encoded_filename or len(encoded_filename) > 3 * 1_024:
                _json_response(self, {"error": "A valid PDF filename is required."}, status=400)
                return
            content_length_header = self.headers.get("Content-Length")
            if content_length_header is None:
                _json_response(
                    self,
                    {"error": "Content-Length is required for PDF uploads."},
                    status=411,
                )
                return
            try:
                filename = unquote(encoded_filename, errors="strict")
                content_length = int(content_length_header)
            except (UnicodeDecodeError, ValueError):
                _json_response(
                    self,
                    {"error": "The upload filename or Content-Length is invalid."},
                    status=400,
                )
                return
            if content_length < 1:
                _json_response(self, {"error": "The uploaded PDF is empty."}, status=422)
                return
            if content_length > max_pdf_bytes:
                _json_response(
                    self,
                    {"error": f"PDF exceeds the {max_pdf_bytes:,}-byte upload limit."},
                    status=413,
                )
                return
            self.connection.settimeout(15)
            try:
                data = self.rfile.read(content_length)
            except TimeoutError:
                _json_response(
                    self,
                    {"error": "The PDF upload timed out."},
                    status=408,
                )
                return
            if len(data) != content_length:
                _json_response(
                    self,
                    {"error": "The upload ended before the declared Content-Length."},
                    status=400,
                )
                return
            try:
                result = store_pdf(
                    document_directory,
                    data,
                    filename,
                    max_bytes=max_pdf_bytes,
                )
            except ValueError as exc:
                _json_response(self, {"error": str(exc)}, status=422)
                return
            except OSError as exc:
                self.log_error("Could not store PDF upload: %s", type(exc).__name__)
                _json_response(
                    self,
                    {"error": "The PDF analysis could not be stored locally."},
                    status=500,
                )
                return
            _json_response(self, result, status=201)

        def log_message(self, format: str, *args: object) -> None:
            _LOGGER.debug(
                "dashboard_http_server_message",
                extra={
                    "route": metric_route(urlparse(self.path).path),
                    "message_template": format,
                },
            )

    return DashboardHandler


def serve(
    database: Path,
    data_dir: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    allow_remote: bool = False,
    documents_dir: Path | None = None,
    max_pdf_bytes: int = MAX_PDF_BYTES,
) -> None:
    """Run on loopback by default; remote binds require an explicit opt-in."""
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
    try:
        is_loopback = host.lower() == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback and not allow_remote:
        raise ValueError(
            "Dashboard host must be loopback unless --allow-remote is explicitly set."
        )
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if host.lower() != "localhost":
            raise ValueError("Dashboard host must be a loopback name or IP address.")
    if isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535.")
    server = ThreadingHTTPServer(
        (host, port),
        create_handler(
            database,
            data_dir,
            documents_dir,
            max_pdf_bytes,
            allow_remote_hosts=allow_remote,
        ),
    )
    print(f"BenchForge dashboard available at http://{host}:{server.server_port}")
    previous_sigterm = None
    in_main_thread = threading.current_thread() is threading.main_thread()
    if in_main_thread:
        previous_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(
            signal.SIGTERM,
            lambda signum, frame: threading.Thread(
                target=server.shutdown, daemon=True
            ).start(),
        )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard.")
    finally:
        if in_main_thread and previous_sigterm is not None:
            signal.signal(signal.SIGTERM, previous_sigterm)
        server.server_close()
