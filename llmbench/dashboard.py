"""Build a self-contained, offline HTML dashboard from result sessions.

No network, no CDN, no build step: the result data is embedded as JSON and
rendered by vanilla-JS canvas charts in the page. Open the file in any browser.

Views:
- Machine cards (hardware + session counts).
- Comparison: grouped bar chart + table per metric, one bar per machine.
- Per-machine deep dive: pooled stats plus a per-session line chart so you can
  see how re-runs on the same box vary (consistency / drift).

All numbers come from loader.py, the same source the Markdown report uses.
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

    # comparison[metric][cell_id][machine_slug] = median (pooled)
    comparison: Dict = {metric: {} for metric, *_ in METRICS}
    for k in keys:
        cid = _cell_id(*k)
        for metric, *_ in METRICS:
            comparison[metric].setdefault(cid, {})
        for m in machines:
            summ = m.pooled_summary(k)
            for metric, *_ in METRICS:
                comparison[metric][cid][m.slug] = _median(summ, metric)

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
# no libraries and works fully offline.

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>llmbench dashboard</title>
<style>
  :root {
    --bg: #0f1117; --panel: #171a23; --panel2: #1e222e; --ink: #e6e9ef;
    --muted: #9aa3b2; --line: #2a2f3d; --accent: #6ea8fe;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--ink);
    font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  header {
    padding: 20px 24px; border-bottom: 1px solid var(--line);
    display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap;
  }
  header h1 { margin: 0; font-size: 20px; }
  header .meta { color: var(--muted); font-size: 13px; }
  main { padding: 24px; max-width: 1200px; margin: 0 auto; }
  h2 { font-size: 16px; margin: 28px 0 12px; }
  h3 { font-size: 14px; margin: 18px 0 8px; color: var(--muted); font-weight: 600; }
  .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px,1fr)); gap: 12px; }
  .card { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 14px; }
  .card .name { font-weight: 700; font-size: 15px; }
  .card .slug { color: var(--muted); font-size: 11px; font-family: ui-monospace, monospace; margin-top: 2px; }
  .card dl { margin: 10px 0 0; display: grid; grid-template-columns: auto 1fr; gap: 2px 10px; }
  .card dt { color: var(--muted); } .card dd { margin: 0; }
  .badge { display:inline-block; background: var(--panel2); border:1px solid var(--line);
           border-radius: 999px; padding: 1px 8px; font-size: 12px; color: var(--muted); }
  .controls { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 8px 0 16px; }
  select { background: var(--panel2); color: var(--ink); border: 1px solid var(--line);
           border-radius: 8px; padding: 6px 10px; font-size: 13px; }
  .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 16px; }
  canvas { width: 100%; display: block; }
  table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 13px; }
  th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--line); }
  th { color: var(--muted); font-weight: 600; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  .best { color: #7ee787; font-weight: 700; }
  .dim { color: var(--muted); }
  .legend { display: flex; gap: 14px; flex-wrap: wrap; margin: 8px 0 0; font-size: 12px; }
  .legend span { display: inline-flex; align-items: center; gap: 6px; }
  .swatch { width: 12px; height: 12px; border-radius: 3px; display: inline-block; }
  .empty { color: var(--muted); padding: 20px; text-align: center; }
  details { margin-top: 10px; } summary { cursor: pointer; color: var(--accent); }
  .err td { color: #ff7b72; }
</style>
</head>
<body>
<header>
  <h1>llmbench dashboard</h1>
  <span class="meta" id="hdr-meta"></span>
</header>
<main>
  <section>
    <h2>Machines</h2>
    <div class="cards" id="cards"></div>
  </section>

  <section>
    <h2>Comparison</h2>
    <div class="controls">
      <label>Metric
        <select id="cmp-metric"></select>
      </label>
      <span class="dim" id="cmp-hint"></span>
    </div>
    <div class="panel">
      <canvas id="cmp-canvas" height="360"></canvas>
      <div class="legend" id="cmp-legend"></div>
    </div>
    <div id="cmp-table"></div>
  </section>

  <section>
    <h2>Per-machine deep dive</h2>
    <div class="controls">
      <label>Machine
        <select id="dd-machine"></select>
      </label>
      <label>Cell
        <select id="dd-cell"></select>
      </label>
    </div>
    <div class="panel">
      <div id="dd-stats"></div>
      <h3 style="margin-top:16px">Generation speed per session (re-runs)</h3>
      <canvas id="dd-canvas" height="300"></canvas>
      <p class="dim" id="dd-note"></p>
    </div>
  </section>

  <section id="err-section" style="display:none">
    <h2>Errors</h2>
    <div id="err-table"></div>
  </section>
</main>

<script id="data" type="application/json">/*__DATA__*/</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);

// Stable color per machine.
const PALETTE = ['#6ea8fe','#7ee787','#ffa657','#d2a8ff','#ff7b72','#79c0ff','#f0883e','#56d364'];
const COLOR = {};
DATA.machines.forEach((m,i) => COLOR[m.slug] = PALETTE[i % PALETTE.length]);

const fmt = (v, d) => (typeof v === 'number' && isFinite(v)) ? v.toFixed(d) : '—';
const el = (t, props={}, kids=[]) => {
  const n = document.createElement(t);
  for (const [k,v] of Object.entries(props)) {
    if (k === 'class') n.className = v; else if (k === 'html') n.innerHTML = v; else n.setAttribute(k,v);
  }
  (Array.isArray(kids)?kids:[kids]).forEach(c => n.append(c));
  return n;
};

// ---- header + machine cards ----
document.getElementById('hdr-meta').textContent =
  `generated ${DATA.generated_at} · ${DATA.machines.length} machine(s) · ` +
  `${DATA.machines.reduce((a,m)=>a+m.sessions,0)} session(s) · ${DATA.cells.length} cell(s)`;

const cards = document.getElementById('cards');
DATA.machines.forEach(m => {
  const dl = el('dl');
  const rows = [
    ['OS', `${m.os||'—'} ${m.arch||''}`],
    ['CPU', m.cpu||'—'],
    ['RAM', m.ram_gb!=null ? m.ram_gb+' GB' : '—'],
    ['GPU', (m.gpus&&m.gpus.length)?m.gpus.join(', '):'—'],
  ];
  rows.forEach(([k,v]) => { dl.append(el('dt',{},document.createTextNode(k))); dl.append(el('dd',{},document.createTextNode(v))); });
  const card = el('div',{class:'card'},[
    el('div',{},[
      el('span',{class:'swatch',style:`background:${COLOR[m.slug]};margin-right:6px`}),
      el('span',{class:'name'},document.createTextNode(m.label)),
    ]),
    el('div',{class:'slug'},document.createTextNode(m.slug)),
    el('div',{style:'margin-top:6px'},el('span',{class:'badge'},document.createTextNode(m.sessions+' session(s)'))),
    dl,
  ]);
  cards.append(card);
});

// ---- shared bar/line drawing on canvas (retina-aware) ----
function setupCanvas(canvas, cssHeight) {
  const ratio = window.devicePixelRatio || 1;
  const cssWidth = canvas.clientWidth || canvas.parentElement.clientWidth;
  canvas.width = cssWidth * ratio;
  canvas.height = cssHeight * ratio;
  canvas.style.height = cssHeight + 'px';
  const ctx = canvas.getContext('2d');
  ctx.setTransform(ratio,0,0,ratio,0,0);
  ctx.clearRect(0,0,cssWidth,cssHeight);
  return {ctx, w: cssWidth, h: cssHeight};
}

function axes(ctx, w, h, pad, maxVal, yLabel) {
  ctx.strokeStyle = '#2a2f3d'; ctx.fillStyle = '#9aa3b2';
  ctx.font = '11px -apple-system, sans-serif'; ctx.lineWidth = 1;
  const ticks = 5;
  for (let i=0;i<=ticks;i++){
    const y = pad.top + (h-pad.top-pad.bottom)*(1-i/ticks);
    const val = maxVal*i/ticks;
    ctx.beginPath(); ctx.moveTo(pad.left,y); ctx.lineTo(w-pad.right,y); ctx.stroke();
    ctx.textAlign='right'; ctx.textBaseline='middle';
    ctx.fillText(val.toFixed(maxVal<10?1:0), pad.left-6, y);
  }
  if (yLabel){ ctx.save(); ctx.translate(12,(pad.top+h-pad.bottom)/2); ctx.rotate(-Math.PI/2);
    ctx.textAlign='center'; ctx.fillText(yLabel,0,0); ctx.restore(); }
}

// Grouped bars: groups = cells, series = machines.
function drawGroupedBars(canvas, groups, series, valueOf, yLabel) {
  const {ctx,w,h} = setupCanvas(canvas, canvas.getAttribute('height')*1);
  const pad = {top:16,right:16,bottom:70,left:48};
  let maxVal = 0;
  groups.forEach(g => series.forEach(s => { const v=valueOf(g,s); if (typeof v==='number'&&v>maxVal) maxVal=v; }));
  if (maxVal<=0){ ctx.fillStyle='#9aa3b2'; ctx.fillText('No data',pad.left,pad.top+20); return; }
  maxVal *= 1.1;
  axes(ctx,w,h,pad,maxVal,yLabel);
  const plotW = w-pad.left-pad.right, plotH = h-pad.top-pad.bottom;
  const gW = plotW/groups.length, bW = Math.min(46, (gW*0.8)/series.length);
  groups.forEach((g,gi) => {
    const gx = pad.left + gi*gW + gW/2;
    series.forEach((s,si) => {
      const v = valueOf(g,s); if (typeof v!=='number'||!isFinite(v)) return;
      const bh = plotH*(v/maxVal);
      const x = gx - (series.length*bW)/2 + si*bW;
      const y = pad.top+plotH-bh;
      ctx.fillStyle = COLOR[s.slug]; ctx.fillRect(x,y,bW-2,bh);
    });
    ctx.save(); ctx.fillStyle='#9aa3b2'; ctx.font='11px sans-serif';
    ctx.translate(gx, pad.top+plotH+8); ctx.rotate(Math.PI/5); ctx.textAlign='left';
    ctx.fillText(g.short, 0, 0); ctx.restore();
  });
}

// Multi-line chart: x = sessions, one line per cell.
function drawLines(canvas, labels, lines, yLabel) {
  const {ctx,w,h} = setupCanvas(canvas, canvas.getAttribute('height')*1);
  const pad = {top:16,right:16,bottom:40,left:48};
  let maxVal=0; lines.forEach(ln=>ln.points.forEach(p=>{if(typeof p==='number'&&p>maxVal)maxVal=p;}));
  if (maxVal<=0 || labels.length===0){ ctx.fillStyle='#9aa3b2'; ctx.fillText('No data',pad.left,pad.top+20); return; }
  maxVal*=1.1; axes(ctx,w,h,pad,maxVal,yLabel);
  const plotW=w-pad.left-pad.right, plotH=h-pad.top-pad.bottom;
  const xAt = i => pad.left + (labels.length===1?plotW/2:plotW*i/(labels.length-1));
  const yAt = v => pad.top+plotH*(1-v/maxVal);
  // x labels
  ctx.fillStyle='#9aa3b2'; ctx.font='11px sans-serif'; ctx.textAlign='center'; ctx.textBaseline='top';
  labels.forEach((lb,i)=>ctx.fillText(lb, xAt(i), pad.top+plotH+8));
  lines.forEach((ln,li)=>{
    ctx.strokeStyle = ln.color || PALETTE[li%PALETTE.length]; ctx.lineWidth=2; ctx.beginPath();
    let started=false;
    ln.points.forEach((p,i)=>{ if(typeof p!=='number'||!isFinite(p))return;
      const x=xAt(i),y=yAt(p); if(!started){ctx.moveTo(x,y);started=true;}else ctx.lineTo(x,y);});
    ctx.stroke();
    ctx.fillStyle = ln.color || PALETTE[li%PALETTE.length];
    ln.points.forEach((p,i)=>{ if(typeof p!=='number'||!isFinite(p))return;
      ctx.beginPath(); ctx.arc(xAt(i),yAt(p),3,0,7); ctx.fill();});
  });
}

// ---- comparison view ----
const cmpMetric = document.getElementById('cmp-metric');
DATA.metrics.forEach(m => cmpMetric.append(el('option',{value:m.key},document.createTextNode(m.label))));

function cellShort(c){ return `${c.model} · ${c.format} · ${c.prompt_id} (${c.backend})`; }

function renderComparison() {
  const metricKey = cmpMetric.value;
  const metric = DATA.metrics.find(m=>m.key===metricKey);
  document.getElementById('cmp-hint').textContent =
    metric.higher_is_better ? 'higher is better' : 'lower is better';

  const groups = DATA.cells.map(c => ({...c, short: cellShort(c)}));
  const series = DATA.machines;
  const valueOf = (g,s) => DATA.comparison[metricKey][g.id]?.[s.slug];
  drawGroupedBars(document.getElementById('cmp-canvas'), groups, series, valueOf, metric.label);

  // legend
  const legend = document.getElementById('cmp-legend'); legend.innerHTML='';
  series.forEach(s => legend.append(el('span',{},[
    el('span',{class:'swatch',style:`background:${COLOR[s.slug]}`}), document.createTextNode(s.label)])));

  // table with best-per-row highlight
  const tbl = el('table');
  const head = el('tr',{},[el('th',{},document.createTextNode('Backend')),
    el('th',{},document.createTextNode('Model')), el('th',{},document.createTextNode('Format')), el('th',{},document.createTextNode('Size')),
    el('th',{},document.createTextNode('Prompt'))]);
  series.forEach(s=>head.append(el('th',{class:'num'},document.createTextNode(s.label))));
  tbl.append(el('thead',{},head));
  const body = el('tbody');
  groups.forEach(g=>{
    const vals = series.map(s=>valueOf(g,s));
    const present = vals.filter(v=>typeof v==='number');
    const best = present.length>1 ? (metric.higher_is_better?Math.max(...present):Math.min(...present)) : null;
    const tr = el('tr',{},[el('td',{},document.createTextNode(g.backend)),
      el('td',{},document.createTextNode(g.model)), el('td',{},document.createTextNode(g.format)), el('td',{},document.createTextNode(g.size||'—')),
      el('td',{},document.createTextNode(g.prompt_id))]);
    vals.forEach(v=>{
      const td = el('td',{class:'num'},document.createTextNode(fmt(v,metric.digits)));
      if (best!=null && v===best) td.classList.add('best');
      tr.append(td);
    });
    body.append(tr);
  });
  tbl.append(body);
  const holder = document.getElementById('cmp-table'); holder.innerHTML=''; holder.append(tbl);
}
cmpMetric.addEventListener('change', renderComparison);

// ---- deep dive ----
const ddMachine = document.getElementById('dd-machine');
const ddCell = document.getElementById('dd-cell');
DATA.machines.forEach(m=>ddMachine.append(el('option',{value:m.slug},document.createTextNode(m.label))));

function refreshCellOptions() {
  const slug = ddMachine.value;
  const cells = Object.keys(DATA.deep[slug]||{});
  ddCell.innerHTML='';
  cells.forEach(cid=>ddCell.append(el('option',{value:cid},document.createTextNode(cid))));
}

function renderDeepDive() {
  const slug = ddMachine.value, cid = ddCell.value;
  const entry = (DATA.deep[slug]||{})[cid];
  const statsHolder = document.getElementById('dd-stats');
  const note = document.getElementById('dd-note');
  statsHolder.innerHTML='';
  if (!entry) { statsHolder.append(el('div',{class:'empty'},document.createTextNode('No data for this selection.')));
    drawLines(document.getElementById('dd-canvas'), [], []); note.textContent=''; return; }

  // pooled stats table
  const tbl = el('table');
  tbl.append(el('thead',{},el('tr',{},[el('th',{},document.createTextNode('Metric')),
    el('th',{class:'num'},document.createTextNode('median')), el('th',{class:'num'},document.createTextNode('min')),
    el('th',{class:'num'},document.createTextNode('max')), el('th',{class:'num'},document.createTextNode('± stdev')),
    el('th',{class:'num'},document.createTextNode('samples'))])));
  const body = el('tbody');
  DATA.metrics.forEach(m=>{
    const p = entry.pooled[m.key]||{};
    body.append(el('tr',{},[el('td',{},document.createTextNode(m.label)),
      el('td',{class:'num'},document.createTextNode(fmt(p.median,m.digits))),
      el('td',{class:'num'},document.createTextNode(fmt(p.min,m.digits))),
      el('td',{class:'num'},document.createTextNode(fmt(p.max,m.digits))),
      el('td',{class:'num'},document.createTextNode(fmt(p.stdev,m.digits))),
      el('td',{class:'num'},document.createTextNode(p.n!=null?String(p.n):'—'))]));
  });
  tbl.append(body); statsHolder.append(tbl);

  // per-session line for generation speed
  const series = entry.sessions;
  const labels = series.map(s=>s.label);
  drawLines(document.getElementById('dd-canvas'), labels,
    [{points: series.map(s=>s.gen), color: COLOR[slug]}], 'tok/s');
  const nRunsTotal = series.reduce((a,s)=>a+(s.n_runs||0),0);
  note.textContent = `${series.length} session(s), ${nRunsTotal} measured run(s) pooled. `+
    `Each point is one session's median generation speed; a flat line means the machine is consistent across re-runs.`;
}
ddMachine.addEventListener('change', ()=>{ refreshCellOptions(); renderDeepDive(); });
ddCell.addEventListener('change', renderDeepDive);

// ---- errors ----
if (DATA.errors.length) {
  document.getElementById('err-section').style.display='';
  const tbl = el('table');
  tbl.append(el('thead',{},el('tr',{},['Machine','Backend','Model','Prompt','Error']
    .map(h=>el('th',{},document.createTextNode(h))))));
  const body = el('tbody');
  DATA.errors.forEach(e=>body.append(el('tr',{class:'err'},
    [e.machine,e.backend,e.model,e.prompt_id,e.error].map(v=>el('td',{},document.createTextNode(v||''))))));
  tbl.append(body);
  document.getElementById('err-table').append(tbl);
}

// initial paint + redraw on resize
function renderAll(){ renderComparison(); refreshCellOptions(); renderDeepDive(); }
renderAll();
let rt; window.addEventListener('resize',()=>{ clearTimeout(rt); rt=setTimeout(renderAll,150); });
</script>
</body>
</html>
"""
