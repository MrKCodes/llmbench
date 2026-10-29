"""Build a self-contained, offline HTML dashboard from result sessions.

No network, no CDN, no build step: the result data is embedded as JSON and
rendered by vanilla-JS canvas/DOM in the page. Open the file in any browser.

Views:
- Leaderboard podium: which machine wins the most cells for the chosen metric.
- Comparison: a filterable toolbar (model/backend/format/OS/machine), a
  throughput-vs-TTFT scatter with Pareto frontier, a machine x cell heatmap, and
  grouped whisker bars + a sortable table. A pinned baseline flips the numbers
  to relative speedup (x).
- Machine cards (hardware + session counts).
- Per-machine deep dive: pooled stats plus a per-session line chart so you can
  see how re-runs on the same box vary (consistency / drift).
- Errors table.

All numbers come from loader.py, the same source the Markdown report uses. The
on-disk session JSON schema is never touched; only this derived payload and the
page markup change.
"""

from __future__ import annotations

import json
import time
from typing import Dict, List, Optional

from .loader import (
    METRICS,
    Machine,
    all_cell_keys,
    all_models,
    group_by_machine,
    load_sessions,
)


def _cell_id(backend: str, model: str, fmt: str, prompt_id: str) -> str:
    return f"{backend} / {model} / {fmt} / {prompt_id}"


def _median(summary: Optional[Dict], metric: str) -> Optional[float]:
    if summary and summary.get(metric):
        return summary[metric].get("median")
    return None


def _stat_block(summary: Optional[Dict], metric: str) -> Optional[Dict]:
    """Compact per-(metric,cell,machine) stat for bars/whiskers/scatter.

    Returns None when the machine produced no data for this cell, so the page
    can render a missing cell as "—" rather than a misleading zero.
    """
    s = (summary or {}).get(metric) or {}
    if s.get("median") is None:
        return None
    return {
        "v": s.get("median"),
        "sd": s.get("stdev"),
        "lo": s.get("min"),
        "hi": s.get("max"),
        "n": s.get("n"),
    }


def _build_payload(machines: List[Machine]) -> Dict:
    keys = all_cell_keys(machines)
    models = all_models(machines)

    machine_meta = []
    for m in machines:
        hw = m.hw
        machine_meta.append({
            "slug": m.slug,
            "label": m.label,
            "os": hw.get("os"),
            "arch": hw.get("arch"),
            "cpu": hw.get("cpu"),
            "ram_gb": hw.get("ram_gb"),
            "gpus": hw.get("gpus", []),
            "sessions": len(m.sessions),
        })

    cells = [{
        "id": _cell_id(*k),
        "backend": k[0],
        "model": k[1],
        "format": k[2],
        "prompt_id": k[3],
        "size": models.get(k[1]),
    } for k in keys]

    # comparison[metric][cell_id][machine_slug] = stat block (pooled) or null.
    comparison: Dict = {metric: {} for metric, *_ in METRICS}
    for k in keys:
        cid = _cell_id(*k)
        for metric, *_ in METRICS:
            comparison[metric].setdefault(cid, {})
        for m in machines:
            summ = m.pooled_summary(k)
            for metric, *_ in METRICS:
                comparison[metric][cid][m.slug] = _stat_block(summ, metric)

    # deep[machine_slug][cell_id] = {pooled stats, per-session series}
    deep: Dict = {}
    for m in machines:
        deep[m.slug] = {}
        for k in m.cell_keys():
            cid = _cell_id(*k)
            summ = m.pooled_summary(k) or {}
            sessions = m.session_summaries(k)
            series = []
            for i, s in enumerate(sessions, 1):
                series.append({
                    "label": f"s{i}",
                    "n_runs": s["n_runs"],
                    "gen": _median(s.get("summary"), "gen_tokens_per_s"),
                    "ttft": _median(s.get("summary"), "ttft_s"),
                    "total": _median(s.get("summary"), "total_s"),
                    "error": s.get("error"),
                })
            deep[m.slug][cid] = {
                "pooled": {
                    metric: {
                        "median": _median(summ, metric),
                        "stdev": (summ.get(metric) or {}).get("stdev"),
                        "min": (summ.get(metric) or {}).get("min"),
                        "max": (summ.get(metric) or {}).get("max"),
                        "n": (summ.get(metric) or {}).get("n"),
                    } for metric, *_ in METRICS
                },
                "sessions": series,
            }

    errors = []
    for m in machines:
        for e in m.errors():
            errors.append({"machine": m.label, **e})

    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "metrics": [
            {"key": metric, "label": label, "higher_is_better": hib, "digits": d}
            for metric, label, hib, d in METRICS
        ],
        "machines": machine_meta,
        "cells": cells,
        "comparison": comparison,
        "deep": deep,
        "errors": errors,
    }


def build_dashboard(results_dir: str) -> str:
    machines = group_by_machine(load_sessions(results_dir))
    payload = _build_payload(machines)
    data_json = json.dumps(payload)
    # Guard against any "</script>" inside strings closing our script tag early.
    data_json = data_json.replace("</", "<\\/")
    return _HTML_TEMPLATE.replace("/*__DATA__*/", data_json)


# --- the page: structure (HTML) + style (CSS) + behavior (JS) ----------------
# Charts are drawn on <canvas> with hand-rolled drawing code so the file needs
# no libraries and works fully offline. The heatmap is a plain DOM grid.

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>llmbench dashboard</title>
<script>
  // Set theme before first paint to avoid a flash.
  try {
    var t = localStorage.getItem('llmbench-theme') ||
      (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
    document.documentElement.setAttribute('data-theme', t);
  } catch (e) {}
</script>
<style>
  :root {
    --bg:#0b0e14; --bg-2:#0f131c; --panel:#141925; --panel-2:#1b2130;
    --panel-3:#232b3d; --ink:#eaeef6; --muted:#93a0b5; --faint:#677087;
    --line:#273041; --line-2:#313c52; --accent:#6ea8fe; --accent-ink:#0b0e14;
    --accent-dim:#2d3f63; --good:#56d364; --bad:#ff6b63; --warn:#e3b341;
    --gold:#e8c24a; --silver:#c2cada; --bronze:#cd8b5a;
    --radius:12px; --radius-sm:8px;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 6px 20px rgba(0,0,0,.28);
    --shadow-sm:0 1px 2px rgba(0,0,0,.35);
    --mono:ui-monospace,"SF Mono","Cascadia Code","JetBrains Mono",Menlo,Consolas,monospace;
    --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  }
  [data-theme="light"] {
    --bg:#f4f6fb; --bg-2:#eef1f7; --panel:#ffffff; --panel-2:#f1f4f9;
    --panel-3:#e7ecf4; --ink:#121722; --muted:#5a6475; --faint:#8a93a4;
    --line:#dde3ee; --line-2:#cdd5e3; --accent:#2563c9; --accent-ink:#ffffff;
    --accent-dim:#cfe0ff; --good:#1a8f3c; --bad:#c62f27; --warn:#9a6b00;
    --shadow:0 1px 2px rgba(20,30,60,.08), 0 6px 20px rgba(20,30,60,.08);
    --shadow-sm:0 1px 2px rgba(20,30,60,.08);
  }
  * { box-sizing: border-box; }
  body {
    margin:0; color:var(--ink); font:13px/1.5 var(--sans);
    background:linear-gradient(180deg,var(--bg),var(--bg-2)) fixed;
  }
  a { color:var(--accent); }

  header {
    position:sticky; top:0; z-index:30; padding:12px 32px;
    border-bottom:1px solid var(--line);
    background:color-mix(in srgb, var(--bg) 85%, transparent);
    backdrop-filter:blur(8px);
    display:flex; align-items:center; gap:16px; flex-wrap:wrap;
  }
  header h1 { margin:0; font-size:18px; font-weight:700; letter-spacing:-.01em; }
  header .meta { color:var(--muted); font-size:11px; font-family:var(--mono);
    display:flex; gap:10px; flex-wrap:wrap; }
  header .meta b { color:var(--ink); font-weight:600; }
  nav { margin-left:auto; display:flex; gap:4px; flex-wrap:wrap; }
  nav a { color:var(--muted); text-decoration:none; font-size:12px;
    padding:4px 10px; border-radius:999px; }
  nav a:hover { color:var(--ink); background:var(--panel-2); }
  nav a.active { color:var(--accent); background:var(--accent-dim); }
  .theme-btn { background:var(--panel-2); color:var(--ink); border:1px solid var(--line);
    border-radius:999px; width:30px; height:30px; cursor:pointer; font-size:14px; }
  .theme-btn:hover { border-color:var(--line-2); }

  main { padding:24px 32px 64px; max-width:1280px; margin:0 auto; }
  section { scroll-margin-top:72px; }
  h2 { font-size:18px; margin:32px 0 12px; font-weight:700;
    padding-left:10px; border-left:3px solid var(--accent); }
  h3 { font-size:12px; margin:16px 0 8px; color:var(--muted); font-weight:600;
    text-transform:uppercase; letter-spacing:.06em; }

  .panel { background:var(--panel); border:1px solid var(--line);
    border-radius:var(--radius); padding:16px; box-shadow:var(--shadow-sm); }

  /* cards */
  .cards { display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:16px; }
  .card { background:var(--panel); border:1px solid var(--line); border-left:3px solid var(--line-2);
    border-radius:var(--radius); padding:14px; box-shadow:var(--shadow-sm);
    transition:transform .15s ease, box-shadow .15s ease, border-color .15s ease; }
  .card:hover { transform:translateY(-1px); box-shadow:var(--shadow); border-color:var(--line-2); }
  .card .name { font-weight:700; font-size:15px; }
  .card .slug { color:var(--muted); font-size:11px; font-family:var(--mono); margin-top:2px; word-break:break-all; }
  .card dl { margin:10px 0 0; display:grid; grid-template-columns:auto 1fr; gap:3px 10px; font-size:12px; }
  .card dt { color:var(--muted); } .card dd { margin:0; font-family:var(--mono); }
  .badge { display:inline-block; background:var(--panel-2); border:1px solid var(--line);
    border-radius:999px; padding:1px 8px; font-size:11px; color:var(--muted); }
  .os { display:inline-block; border-radius:999px; padding:1px 8px; font-size:11px;
    font-weight:600; border:1px solid var(--line); }
  .os-mac { color:var(--ink); background:var(--panel-2); }
  .os-win { color:#4aa3ff; background:color-mix(in srgb,#4aa3ff 14%, transparent); }
  .os-other { color:var(--muted); background:var(--panel-2); }

  /* toolbar */
  .toolbar { position:sticky; top:55px; z-index:20; margin:4px 0 16px;
    background:color-mix(in srgb, var(--bg) 88%, transparent); backdrop-filter:blur(8px);
    border:1px solid var(--line); border-radius:var(--radius); padding:12px 14px;
    display:flex; flex-direction:column; gap:10px; box-shadow:var(--shadow-sm); }
  .trow { display:flex; gap:12px 18px; align-items:center; flex-wrap:wrap; }
  .fgroup { display:flex; align-items:center; gap:6px; flex-wrap:wrap; }
  .fgroup > .lbl { color:var(--muted); font-size:11px; text-transform:uppercase;
    letter-spacing:.05em; margin-right:2px; }
  select { background:var(--panel-2); color:var(--ink); border:1px solid var(--line);
    border-radius:var(--radius-sm); padding:6px 10px; font-size:13px; font-family:var(--sans); }
  .seg { display:inline-flex; border:1px solid var(--line); border-radius:var(--radius-sm);
    overflow:hidden; }
  .seg button { background:var(--panel-2); color:var(--muted); border:0; padding:5px 11px;
    font-size:12px; cursor:pointer; border-right:1px solid var(--line); font-family:var(--sans); }
  .seg button:last-child { border-right:0; }
  .seg button.on { background:var(--accent); color:var(--accent-ink); font-weight:600; }
  .seg button:hover:not(.on) { color:var(--ink); background:var(--panel-3); }
  .chip { background:var(--panel-2); color:var(--muted); border:1px solid var(--line);
    border-radius:999px; padding:3px 10px; font-size:12px; cursor:pointer; user-select:none;
    display:inline-flex; align-items:center; gap:6px; }
  .chip:hover { color:var(--ink); border-color:var(--line-2); }
  .chip.on { background:var(--accent-dim); color:var(--ink); border-color:var(--accent); }
  .chip .sz { font-size:10px; color:var(--faint); font-family:var(--mono); }
  .chip .sw { width:9px; height:9px; border-radius:3px; }
  .chip .ct { font-family:var(--mono); font-size:10px; color:var(--faint); }
  .reset { background:transparent; color:var(--muted); border:1px solid var(--line);
    border-radius:var(--radius-sm); padding:5px 10px; font-size:12px; cursor:pointer; }
  .reset:hover { color:var(--ink); border-color:var(--line-2); }
  label.ck { display:inline-flex; align-items:center; gap:5px; font-size:12px; color:var(--muted); cursor:pointer; }
  .activebar { display:flex; gap:6px; flex-wrap:wrap; align-items:center; }
  .activebar .akey { color:var(--faint); font-size:11px; text-transform:uppercase; }
  .achip { background:var(--panel-2); border:1px solid var(--line); border-radius:999px;
    padding:2px 8px; font-size:11px; color:var(--ink); cursor:pointer; }
  .achip:hover { border-color:var(--bad); color:var(--bad); }
  .achip::after { content:" x"; color:var(--faint); }

  /* podium */
  .podium { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; align-items:end;
    max-width:560px; margin:4px auto 0; }
  .pod { text-align:center; }
  .pod .bar { border-radius:var(--radius-sm) var(--radius-sm) 0 0; margin-top:8px;
    border:1px solid var(--line-2); border-bottom:0; display:flex; align-items:flex-start;
    justify-content:center; padding-top:8px; font-size:22px; }
  .pod .who { font-weight:700; font-size:13px; margin-top:6px; word-break:break-word; }
  .pod .score { color:var(--muted); font-size:11px; font-family:var(--mono); }
  .pod.r1 .bar { background:color-mix(in srgb,var(--gold) 28%, var(--panel)); border-color:var(--gold); }
  .pod.r2 .bar { background:color-mix(in srgb,var(--silver) 22%, var(--panel)); border-color:var(--silver); }
  .pod.r3 .bar { background:color-mix(in srgb,var(--bronze) 24%, var(--panel)); border-color:var(--bronze); }
  .podnote { text-align:center; color:var(--muted); font-size:11px; margin-top:8px; }

  canvas { width:100%; display:block; }
  .legend { display:flex; gap:14px; flex-wrap:wrap; margin:10px 0 0; font-size:12px; }
  .legend span { display:inline-flex; align-items:center; gap:6px; color:var(--muted); }
  .swatch { width:12px; height:12px; border-radius:3px; display:inline-block; }

  /* heatmap */
  .heat-wrap { overflow-x:auto; }
  table.heat { border-collapse:separate; border-spacing:3px; font-size:12px; }
  table.heat th { color:var(--muted); font-weight:600; padding:4px 6px; text-align:center;
    white-space:nowrap; }
  table.heat th.rowh { text-align:left; position:sticky; left:0; background:var(--panel);
    z-index:1; font-family:var(--mono); color:var(--ink); }
  table.heat td { width:84px; height:34px; text-align:center; border-radius:6px;
    font-family:var(--mono); font-variant-numeric:tabular-nums; color:#fff; }
  table.heat td.na { color:var(--faint);
    background:repeating-linear-gradient(45deg,var(--panel-2),var(--panel-2) 5px,var(--panel) 5px,var(--panel) 10px); }

  /* tables */
  .scroll-x { overflow-x:auto; }
  table.data { width:100%; border-collapse:collapse; margin-top:12px; font-size:13px; }
  table.data th, table.data td { text-align:left; padding:7px 10px; border-bottom:1px solid var(--line); white-space:nowrap; }
  table.data th { color:var(--muted); font-weight:600; position:sticky; top:0; background:var(--panel); }
  table.data th.sortable { cursor:pointer; user-select:none; }
  table.data th.sortable:hover { color:var(--ink); }
  table.data th .arr { color:var(--accent); }
  td.num, th.num { text-align:right; font-family:var(--mono); font-variant-numeric:tabular-nums; }
  td.lbl0 { position:sticky; left:0; background:var(--panel); }
  .best { color:var(--good); font-weight:700; }
  .rank { display:inline-block; width:15px; height:15px; line-height:15px; text-align:center;
    border-radius:50%; font-size:10px; margin-right:5px; font-weight:700; color:var(--accent-ink); }
  .rank.r1 { background:var(--gold); } .rank.r2 { background:var(--silver); } .rank.r3 { background:var(--bronze); }
  .cov { font-family:var(--mono); font-size:11px; color:var(--muted); }
  .dim { color:var(--muted); }
  .empty { color:var(--muted); padding:28px; text-align:center; }
  details { margin-top:10px; } summary { cursor:pointer; color:var(--accent); }
  details.more { margin-top:20px; }
  details.more > summary { display:inline-block; padding:7px 14px; border:1px solid var(--line);
    border-radius:var(--radius-sm); background:var(--panel-2); color:var(--ink); font-size:13px; font-weight:600; }
  details.more > summary:hover { border-color:var(--line-2); }
  details.more[open] > summary { margin-bottom:4px; }
  .err td { color:var(--bad); }
  .note { color:var(--muted); font-size:12px; margin:8px 0 0; }

  /* tooltip */
  .tip { position:fixed; z-index:50; pointer-events:none; display:none;
    background:var(--panel-3); border:1px solid var(--line-2); border-radius:8px;
    padding:7px 10px; font-size:12px; box-shadow:var(--shadow); max-width:260px; }
  .tip .t { font-weight:700; margin-bottom:3px; }
  .tip .r { color:var(--muted); font-family:var(--mono); }

  :focus-visible { outline:2px solid var(--accent); outline-offset:2px; border-radius:var(--radius-sm); }
  @media (max-width:720px) {
    header, main { padding-left:16px; padding-right:16px; }
    .podium { grid-template-columns:1fr; }
    .pod .bar { height:auto !important; padding:10px; }
  }
  @media (prefers-reduced-motion: reduce) {
    * { animation:none !important; transition:none !important; }
  }
</style>
</head>
<body>
<header>
  <h1>llmbench</h1>
  <span class="meta" id="hdr-meta"></span>
  <nav id="nav">
    <a href="#sec-cmp">Comparison</a>
    <a href="#sec-machines">Machines</a>
    <a href="#sec-deep">Deep dive</a>
    <a href="#sec-err" id="nav-err" style="display:none">Errors</a>
  </nav>
  <button class="theme-btn" id="theme-btn" title="Toggle light/dark" aria-label="Toggle theme"></button>
</header>
<main>
  <section id="sec-cmp">
    <h2>Comparison</h2>

    <div class="toolbar" id="toolbar">
      <div class="trow">
        <div class="fgroup"><span class="lbl">Metric</span>
          <select id="cmp-metric"></select>
          <span class="dim" id="cmp-hint"></span>
        </div>
        <div class="fgroup"><span class="lbl">Backend</span><span class="seg" id="seg-backend"></span></div>
        <div class="fgroup"><span class="lbl">Format</span><span class="seg" id="seg-format"></span></div>
        <div class="fgroup"><span class="lbl">OS</span><span class="seg" id="seg-os"></span></div>
      </div>
      <div class="trow"><div class="fgroup"><span class="lbl">Models</span><span id="chips-model"></span></div></div>
      <div class="trow"><div class="fgroup"><span class="lbl">Prompts</span><span id="chips-prompt"></span></div></div>
      <div class="trow"><div class="fgroup"><span class="lbl">Machines</span><span id="chips-machine"></span></div></div>
      <div class="trow">
        <div class="fgroup"><span class="lbl">Baseline</span><select id="cmp-baseline"></select></div>
        <div class="fgroup"><span class="seg" id="seg-view"></span></div>
        <label class="ck"><input type="checkbox" id="cmp-only"> only cells run on every shown machine</label>
        <button class="reset" id="btn-reset">Reset filters</button>
      </div>
      <div class="trow activebar" id="activebar"></div>
    </div>

    <h3 id="bars-title">Per-model comparison — one bar per machine</h3>
    <div class="panel">
      <canvas id="cmp-canvas" data-h="380" height="380"></canvas>
      <div class="legend" id="cmp-legend"></div>
    </div>
    <div class="scroll-x" id="cmp-table"></div>

    <details class="more" id="more-views">
      <summary>More views — leaderboard, throughput-vs-latency scatter, heatmap</summary>

      <h3 style="margin-top:16px">Leaderboard — most cells won</h3>
      <div class="panel">
        <div class="podium" id="podium"></div>
        <div class="podnote" id="podium-note"></div>
      </div>

      <h3 style="margin-top:20px">Throughput vs. latency (each point = machine x cell)</h3>
      <div class="panel">
        <canvas id="sc-canvas" data-h="380" height="380"></canvas>
        <div class="legend" id="sc-legend"></div>
        <p class="note" id="sc-note"></p>
      </div>

      <h3 style="margin-top:20px">Heatmap — best per row is brightest</h3>
      <div class="panel heat-wrap"><div id="heatmap"></div></div>
    </details>
  </section>

  <section id="sec-machines">
    <h2>Machines</h2>
    <div class="cards" id="cards"></div>
  </section>

  <section id="sec-deep">
    <h2>Per-machine deep dive</h2>
    <div class="controls trow" style="margin:8px 0 12px; display:flex; gap:12px; flex-wrap:wrap">
      <label class="ck">Machine <select id="dd-machine"></select></label>
      <label class="ck">Cell <select id="dd-cell"></select></label>
    </div>
    <div class="panel">
      <div id="dd-stats"></div>
      <h3 style="margin-top:16px">Generation speed per session (re-runs)</h3>
      <canvas id="dd-canvas" data-h="300" height="300"></canvas>
      <p class="note" id="dd-note"></p>
    </div>
  </section>

  <section id="sec-err" style="display:none">
    <h2>Errors</h2>
    <div class="scroll-x" id="err-table"></div>
  </section>
</main>

<div class="tip" id="tip"></div>

<script id="data" type="application/json">/*__DATA__*/</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);

// Color-blind-safe (Okabe-Ito) series palette, one stable color per machine.
const PALETTE = ['#56b4e9','#e69f00','#009e73','#d55e00','#0072b2','#cc79a7','#f0e442','#999999'];
const COLOR = {};
const METRIC = {}; DATA.metrics.forEach(m => METRIC[m.key] = m);
const UNIT = {gen_tokens_per_s:'tok/s', ttft_s:'s', total_s:'s'};
const DEFAULT_METRIC = DATA.metrics[0].key;

function osFamily(os) {
  if (!os) return 'Other';
  if (os.indexOf('Darwin') === 0 || os.indexOf('macOS') === 0) return 'macOS';
  if (os.indexOf('Windows') === 0) return 'Windows';
  return os.split(' ')[0] || 'Other';
}
DATA.machines.forEach((m,i) => { COLOR[m.slug] = PALETTE[i % PALETTE.length]; m.family = osFamily(m.os); });

const fmt = (v,d) => (typeof v === 'number' && isFinite(v)) ? v.toFixed(d) : '—';
const el = (t, props={}, kids=[]) => {
  const n = document.createElement(t);
  for (const [k,v] of Object.entries(props)) {
    if (k === 'class') n.className = v; else if (k === 'html') n.innerHTML = v; else n.setAttribute(k,v);
  }
  (Array.isArray(kids)?kids:[kids]).forEach(c => n.append(c));
  return n;
};
const txt = s => document.createTextNode(s == null ? '' : String(s));
const uniq = a => [...new Set(a)];

// ---- derived domains for filters ----
const ALL_MODELS   = uniq(DATA.cells.map(c => c.model));
const ALL_BACKENDS = uniq(DATA.cells.map(c => c.backend));
const ALL_FORMATS  = uniq(DATA.cells.map(c => c.format));
const ALL_PROMPTS  = uniq(DATA.cells.map(c => c.prompt_id));
const ALL_MACH     = DATA.machines.map(m => m.slug);
const ALL_FAM      = uniq(DATA.machines.map(m => m.family));
const SIZE_OF = {}; DATA.cells.forEach(c => { if (c.size) SIZE_OF[c.model] = c.size; });
const LABEL_OF = {}; DATA.machines.forEach(m => LABEL_OF[m.slug] = m.label);
const hasMLX = ALL_FORMATS.indexOf('mlx') >= 0;

// ---- state ----
const state = {
  metric: DEFAULT_METRIC,
  backend:'all', format:'all', os:'all',
  models:new Set(ALL_MODELS), prompts:new Set(ALL_PROMPTS), mach:new Set(ALL_MACH),
  baseline:null, view:'raw', cmpOnly:false, sort:null,
};

// ---- data accessors (sparse: null = machine never ran this cell) ----
const statOf = (mk,cid,slug) => (DATA.comparison[mk] && DATA.comparison[mk][cid] && DATA.comparison[mk][cid][slug]) || null;
const rawVal = (mk,cid,slug) => { const s = statOf(mk,cid,slug); return s ? s.v : null; };
const isHib  = mk => METRIC[mk].higher_is_better;
const bestOf = (mk,vals) => { const p = vals.filter(v => typeof v === 'number'); if (!p.length) return null; return isHib(mk)?Math.max(...p):Math.min(...p); };

function visMachines() {
  return DATA.machines.filter(m => state.mach.has(m.slug) && (state.os==='all' || m.family===state.os));
}
function visCells() {
  let cells = DATA.cells.filter(c =>
    state.models.has(c.model) && state.prompts.has(c.prompt_id) &&
    (state.backend==='all' || c.backend===state.backend) &&
    (state.format==='all'  || c.format===state.format));
  if (state.cmpOnly) {
    const ms = visMachines();
    cells = cells.filter(c => ms.length>0 && ms.every(m => statOf(state.metric,c.id,m.slug)));
  }
  return cells;
}

// ---- relative (baseline) view ----
function cellValue(mk,cid,slug) {
  const v = rawVal(mk,cid,slug);
  if (state.view==='raw' || !state.baseline) return v;
  const bv = rawVal(mk,cid,state.baseline);
  if (typeof v!=='number' || typeof bv!=='number' || v===0 || bv===0) return null;
  return isHib(mk) ? v/bv : bv/v;   // >1 always = better than baseline
}
function cellDisplay(mk,cid,slug) {
  const d = METRIC[mk].digits;
  if (state.view==='raw' || !state.baseline) return fmt(rawVal(mk,cid,slug), d);
  const r = cellValue(mk,cid,slug);
  return (typeof r==='number' && isFinite(r)) ? r.toFixed(2)+'×' : '—';
}

// ==== URL hash persistence ====
function isAll(set,all) { return set.size===all.length; }
function encSet(set,all) { return isAll(set,all) ? null : [...set].join(','); }
function writeHash() {
  const p = [];
  if (state.metric!==DEFAULT_METRIC) p.push('metric='+state.metric);
  if (state.backend!=='all') p.push('be='+encodeURIComponent(state.backend));
  if (state.format!=='all')  p.push('fmt='+encodeURIComponent(state.format));
  if (state.os!=='all')      p.push('os='+encodeURIComponent(state.os));
  const ms = encSet(state.models,ALL_MODELS);   if (ms) p.push('models='+encodeURIComponent(ms));
  const ps = encSet(state.prompts,ALL_PROMPTS); if (ps) p.push('prompts='+encodeURIComponent(ps));
  const mc = encSet(state.mach,ALL_MACH);        if (mc) p.push('mach='+encodeURIComponent(mc));
  if (state.baseline) p.push('base='+encodeURIComponent(state.baseline));
  if (state.view!=='raw') p.push('view='+state.view);
  if (state.cmpOnly) p.push('cmp=1');
  if (state.sort) p.push('sort='+encodeURIComponent(state.sort.col)+':'+state.sort.dir);
  const h = p.join('&');
  try { history.replaceState(null,'', h ? '#'+h : location.pathname+location.search); }
  catch(e) { location.hash = h; }
}
function readHash() {
  const raw = location.hash.replace(/^#/,'');
  if (!raw) return;
  const map = {};
  raw.split('&').forEach(kv => { const i=kv.indexOf('='); if (i>0) map[kv.slice(0,i)]=decodeURIComponent(kv.slice(i+1)); });
  if (map.metric && METRIC[map.metric]) state.metric = map.metric;
  if (map.be && ALL_BACKENDS.indexOf(map.be)>=0) state.backend = map.be;
  if (map.fmt && ALL_FORMATS.indexOf(map.fmt)>=0) state.format = map.fmt;
  if (map.os && ALL_FAM.indexOf(map.os)>=0) state.os = map.os;
  const setFrom = (val,all) => new Set(val.split(',').filter(x => all.indexOf(x)>=0));
  if (map.models)  { const s=setFrom(map.models,ALL_MODELS);   if (s.size) state.models=s; }
  if (map.prompts) { const s=setFrom(map.prompts,ALL_PROMPTS); if (s.size) state.prompts=s; }
  if (map.mach)    { const s=setFrom(map.mach,ALL_MACH);        if (s.size) state.mach=s; }
  if (map.base && LABEL_OF[map.base]) state.baseline = map.base;
  if (map.view==='rel') state.view='rel';
  if (map.cmp==='1') state.cmpOnly=true;
  if (map.sort) { const [col,dir]=map.sort.split(':'); if (col) state.sort={col, dir: dir==='asc'?'asc':'desc'}; }
}

// ==== header + theme ====
document.getElementById('hdr-meta').innerHTML =
  `<span>generated <b>${DATA.generated_at}</b></span>` +
  `<span><b>${DATA.machines.length}</b> machines</span>` +
  `<span><b>${DATA.machines.reduce((a,m)=>a+m.sessions,0)}</b> sessions</span>` +
  `<span><b>${DATA.cells.length}</b> cells</span>`;

const themeBtn = document.getElementById('theme-btn');
function syncThemeIcon() { themeBtn.textContent = document.documentElement.getAttribute('data-theme')==='light' ? '☾' : '☀'; }
themeBtn.addEventListener('click', () => {
  const next = document.documentElement.getAttribute('data-theme')==='light' ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', next);
  try { localStorage.setItem('llmbench-theme', next); } catch(e) {}
  syncThemeIcon(); renderAll();
});
syncThemeIcon();

// ==== machine cards (static) ====
const cards = document.getElementById('cards');
DATA.machines.forEach(m => {
  const dl = el('dl');
  [['OS',`${m.os||'—'} ${m.arch||''}`],['CPU',m.cpu||'—'],
   ['RAM', m.ram_gb!=null ? m.ram_gb+' GB' : '—'],
   ['GPU',(m.gpus&&m.gpus.length)?m.gpus.join(', '):'—']]
  .forEach(([k,v]) => { dl.append(el('dt',{},txt(k))); dl.append(el('dd',{},txt(v))); });
  const osCls = m.family==='macOS'?'os-mac':(m.family==='Windows'?'os-win':'os-other');
  const card = el('div',{class:'card',style:`border-left-color:${COLOR[m.slug]}`},[
    el('div',{style:'display:flex;align-items:center;gap:8px;flex-wrap:wrap'},[
      el('span',{class:'swatch',style:`background:${COLOR[m.slug]}`}),
      el('span',{class:'name'},txt(m.label)),
      el('span',{class:'os '+osCls},txt(m.family)),
    ]),
    el('div',{class:'slug'},txt(m.slug)),
    el('div',{style:'margin-top:6px'},el('span',{class:'badge'},txt(m.sessions+' session(s)'))),
    dl,
  ]);
  cards.append(card);
});

// ==== shared canvas helpers (retina-aware) ====
// Returns null when the canvas has no usable width yet (hidden <details>, or
// a layout that hasn't settled). Callers bail so a blank frame is never locked
// in; a ResizeObserver (see bottom) repaints once a real width appears.
function setupCanvas(canvas) {
  const ratio = window.devicePixelRatio || 1;
  const cssWidth = canvas.clientWidth || canvas.parentElement.clientWidth;
  if (!cssWidth) return null;
  // Logical height lives in data-h; `canvas.height` is the backing-store pixel
  // size we overwrite each draw. Reading data-h (never mutated) avoids the
  // retina feedback loop where height = prevHeight*ratio grows every redraw.
  const cssHeight = +canvas.dataset.h;
  canvas.width = cssWidth*ratio; canvas.height = cssHeight*ratio;
  canvas.style.height = cssHeight+'px';
  const ctx = canvas.getContext('2d'); ctx.setTransform(ratio,0,0,ratio,0,0);
  ctx.clearRect(0,0,cssWidth,cssHeight);
  const css = getComputedStyle(document.documentElement);
  return {ctx, w:cssWidth, h:cssHeight,
    line:css.getPropertyValue('--line').trim(), muted:css.getPropertyValue('--muted').trim(),
    panel:css.getPropertyValue('--panel').trim(), ink:css.getPropertyValue('--ink').trim()};
}
function yAxis(ctx, w, h, pad, maxVal, yLabel, muted, line) {
  ctx.strokeStyle=line; ctx.fillStyle=muted; ctx.font='11px var(--mono),monospace'; ctx.lineWidth=1;
  const ticks=5;
  for (let i=0;i<=ticks;i++){
    const y=pad.top+(h-pad.top-pad.bottom)*(1-i/ticks), val=maxVal*i/ticks;
    ctx.beginPath(); ctx.moveTo(pad.left,y); ctx.lineTo(w-pad.right,y); ctx.stroke();
    ctx.textAlign='right'; ctx.textBaseline='middle';
    ctx.fillText(val.toFixed(maxVal<10?1:0), pad.left-6, y);
  }
  if (yLabel){ ctx.save(); ctx.translate(12,(pad.top+h-pad.bottom)/2); ctx.rotate(-Math.PI/2);
    ctx.textAlign='center'; ctx.fillText(yLabel,0,0); ctx.restore(); }
}

// shared tooltip
const tip = document.getElementById('tip');
function showTip(x,y,title,rows) {
  tip.innerHTML = `<div class="t">${title}</div>` + rows.map(r=>`<div class="r">${r}</div>`).join('');
  tip.style.display='block';
  const pad=14; let left=x+pad, top=y+pad;
  const rc=tip.getBoundingClientRect();
  if (left+rc.width>innerWidth) left=x-rc.width-pad;
  if (top+rc.height>innerHeight) top=y-rc.height-pad;
  tip.style.left=left+'px'; tip.style.top=top+'px';
}
function hideTip(){ tip.style.display='none'; }

// ==== grouped whisker bars (comparison) ====
let cmpBarRects = [];
let barsAnimated = false;
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;

function drawBars(progress) {
  const canvas = document.getElementById('cmp-canvas');
  const s = setupCanvas(canvas);
  if (!s) { barsAnimated = false; return; }   // no width yet; let observer retry
  const {ctx,w,h,line,muted,panel} = s;
  cmpBarRects = [];
  const mk = state.metric, groups = visCells(), series = visMachines();
  const rel = state.view==='rel' && state.baseline;
  const yLabel = rel ? '× vs baseline' : METRIC[mk].label;

  if (!groups.length || !series.length) {
    ctx.fillStyle=muted; ctx.font='13px var(--sans)';
    ctx.fillText('No cells match the current filters.', 20, 40); return;
  }
  let maxVal=0;
  groups.forEach(g => series.forEach(s => {
    if (rel) { const v=cellValue(mk,g.id,s.slug); if (typeof v==='number'&&v>maxVal) maxVal=v; }
    else { const st=statOf(mk,g.id,s.slug); if (st){ const top=(st.hi!=null?st.hi:st.v); if (top>maxVal) maxVal=top; } }
  }));
  if (rel && maxVal<1) maxVal=1;
  if (maxVal<=0){ ctx.fillStyle=muted; ctx.fillText('No data',48,36); return; }
  maxVal*=1.1;
  const pad={top:16,right:16,bottom:76,left:52};
  yAxis(ctx,w,h,pad,maxVal,yLabel,muted,line);
  const plotW=w-pad.left-pad.right, plotH=h-pad.top-pad.bottom;
  const gW=plotW/groups.length, bW=Math.min(42,(gW*0.82)/series.length);

  if (rel) { // 1.0x reference line
    const y=pad.top+plotH*(1-1/maxVal);
    ctx.strokeStyle=muted; ctx.setLineDash([4,4]); ctx.beginPath();
    ctx.moveTo(pad.left,y); ctx.lineTo(w-pad.right,y); ctx.stroke(); ctx.setLineDash([]);
  }
  groups.forEach((g,gi) => {
    const gx=pad.left+gi*gW+gW/2;
    series.forEach((s,si) => {
      const val = rel ? cellValue(mk,g.id,s.slug) : rawVal(mk,g.id,s.slug);
      if (typeof val!=='number'||!isFinite(val)) return;
      const bh=plotH*(val/maxVal)*progress;
      const x=gx-(series.length*bW)/2+si*bW, y=pad.top+plotH-bh;
      ctx.fillStyle=COLOR[s.slug]; ctx.fillRect(x,y,bW-2,bh);
      const st=statOf(mk,g.id,s.slug);
      cmpBarRects.push({x,y,w:bW-2,h:bh, slug:s.slug, label:s.label, cid:g.id, cell:cellShort(g), st, rel, val});
      if (!rel && progress>=1 && st && st.n>1 && st.sd) { // stdev whisker + min/max ticks
        const cx=x+(bW-2)/2;
        const yv=v=>pad.top+plotH*(1-v/maxVal);
        ctx.strokeStyle=panel; ctx.lineWidth=2;
        const yHi=yv(val+st.sd), yLo=yv(Math.max(0,val-st.sd));
        ctx.beginPath(); ctx.moveTo(cx,yHi); ctx.lineTo(cx,yLo); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(cx-4,yHi); ctx.lineTo(cx+4,yHi);
        ctx.moveTo(cx-4,yLo); ctx.lineTo(cx+4,yLo); ctx.stroke();
      }
    });
    ctx.save(); ctx.fillStyle=muted; ctx.font='11px var(--sans)';
    ctx.translate(gx,pad.top+plotH+8); ctx.rotate(Math.PI/5); ctx.textAlign='left';
    ctx.fillText(g.short, 0, 0); ctx.restore();
  });
}
document.getElementById('cmp-canvas').addEventListener('mousemove', ev => {
  const r=ev.currentTarget.getBoundingClientRect(), mx=ev.clientX-r.left, my=ev.clientY-r.top;
  const hit=cmpBarRects.find(b => mx>=b.x&&mx<=b.x+b.w&&my>=b.y&&my<=b.y+b.h);
  if (!hit){ hideTip(); return; }
  const d=METRIC[state.metric].digits, rows=[];
  if (hit.rel) rows.push(`${hit.val.toFixed(2)}× vs ${LABEL_OF[state.baseline]}`);
  else {
    rows.push(`${fmt(hit.st?hit.st.v:null,d)} ${UNIT[state.metric]||''} (median)`);
    if (hit.st && hit.st.n>1) rows.push(`± ${fmt(hit.st.sd,d)} · ${fmt(hit.st.lo,d)}–${fmt(hit.st.hi,d)} · n=${hit.st.n}`);
    else if (hit.st) rows.push(`n=${hit.st.n}`);
  }
  rows.push(hit.cell);
  showTip(ev.clientX, ev.clientY, hit.label, rows);
});
document.getElementById('cmp-canvas').addEventListener('mouseleave', hideTip);

// ==== scatter: throughput (x) vs TTFT (y, lower=up) + Pareto frontier ====
let scDots = [];
function drawScatter() {
  const canvas=document.getElementById('sc-canvas');
  const s=setupCanvas(canvas);
  if (!s) return;   // hidden (collapsed details) or no width yet
  const {ctx,w,h,line,muted,ink}=s;
  scDots=[];
  const cells=visCells(), machines=visMachines();
  const pts=[];
  cells.forEach(c => machines.forEach(m => {
    const x=rawVal('gen_tokens_per_s',c.id,m.slug), y=rawVal('ttft_s',c.id,m.slug);
    if (typeof x==='number'&&typeof y==='number') pts.push({x,y,slug:m.slug,cell:cellShort(c),ml:m.label});
  }));
  const note=document.getElementById('sc-note');
  if (!pts.length){ ctx.fillStyle=muted; ctx.font='13px var(--sans)';
    ctx.fillText('Need both generation speed and TTFT for a point — none match.',20,40);
    note.textContent=''; return; }
  const pad={top:18,right:18,bottom:46,left:56};
  const xs=pts.map(p=>p.x), ys=pts.map(p=>p.y);
  let xMin=Math.min(...xs), xMax=Math.max(...xs), yMin=Math.min(...ys), yMax=Math.max(...ys);
  if (xMax===xMin) xMax=xMin+1; if (yMax===yMin) yMax=yMin+0.01;
  const xr=xMax-xMin, yr=yMax-yMin; xMin-=xr*0.08; xMax+=xr*0.08; yMin-=yr*0.08; yMax+=yr*0.08;
  const plotW=w-pad.left-pad.right, plotH=h-pad.top-pad.bottom;
  const xAt=v=>pad.left+plotW*(v-xMin)/(xMax-xMin);
  const yAt=v=>pad.top+plotH*(v-yMin)/(yMax-yMin); // small ttft -> top
  // grid + axis labels
  ctx.strokeStyle=line; ctx.fillStyle=muted; ctx.font='11px var(--mono),monospace'; ctx.lineWidth=1;
  for (let i=0;i<=4;i++){
    const gy=pad.top+plotH*i/4, gx=pad.left+plotW*i/4;
    ctx.beginPath(); ctx.moveTo(pad.left,gy); ctx.lineTo(w-pad.right,gy); ctx.stroke();
    ctx.textAlign='right'; ctx.textBaseline='middle';
    ctx.fillText((yMin+(yMax-yMin)*i/4).toFixed(2), pad.left-6, gy);
    ctx.textAlign='center'; ctx.textBaseline='top';
    ctx.fillText((xMin+(xMax-xMin)*i/4).toFixed(0), gx, pad.top+plotH+6);
  }
  ctx.fillStyle=muted; ctx.font='11px var(--sans)'; ctx.textAlign='center';
  ctx.fillText('generation speed (tok/s) — higher is better →', pad.left+plotW/2, h-14);
  ctx.save(); ctx.translate(14,pad.top+plotH/2); ctx.rotate(-Math.PI/2);
  ctx.fillText('↑ TTFT (s) — lower is better', 0, 0); ctx.restore();
  // Pareto frontier: maximize x, minimize y.
  const front=pts.filter(p => !pts.some(q =>
    (q.x>=p.x && q.y<=p.y) && (q.x>p.x || q.y<p.y))).sort((a,b)=>a.x-b.x);
  if (front.length>1){
    ctx.strokeStyle=METRIC?getComputedStyle(document.documentElement).getPropertyValue('--accent').trim():'#6ea8fe';
    ctx.lineWidth=1.5; ctx.setLineDash([5,4]); ctx.beginPath();
    front.forEach((p,i)=>{ const X=xAt(p.x),Y=yAt(p.y); i?ctx.lineTo(X,Y):ctx.moveTo(X,Y); });
    ctx.stroke(); ctx.setLineDash([]);
  }
  pts.forEach(p => {
    const X=xAt(p.x), Y=yAt(p.y), onFront=front.indexOf(p)>=0;
    ctx.beginPath(); ctx.arc(X,Y, onFront?6:4.5, 0, 7);
    ctx.fillStyle=COLOR[p.slug]; ctx.fill();
    if (onFront){ ctx.strokeStyle=ink; ctx.lineWidth=1.5; ctx.stroke(); }
    scDots.push({X,Y,r:onFront?8:6, ...p, onFront});
  });
  note.textContent = `${pts.length} point(s). Outlined points are on the Pareto frontier — `+
    `no other machine/cell is both faster and lower-latency. Up-and-right is best.`;
}
document.getElementById('sc-canvas').addEventListener('mousemove', ev => {
  const r=ev.currentTarget.getBoundingClientRect(), mx=ev.clientX-r.left, my=ev.clientY-r.top;
  let hit=null, bd=1e9;
  scDots.forEach(p => { const dd=(p.X-mx)**2+(p.Y-my)**2; if (dd<=p.r*p.r && dd<bd){bd=dd;hit=p;} });
  if (!hit){ hideTip(); return; }
  showTip(ev.clientX,ev.clientY, hit.ml, [
    `${hit.x.toFixed(1)} tok/s · ${hit.y.toFixed(3)} s TTFT`,
    hit.cell + (hit.onFront?' · Pareto-optimal':'')]);
});
document.getElementById('sc-canvas').addEventListener('mouseleave', hideTip);

// ==== heatmap (DOM grid, per-row normalized) ====
function renderHeatmap() {
  const host=document.getElementById('heatmap'); host.innerHTML='';
  const mk=state.metric, cells=visCells(), machines=visMachines();
  if (!cells.length || !machines.length){ host.append(el('div',{class:'empty'},txt('No cells match the current filters.'))); return; }
  const tbl=el('table',{class:'heat'});
  const head=el('tr',{},[el('th',{class:'rowh'},txt('cell \\ machine'))]);
  machines.forEach(m => head.append(el('th',{title:m.label},[
    el('span',{class:'swatch',style:`background:${COLOR[m.slug]};margin-right:5px`}), txt(m.label)])));
  tbl.append(el('thead',{},head));
  const body=el('tbody');
  cells.forEach(c => {
    const vals=machines.map(m=>rawVal(mk,c.id,m.slug));
    const present=vals.filter(v=>typeof v==='number');
    const lo=present.length?Math.min(...present):0, hi=present.length?Math.max(...present):0;
    const tr=el('tr',{},[el('th',{class:'rowh',title:c.id},txt(c.short))]);
    vals.forEach((v,i) => {
      if (typeof v!=='number'){ tr.append(el('td',{class:'na',title:machines[i].label+': not run'},txt('—'))); return; }
      let t = hi===lo ? 1 : (isHib(mk) ? (v-lo)/(hi-lo) : (hi-v)/(hi-lo)); // 1 = best in row
      const td=el('td',{style:`background:rgba(110,168,254,${(0.16+0.84*t).toFixed(3)})`,
        title:`${machines[i].label}: ${fmt(v,METRIC[mk].digits)} ${UNIT[mk]||''}`}, txt(fmt(v,METRIC[mk].digits)));
      tr.append(td);
    });
    body.append(tr);
  });
  tbl.append(body); host.append(tbl);
}

// ==== podium ====
function renderPodium() {
  const host=document.getElementById('podium'); host.innerHTML='';
  const note=document.getElementById('podium-note');
  const mk=state.metric, cells=visCells(), machines=visMachines();
  const wins={}; machines.forEach(m=>wins[m.slug]=0);
  let contests=0;
  cells.forEach(c => {
    const vals=machines.map(m=>({slug:m.slug,v:rawVal(mk,c.id,m.slug)})).filter(x=>typeof x.v==='number');
    if (vals.length<2) return;
    contests++;
    const best=bestOf(mk,vals.map(x=>x.v)); const w=vals.filter(x=>x.v===best);
    w.forEach(x=>wins[x.slug]+=1/w.length);
  });
  const ranked=machines.map(m=>({...m,wins:wins[m.slug]})).sort((a,b)=>b.wins-a.wins).slice(0,3);
  if (!contests){ host.append(el('div',{class:'empty',style:'grid-column:1/-1'},
    txt('No head-to-head cells (need ≥2 shown machines sharing a cell).'))); note.textContent=''; return; }
  const maxW=Math.max(1,...ranked.map(r=>r.wins));
  const order=[1,0,2]; // silver, gold, bronze visual order
  order.forEach(idx => {
    const r=ranked[idx]; if (!r) return;
    const medal=['🥇','🥈','🥉'][idx];
    const hgt=60+120*(r.wins/maxW);
    host.append(el('div',{class:'pod r'+(idx+1)},[
      el('div',{class:'bar',style:`height:${hgt}px;background:color-mix(in srgb,${COLOR[r.slug]} 30%, var(--panel))`},txt(medal)),
      el('div',{class:'who'},txt(r.label)),
      el('div',{class:'score'},txt(r.wins.toFixed(r.wins%1?1:0)+' win(s)')),
    ]));
  });
  note.textContent = `${METRIC[mk].label} — ${contests} head-to-head cell(s) across ${machines.length} shown machine(s). `+
    `Ties split the win.`;
}

// ==== comparison table (sortable, rank chips, baseline-aware) ====
function cellShort(c){ return `${c.model} · ${c.format} · ${c.prompt_id} (${c.backend})`; }
function renderCmpTable() {
  const mk=state.metric, groups=visCells().slice(), series=visMachines();
  const holder=document.getElementById('cmp-table'); holder.innerHTML='';
  if (!groups.length || !series.length){ holder.append(el('div',{class:'empty'},txt('No rows match the current filters.'))); return; }

  // sort rows
  if (state.sort){
    const {col,dir}=state.sort, sgn=dir==='asc'?1:-1;
    const dims={backend:'backend',model:'model',format:'format',size:'size',prompt:'prompt_id'};
    groups.sort((a,b)=>{
      let av,bv;
      if (dims[col]){ av=a[dims[col]]||''; bv=b[dims[col]]||''; return sgn*String(av).localeCompare(String(bv)); }
      av=cellValue(mk,a.id,col); bv=cellValue(mk,b.id,col);
      const an=typeof av!=='number', bn=typeof bv!=='number';
      if (an&&bn) return 0; if (an) return 1; if (bn) return -1; // nulls last
      return sgn*(av-bv);
    });
  }
  const tbl=el('table',{class:'data'});
  const arrow=c=>state.sort&&state.sort.col===c?el('span',{class:'arr'},txt(state.sort.dir==='asc'?' ▲':' ▼')):txt('');
  const dimTh=(name,col)=>{ const th=el('th',{class:'sortable'},[txt(name),arrow(col)]); th.onclick=()=>toggleSort(col); return th; };
  const head=el('tr',{},[dimTh('Backend','backend'),dimTh('Model','model'),dimTh('Format','format'),
    dimTh('Size','size'),dimTh('Prompt','prompt'),el('th',{},txt('Ran'))]);
  series.forEach(s=>{ const th=el('th',{class:'sortable num',title:s.slug},[txt(s.label),arrow(s.slug)]); th.onclick=()=>toggleSort(s.slug); head.append(th); });
  tbl.append(el('thead',{},head));
  const body=el('tbody');
  groups.forEach(g => {
    const raws=series.map(s=>rawVal(mk,g.id,s.slug));
    const present=raws.filter(v=>typeof v==='number');
    const best=present.length>1?bestOf(mk,present):null;
    // rank map (raw order holds for relative too)
    const order=series.map((s,i)=>({i,v:raws[i]})).filter(x=>typeof x.v==='number')
      .sort((a,b)=>isHib(mk)?b.v-a.v:a.v-b.v);
    const rankOf={}; order.forEach((o,r)=>{ if (r<3) rankOf[o.i]=r+1; });
    const tr=el('tr',{},[el('td',{},txt(g.backend)),el('td',{},txt(g.model)),el('td',{},txt(g.format)),
      el('td',{},txt(g.size||'—')),el('td',{},txt(g.prompt_id)),
      el('td',{class:'cov'},txt(`${present.length}/${series.length}`))]);
    raws.forEach((rv,i) => {
      const s=series[i], td=el('td',{class:'num'});
      if (rankOf[i]) td.append(el('span',{class:'rank r'+rankOf[i]},txt(rankOf[i])));
      td.append(txt(cellDisplay(mk,g.id,s.slug)));
      if (best!=null && rv===best && present.length>1) td.classList.add('best');
      tr.append(td);
    });
    body.append(tr);
  });
  tbl.append(body); holder.append(tbl);
}
function toggleSort(col){
  if (state.sort && state.sort.col===col) {
    if (state.sort.dir==='desc') state.sort.dir='asc';
    else state.sort=null;
  } else state.sort={col, dir:'desc'};
  writeHash(); renderCmpTable();
}

// ==== comparison legend ====
function renderCmpLegend() {
  const lg=document.getElementById('cmp-legend'); lg.innerHTML='';
  visMachines().forEach(s => lg.append(el('span',{},[
    el('span',{class:'swatch',style:`background:${COLOR[s.slug]}`}), txt(s.label)])));
  const scl=document.getElementById('sc-legend'); scl.innerHTML='';
  visMachines().forEach(s => scl.append(el('span',{},[
    el('span',{class:'swatch',style:`background:${COLOR[s.slug]}`}), txt(s.label)])));
}

// ==== toolbar controls ====
const metricSel=document.getElementById('cmp-metric');
DATA.metrics.forEach(m => metricSel.append(el('option',{value:m.key},txt(m.label))));
metricSel.addEventListener('change',()=>{ state.metric=metricSel.value; if (state.sort){} writeHash(); renderAll(); });

function buildSeg(hostId, values, get, set, labels) {
  const host=document.getElementById(hostId); host.innerHTML='';
  ['all',...values].forEach(v => {
    const b=el('button',{},txt(v==='all'?'All':(labels&&labels[v]||v)));
    b.onclick=()=>{ set(v); writeHash(); renderAll(); };
    b.dataset.v=v; host.append(b);
  });
  host.__get=get;
}
buildSeg('seg-backend',ALL_BACKENDS,()=>state.backend,v=>state.backend=v);
buildSeg('seg-format', ALL_FORMATS, ()=>state.format, v=>state.format=v);
buildSeg('seg-os',     ALL_FAM,     ()=>state.os,     v=>state.os=v);
buildSeg('seg-view',['raw','rel'],()=>state.view,v=>state.view=v,{raw:'Raw',rel:'× vs baseline'});
// view segment uses 'all' slot we don't want -> rebuild without All
(function(){ const host=document.getElementById('seg-view'); host.innerHTML='';
  [['raw','Raw'],['rel','× vs baseline']].forEach(([v,l])=>{
    const b=el('button',{},txt(l)); b.dataset.v=v;
    b.onclick=()=>{ state.view=v; writeHash(); renderAll(); }; host.append(b); });
  host.__get=()=>state.view; })();

function buildChips(hostId, values, set, opts={}) {
  const host=document.getElementById(hostId); host.innerHTML='';
  const allChip=el('span',{class:'chip',title:'select all'},txt('All'));
  allChip.onclick=()=>{ values.forEach(v=>set.add(v)); writeHash(); renderAll(); };
  host.append(allChip);
  values.forEach(v => {
    const kids=[];
    if (opts.swatch) kids.push(el('span',{class:'sw',style:`background:${opts.swatch(v)}`}));
    kids.push(txt(opts.label?opts.label(v):v));
    if (opts.sub) kids.push(el('span',{class:'sz'},txt(opts.sub(v))));
    const c=el('span',{class:'chip','data-v':v}, kids);
    c.onclick=()=>{ if (set.has(v)){ if (set.size>1) set.delete(v); } else set.add(v); writeHash(); renderAll(); };
    host.append(c);
  });
  host.__set=set; host.__values=values;
}
buildChips('chips-model', ALL_MODELS, state.models, {sub:v=>SIZE_OF[v]||''});
buildChips('chips-prompt',ALL_PROMPTS,state.prompts,{});
buildChips('chips-machine',ALL_MACH, state.mach,   {swatch:v=>COLOR[v], label:v=>LABEL_OF[v]});

const baseSel=document.getElementById('cmp-baseline');
baseSel.append(el('option',{value:''},txt('none (raw)')));
DATA.machines.forEach(m => baseSel.append(el('option',{value:m.slug},txt(m.label))));
baseSel.addEventListener('change',()=>{ state.baseline=baseSel.value||null;
  if (state.baseline && state.view==='raw') state.view='rel';
  if (!state.baseline) state.view='raw';
  writeHash(); renderAll(); });

const cmpOnly=document.getElementById('cmp-only');
cmpOnly.addEventListener('change',()=>{ state.cmpOnly=cmpOnly.checked; writeHash(); renderAll(); });

document.getElementById('btn-reset').addEventListener('click',()=>{
  state.backend='all'; state.format='all'; state.os='all';
  state.models=new Set(ALL_MODELS); state.prompts=new Set(ALL_PROMPTS); state.mach=new Set(ALL_MACH);
  state.baseline=null; state.view='raw'; state.cmpOnly=false; state.sort=null;
  buildChips('chips-model',ALL_MODELS,state.models,{sub:v=>SIZE_OF[v]||''});
  buildChips('chips-prompt',ALL_PROMPTS,state.prompts,{});
  buildChips('chips-machine',ALL_MACH,state.mach,{swatch:v=>COLOR[v],label:v=>LABEL_OF[v]});
  writeHash(); renderAll();
});

function syncControls() {
  metricSel.value=state.metric;
  baseSel.value=state.baseline||''; cmpOnly.checked=state.cmpOnly;
  ['seg-backend','seg-format','seg-os','seg-view'].forEach(id => {
    const host=document.getElementById(id), cur=host.__get();
    [...host.children].forEach(b => b.classList.toggle('on', b.dataset.v===cur));
  });
  [['chips-model',state.models],['chips-prompt',state.prompts],['chips-machine',state.mach]].forEach(([id,set])=>{
    const host=document.getElementById(id);
    [...host.children].forEach(c => { const v=c.getAttribute('data-v'); if (v!=null) c.classList.toggle('on', set.has(v)); });
  });
}

function renderActiveBar() {
  const bar=document.getElementById('activebar'); bar.innerHTML='';
  const add=(key,txtv,clear)=>{ const c=el('span',{class:'achip'},txt(txtv)); c.onclick=()=>{ clear(); writeHash(); syncControls(); renderAll(); }; bar.append(c); };
  const chips=[];
  if (state.backend!=='all') chips.push(['backend',state.backend,()=>state.backend='all']);
  if (state.format!=='all') chips.push(['format',state.format,()=>state.format='all']);
  if (state.os!=='all') chips.push(['os',state.os,()=>state.os='all']);
  if (!isAll(state.models,ALL_MODELS)) chips.push(['models',state.models.size+' models',()=>state.models=new Set(ALL_MODELS)]);
  if (!isAll(state.prompts,ALL_PROMPTS)) chips.push(['prompts',state.prompts.size+' prompts',()=>state.prompts=new Set(ALL_PROMPTS)]);
  if (!isAll(state.mach,ALL_MACH)) chips.push(['machines',state.mach.size+' machines',()=>state.mach=new Set(ALL_MACH)]);
  if (state.baseline) chips.push(['baseline',LABEL_OF[state.baseline],()=>{state.baseline=null;state.view='raw';}]);
  if (state.cmpOnly) chips.push(['view','comparable only',()=>state.cmpOnly=false]);
  if (!chips.length){ bar.append(el('span',{class:'dim',style:'font-size:11px'},txt('No filters active — showing everything.'))); return; }
  bar.append(el('span',{class:'akey'},txt('filters:')));
  chips.forEach(([k,v,clr])=>add(k,v,clr));
}

// ==== deep dive (filtered) ====
const ddMachine=document.getElementById('dd-machine'), ddCell=document.getElementById('dd-cell');
function refreshDeepControls() {
  const ms=visMachines(); const prev=ddMachine.value;
  ddMachine.innerHTML='';
  ms.forEach(m=>ddMachine.append(el('option',{value:m.slug},txt(m.label))));
  if (ms.some(m=>m.slug===prev)) ddMachine.value=prev;
  refreshCellOptions();
}
function refreshCellOptions() {
  const slug=ddMachine.value, prev=ddCell.value;
  const visIds=new Set(visCells().map(c=>c.id));
  const cells=Object.keys(DATA.deep[slug]||{}).filter(cid=>visIds.has(cid));
  ddCell.innerHTML='';
  cells.forEach(cid=>ddCell.append(el('option',{value:cid},txt(cid))));
  if (cells.indexOf(prev)>=0) ddCell.value=prev;
}
function drawLines(canvas, labels, lines, yLabel) {
  const s=setupCanvas(canvas);
  if (!s) return;
  const {ctx,w,h,line,muted}=s;
  const pad={top:16,right:16,bottom:40,left:52};
  let maxVal=0; lines.forEach(ln=>ln.points.forEach(p=>{if(typeof p==='number'&&p>maxVal)maxVal=p;}));
  if (maxVal<=0||!labels.length){ ctx.fillStyle=muted; ctx.font='13px var(--sans)'; ctx.fillText('No data',pad.left,pad.top+20); return; }
  maxVal*=1.1; yAxis(ctx,w,h,pad,maxVal,yLabel,muted,line);
  const plotW=w-pad.left-pad.right, plotH=h-pad.top-pad.bottom;
  const xAt=i=>pad.left+(labels.length===1?plotW/2:plotW*i/(labels.length-1));
  const yAt=v=>pad.top+plotH*(1-v/maxVal);
  ctx.fillStyle=muted; ctx.font='11px var(--mono),monospace'; ctx.textAlign='center'; ctx.textBaseline='top';
  labels.forEach((lb,i)=>ctx.fillText(lb,xAt(i),pad.top+plotH+8));
  lines.forEach(ln=>{
    ctx.strokeStyle=ln.color; ctx.lineWidth=2; ctx.beginPath(); let started=false;
    ln.points.forEach((p,i)=>{ if(typeof p!=='number'||!isFinite(p))return; const x=xAt(i),y=yAt(p);
      started?ctx.lineTo(x,y):(ctx.moveTo(x,y),started=true); });
    ctx.stroke(); ctx.fillStyle=ln.color;
    ln.points.forEach((p,i)=>{ if(typeof p!=='number'||!isFinite(p))return; ctx.beginPath(); ctx.arc(xAt(i),yAt(p),3,0,7); ctx.fill(); });
  });
}
function renderDeepDive() {
  const slug=ddMachine.value, cid=ddCell.value;
  const entry=(DATA.deep[slug]||{})[cid];
  const statsHolder=document.getElementById('dd-stats'), note=document.getElementById('dd-note');
  statsHolder.innerHTML='';
  if (!entry){ statsHolder.append(el('div',{class:'empty'},txt('No data for this selection.')));
    drawLines(document.getElementById('dd-canvas'),[],[]); note.textContent=''; return; }
  const tbl=el('table',{class:'data'});
  tbl.append(el('thead',{},el('tr',{},['Metric','median','min','max','± stdev','samples']
    .map((t,i)=>el('th',{class:i?'num':''},txt(t))))));
  const body=el('tbody');
  DATA.metrics.forEach(m=>{ const p=entry.pooled[m.key]||{};
    body.append(el('tr',{},[el('td',{},txt(m.label)),
      el('td',{class:'num'},txt(fmt(p.median,m.digits))), el('td',{class:'num'},txt(fmt(p.min,m.digits))),
      el('td',{class:'num'},txt(fmt(p.max,m.digits))), el('td',{class:'num'},txt(fmt(p.stdev,m.digits))),
      el('td',{class:'num'},txt(p.n!=null?String(p.n):'—'))])); });
  tbl.append(body); statsHolder.append(tbl);
  const series=entry.sessions, labels=series.map(s=>s.label);
  drawLines(document.getElementById('dd-canvas'),labels,[{points:series.map(s=>s.gen),color:COLOR[slug]}],'tok/s');
  const nRuns=series.reduce((a,s)=>a+(s.n_runs||0),0);
  note.textContent=`${series.length} session(s), ${nRuns} measured run(s) pooled. `+
    `Each point is one session's median generation speed; a flat line means the machine is consistent across re-runs.`;
}
ddMachine.addEventListener('change',()=>{ refreshCellOptions(); renderDeepDive(); });
ddCell.addEventListener('change',renderDeepDive);

// ==== errors (static) ====
if (DATA.errors.length) {
  document.getElementById('sec-err').style.display='';
  document.getElementById('nav-err').style.display='';
  const tbl=el('table',{class:'data'});
  tbl.append(el('thead',{},el('tr',{},['Machine','Backend','Model','Format','Prompt','Error']
    .map(h=>el('th',{},txt(h))))));
  const body=el('tbody');
  DATA.errors.forEach(e=>body.append(el('tr',{class:'err'},
    [e.machine,e.backend,e.model,e.format,e.prompt_id,e.error].map(v=>el('td',{},txt(v||''))))));
  tbl.append(body); document.getElementById('err-table').append(tbl);
}

// ==== hint + titles ====
function renderHints() {
  const m=METRIC[state.metric];
  document.getElementById('cmp-hint').textContent = m.higher_is_better?'higher is better':'lower is better';
  document.getElementById('bars-title').textContent = (state.view==='rel'&&state.baseline)
    ? `Per-model — × vs ${LABEL_OF[state.baseline]} (higher = better), one bar per machine`
    : `Per-model — ${m.label}, one bar per machine`;
}

// ==== master render ====
function renderAll() {
  renderHints(); renderPodium(); drawScatter(); renderHeatmap();
  if (!barsAnimated && !reducedMotion) animateBars(); else drawBars(1);
  renderCmpLegend(); renderCmpTable();
  refreshDeepControls(); renderDeepDive();
  renderActiveBar();
}
function animateBars() {
  barsAnimated=true; const dur=450; let start=null;
  function step(ts){ if(start==null)start=ts; const t=Math.min(1,(ts-start)/dur);
    const e=1-Math.pow(1-t,3); drawBars(e); if (t<1) requestAnimationFrame(step); }
  requestAnimationFrame(step);
}

// nav active-section highlight
const navLinks=[...document.querySelectorAll('nav a')];
const io=new IntersectionObserver(ents=>{
  ents.forEach(e=>{ if(e.isIntersecting){ const id=e.target.id;
    navLinks.forEach(a=>a.classList.toggle('active', a.getAttribute('href')==='#'+id)); } });
},{rootMargin:'-40% 0px -55% 0px'});
['sec-cmp','sec-machines','sec-deep','sec-err'].forEach(id=>{ const n=document.getElementById(id); if(n) io.observe(n); });

// Redraw the hidden "More views" canvases the moment they're revealed — while
// the <details> is closed their canvas has zero width and can't paint.
document.getElementById('more-views').addEventListener('toggle', ev => {
  if (ev.target.open) { drawScatter(); renderHeatmap(); }
});

// Root fix for blank charts: repaint each canvas whenever it actually has a
// width. Covers first-paint layout races, sticky-toolbar reflow, window
// resize, and <details> reveal — no debounce guesswork, no 0-width lock-in.
const redrawFor = {
  'cmp-canvas': () => drawBars(1),
  'sc-canvas':  () => drawScatter(),
  'dd-canvas':  () => renderDeepDive(),
};
if (window.ResizeObserver) {
  const ro = new ResizeObserver(entries => {
    for (const e of entries) {
      if (e.contentRect.width > 0) { const f = redrawFor[e.target.id]; if (f) f(); }
    }
  });
  Object.keys(redrawFor).forEach(id => { const c=document.getElementById(id); if (c) ro.observe(c); });
} else {
  let rt; window.addEventListener('resize',()=>{ clearTimeout(rt);
    rt=setTimeout(()=>{ drawScatter(); drawBars(1); renderDeepDive(); },150); });
}

// boot
readHash(); syncControls(); renderAll();
window.addEventListener('hashchange',()=>{
  // reset mutable sets from current state so syncControls reflects hash
  readHash();
  buildChips('chips-model',ALL_MODELS,state.models,{sub:v=>SIZE_OF[v]||''});
  buildChips('chips-prompt',ALL_PROMPTS,state.prompts,{});
  buildChips('chips-machine',ALL_MACH,state.mach,{swatch:v=>COLOR[v],label:v=>LABEL_OF[v]});
  syncControls(); renderAll();
});
</script>
</body>
</html>
"""
