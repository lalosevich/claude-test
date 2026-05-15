#!/usr/bin/env python3
"""ccburn_web - browser dashboard for Claude Code usage."""

import argparse
import logging
import os
import sys
import threading
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from flask import Flask, Response, jsonify, request
except ImportError:
    sys.stderr.write("Missing dependency: pip install flask\n")
    sys.exit(1)

from ccburn_lib import (
    PLAN_LIMITS, calibrate, find_jsonl, get_snapshot, load_config,
    parse_records, save_snapshot, session_window, weekly_window,
)


def serialize_window(start, end, recs, budget, now):
    series = []
    total = 0.0
    for t, n in recs:
        total += n
        series.append({
            "minute": (t - start).total_seconds() / 60.0,
            "pct": (total / budget * 100) if budget else 0,
            "total": total,
        })
    total_min = (end - start).total_seconds() / 60.0
    elapsed_min = max(0.0, min(total_min, (now - start).total_seconds() / 60.0))
    rate = total / elapsed_min if elapsed_min > 0 else 0
    proj_total = rate * total_min if elapsed_min > 0 else 0
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "total_minutes": total_min,
        "elapsed_minutes": elapsed_min,
        "elapsed_pct": (elapsed_min / total_min * 100) if total_min else 0,
        "total_tokens": total,
        "budget": budget,
        "pct": (total / budget * 100) if budget else 0,
        "projection_pct": (proj_total / budget * 100) if budget else 0,
        "reset_seconds": max(0, (end - now).total_seconds()),
        "series": series,
    }


CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


def make_app(args):
    app = Flask(__name__)

    @app.after_request
    def add_cors(resp):
        for k, v in CORS_HEADERS.items():
            resp.headers.setdefault(k, v)
        return resp

    @app.route("/")
    def index():
        return Response(INDEX_HTML, mimetype="text/html")

    @app.route("/api/usage")
    def api_usage():
        claude_dir = Path(args.claude_dir).expanduser()
        if not claude_dir.exists():
            return jsonify({"error": f"No Claude data directory at {claude_dir}"}), 404
        records = list(parse_records(find_jsonl(claude_dir)))
        now = datetime.now(timezone.utc)
        s_start, s_end, s_rec = session_window(records, now, hours=5)
        w_start, w_end, w_rec = weekly_window(
            records, now, args.week_reset_day, args.week_reset_hour)
        return jsonify({
            "plan": args.plan,
            "generated_at": now.isoformat(),
            "session": serialize_window(s_start, s_end, s_rec, args.session_limit, now),
            "weekly":  serialize_window(w_start, w_end, w_rec, args.weekly_limit, now),
            "snapshot": get_snapshot(),
            "latest_record": (max((t for t, _ in records), default=None)
                              .isoformat() if records else None),
        })

    @app.route("/ccburn.user.js")
    def userscript():
        try:
            content = _userscript_path().read_text(encoding="utf-8")
        except OSError:
            return Response("// userscript file missing", mimetype="text/javascript", status=500)
        return Response(content, mimetype="text/javascript")

    @app.route("/api/snapshot", methods=["GET", "POST", "OPTIONS"])
    def api_snapshot():
        if request.method == "OPTIONS":
            return ("", 204)
        if request.method == "GET":
            if request.args:
                save_snapshot(
                    session_pct=request.args.get("session_pct", type=float),
                    weekly_pct=request.args.get("weekly_pct", type=float),
                    session_reset_seconds=request.args.get("session_reset_seconds", type=float),
                    weekly_reset_seconds=request.args.get("weekly_reset_seconds", type=float),
                )
                return Response(SNAPSHOT_OK_HTML, mimetype="text/html")
            return jsonify(get_snapshot())
        data = request.get_json(silent=True) or {}
        snap = save_snapshot(
            session_pct=data.get("session_pct"),
            weekly_pct=data.get("weekly_pct"),
            session_reset_seconds=data.get("session_reset_seconds"),
            weekly_reset_seconds=data.get("weekly_reset_seconds"),
            sonnet_pct=data.get("sonnet_pct"),
        )
        return jsonify(snap)

    return app


def _userscript_path():
    return Path(__file__).with_name("ccburn.user.js")


SNAPSHOT_OK_HTML = """<!doctype html>
<html><head><title>ccburn updated</title></head>
<body style="background:#0b0b10;color:#e8e8f0;font-family:-apple-system,Segoe UI,sans-serif;
             padding:80px 40px;text-align:center;margin:0">
  <h1 style="font-size:24px">ccburn updated</h1>
  <p style="color:#8a8a9a">Snapshot saved. This tab will close in a moment.</p>
  <p><a style="color:#ff7849;text-decoration:none"
        href="http://127.0.0.1:8765/">Open dashboard &rarr;</a></p>
  <script>setTimeout(function(){try{window.close()}catch(e){}}, 1500);</script>
</body></html>"""


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ccburn</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='86' font-size='86'>%F0%9F%94%A5</text></svg>">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #0b0b10; --panel: #15151d; --panel-2: #1c1c26;
    --border: #2a2a38; --text: #e8e8f0; --dim: #8a8a9a;
    --accent: #ff7849; --green: #4ade80; --yellow: #fbbf24;
    --red: #ef4444; --blue: #60a5fa; --purple: #a78bfa;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0;
    background: radial-gradient(ellipse at top, #16161f, var(--bg) 60%);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    min-height: 100vh;
  }
  header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 24px 32px 8px; max-width: 1280px; margin: 0 auto;
  }
  header .brand { display: flex; align-items: baseline; gap: 12px; }
  header h1 { font-size: 28px; margin: 0; font-weight: 700; letter-spacing: -0.02em; }
  header .tagline { color: var(--dim); font-size: 13px; }
  header .plan {
    color: var(--dim); font-size: 13px;
    background: var(--panel); border: 1px solid var(--border);
    padding: 6px 12px; border-radius: 999px;
  }
  header .plan b { color: var(--accent); font-weight: 600; }
  main {
    display: grid; grid-template-columns: 1fr 1fr; gap: 20px;
    padding: 16px 32px 32px; max-width: 1280px; margin: 0 auto;
  }
  @media (max-width: 900px) { main { grid-template-columns: 1fr; } }
  .panel {
    background: linear-gradient(180deg, var(--panel-2), var(--panel));
    border: 1px solid var(--border); border-radius: 16px;
    padding: 20px 22px; box-shadow: 0 10px 30px rgba(0,0,0,0.35);
  }
  .panel-head {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 18px;
  }
  .panel-head h2 { font-size: 18px; margin: 0; font-weight: 600; }
  .panel-head .duration { color: var(--dim); font-weight: 400; font-size: 13px; margin-left: 6px; }
  .panel-head .reset { color: var(--dim); font-size: 13px; }
  .panel-head .reset b { color: var(--text); font-weight: 600; }
  .bar-row {
    display: grid; grid-template-columns: 70px 1fr 70px; align-items: center;
    gap: 12px; margin-bottom: 10px;
  }
  .bar-row .label { color: var(--dim); font-size: 13px; text-transform: uppercase; letter-spacing: 0.05em; }
  .bar { position: relative; height: 10px; background: rgba(255,255,255,0.06);
    border-radius: 999px; overflow: hidden; }
  .bar-fill {
    height: 100%; border-radius: 999px;
    transition: width 0.6s cubic-bezier(.2,.7,.2,1), background 0.3s;
    background: linear-gradient(90deg, var(--green), var(--green));
    box-shadow: 0 0 12px rgba(74,222,128,0.4);
  }
  .bar-fill.elapsed {
    background: linear-gradient(90deg, var(--blue), var(--purple));
    box-shadow: 0 0 12px rgba(96,165,250,0.3);
  }
  .pct { text-align: right; font-variant-numeric: tabular-nums; font-size: 13px; }
  .chart-wrap { position: relative; height: 220px; margin-top: 18px; }
  .totals { display: flex; justify-content: space-between; align-items: center;
    margin-top: 14px; color: var(--dim); font-size: 12px; }
  .totals b { color: var(--text); font-weight: 600; font-variant-numeric: tabular-nums; }
  footer { max-width: 1280px; margin: 0 auto;
    padding: 8px 32px 24px; color: var(--dim); font-size: 12px; text-align: center; }
  .pulse { animation: pulse 1.4s ease-in-out infinite; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
  .err {
    background: rgba(239,68,68,0.1); border: 1px solid var(--red); color: var(--red);
    padding: 16px; border-radius: 12px; margin: 16px 32px; max-width: 1280px;
  }
  .snapshot {
    max-width: 1280px; margin: 0 auto;
    padding: 0 32px;
  }
  .snapshot-card {
    background: linear-gradient(180deg, var(--panel-2), var(--panel));
    border: 1px solid var(--border); border-radius: 16px;
    padding: 18px 22px; box-shadow: 0 10px 30px rgba(0,0,0,0.35);
  }
  .snapshot-head {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 14px;
  }
  .snapshot-head h2 { font-size: 16px; margin: 0; font-weight: 600; }
  .snapshot-head .sub { color: var(--dim); font-size: 12px; margin-left: 8px; }
  .snapshot-head .updated { color: var(--dim); font-size: 12px; margin-right: 10px; }
  .btn {
    background: var(--accent); color: #1a0a00; border: none;
    padding: 6px 14px; border-radius: 8px; font-weight: 600; font-size: 12px;
    cursor: pointer; transition: filter 0.15s;
  }
  .btn:hover { filter: brightness(1.1); }
  .btn-ghost {
    background: transparent; color: var(--text); border: 1px solid var(--border);
  }
  .snap-row {
    display: grid; grid-template-columns: 90px 1fr 70px 90px; align-items: center;
    gap: 12px; margin-bottom: 8px;
  }
  .snap-row .label { color: var(--dim); font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; }
  .snap-row .reset { text-align: right; color: var(--dim); font-size: 12px; }
  .modal-bg {
    position: fixed; inset: 0; background: rgba(0,0,0,0.6); display: none;
    align-items: center; justify-content: center; z-index: 10;
  }
  .modal-bg.show { display: flex; }
  .modal {
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 14px; padding: 24px; min-width: 360px; max-width: 480px;
  }
  .modal h3 { margin: 0 0 6px 0; font-size: 18px; }
  .modal p { color: var(--dim); font-size: 13px; margin: 0 0 16px 0; }
  .field { display: grid; grid-template-columns: 130px 1fr; align-items: center; gap: 10px; margin-bottom: 10px; }
  .field label { color: var(--dim); font-size: 13px; }
  .field input {
    background: #0b0b10; border: 1px solid var(--border); color: var(--text);
    padding: 8px 10px; border-radius: 8px; font-size: 14px; outline: none;
  }
  .field input:focus { border-color: var(--accent); }
  .modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
  .stale { color: var(--yellow); }
  details.help {
    margin-top: 10px; color: var(--dim); font-size: 12px;
  }
  details.help summary { cursor: pointer; }
  details.help code { background: #0b0b10; padding: 2px 6px; border-radius: 4px; }
</style>
</head>
<body>
<header>
  <div class="brand">
    <h1>🔥 ccburn</h1>
    <span class="tagline">Claude Code usage</span>
  </div>
  <div class="plan">Plan · <b id="plan">—</b></div>
</header>

<div id="err"></div>

<div class="snapshot">
  <div class="snapshot-card">
    <div class="snapshot-head">
      <div>
        <h2 style="display:inline">Anthropic (claude.ai)</h2>
        <span class="sub">manual snapshot — your real account-wide usage</span>
      </div>
      <div>
        <span class="updated" id="snap-updated">no snapshot yet</span>
        <button class="btn" onclick="openSnapModal()">Update</button>
      </div>
    </div>
    <div class="snap-row">
      <span class="label">Session</span>
      <div class="bar"><div class="bar-fill" id="snap-session-fill"></div></div>
      <span class="pct" id="snap-session-pct">—</span>
      <span class="reset" id="snap-session-reset">—</span>
    </div>
    <div class="snap-row">
      <span class="label">Weekly</span>
      <div class="bar"><div class="bar-fill" id="snap-weekly-fill"></div></div>
      <span class="pct" id="snap-weekly-pct">—</span>
      <span class="reset" id="snap-weekly-reset">—</span>
    </div>
    <details class="help" open>
      <summary>Set up automatic syncing (one-time, ~30 seconds)</summary>
      <ol style="margin:8px 0 0 18px;padding:0">
        <li>Install <a href="https://chrome.google.com/webstore/detail/tampermonkey/dhdgffkkebhmkfjojejmpbldmpobfkfo" target="_blank" style="color:var(--accent)">Tampermonkey</a> for Chrome (free, ~10s).</li>
        <li>Click here: <a href="/ccburn.user.js" target="_blank" style="color:var(--accent);font-weight:600">install ccburn auto-sync</a> — Tampermonkey will pop up &rarr; click <b>Install</b>.</li>
        <li>Keep any <code>claude.ai</code> tab open. The userscript fetches your usage page in a hidden iframe every 10 min and pushes the %s here. No clicks ever.</li>
      </ol>
      <p style="margin-top:10px">Prefer a manual one-click instead? Drag <a id="bookmarklet" href="#">ccburn snapshot</a> to your bookmarks bar, then click it while on <code>claude.ai/settings/usage</code>.</p>
    </details>
  </div>
</div>

<div class="modal-bg" id="snap-modal">
  <div class="modal">
    <h3>Update Anthropic snapshot</h3>
    <p>Copy your current numbers from <a href="https://claude.ai/settings/usage" target="_blank" style="color:var(--accent)">claude.ai/settings/usage</a>.</p>
    <div class="field">
      <label for="in-session-pct">Session %</label>
      <input id="in-session-pct" type="number" min="0" max="100" step="0.1" placeholder="39">
    </div>
    <div class="field">
      <label for="in-session-reset">Resets in (e.g. 1h 13m)</label>
      <input id="in-session-reset" type="text" placeholder="1h 13m">
    </div>
    <div class="field">
      <label for="in-weekly-pct">Weekly %</label>
      <input id="in-weekly-pct" type="number" min="0" max="100" step="0.1" placeholder="46">
    </div>
    <div class="field">
      <label for="in-weekly-reset">Resets in (e.g. 2d 14h)</label>
      <input id="in-weekly-reset" type="text" placeholder="2d 14h">
    </div>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeSnapModal()">Cancel</button>
      <button class="btn" onclick="saveSnap()">Save</button>
    </div>
  </div>
</div>

<main>
  <section class="panel">
    <div class="panel-head">
      <h2>CLI Session<span class="duration">5h · Claude Code only</span></h2>
      <div class="reset">resets in <b id="session-reset" class="pulse">—</b></div>
    </div>
    <div class="bar-row">
      <span class="label">Usage</span>
      <div class="bar"><div class="bar-fill" id="session-usage"></div></div>
      <span class="pct" id="session-usage-pct">—</span>
    </div>
    <div class="bar-row">
      <span class="label">Elapsed</span>
      <div class="bar"><div class="bar-fill elapsed" id="session-elapsed"></div></div>
      <span class="pct" id="session-elapsed-pct">—</span>
    </div>
    <div class="chart-wrap"><canvas id="session-chart"></canvas></div>
    <div class="totals">
      <span><b id="session-tokens">—</b> / <span id="session-budget">—</span></span>
      <span>projection: <b id="session-proj">—</b></span>
    </div>
  </section>

  <section class="panel">
    <div class="panel-head">
      <h2>CLI Weekly<span class="duration">7d · Claude Code only</span></h2>
      <div class="reset">resets in <b id="weekly-reset" class="pulse">—</b></div>
    </div>
    <div class="bar-row">
      <span class="label">Usage</span>
      <div class="bar"><div class="bar-fill" id="weekly-usage"></div></div>
      <span class="pct" id="weekly-usage-pct">—</span>
    </div>
    <div class="bar-row">
      <span class="label">Elapsed</span>
      <div class="bar"><div class="bar-fill elapsed" id="weekly-elapsed"></div></div>
      <span class="pct" id="weekly-elapsed-pct">—</span>
    </div>
    <div class="chart-wrap"><canvas id="weekly-chart"></canvas></div>
    <div class="totals">
      <span><b id="weekly-tokens">—</b> / <span id="weekly-budget">—</span></span>
      <span>projection: <b id="weekly-proj">—</b></span>
    </div>
  </section>
</main>

<footer>
  Top panel: manual snapshots of Anthropic's account-wide numbers (covers web + CLI + desktop).<br>
  Lower panels: tokens parsed from local Claude Code JSONLs only — won't reflect claude.ai or Desktop activity.
</footer>

<script>
const CSS = getComputedStyle(document.documentElement);
const C = {
  green:  CSS.getPropertyValue('--green').trim(),
  yellow: CSS.getPropertyValue('--yellow').trim(),
  red:    CSS.getPropertyValue('--red').trim(),
  blue:   CSS.getPropertyValue('--blue').trim(),
  purple: CSS.getPropertyValue('--purple').trim(),
  dim:    CSS.getPropertyValue('--dim').trim(),
};
function pickColor(p) { return p < 70 ? C.green : p < 90 ? C.yellow : C.red; }
function pickGlow(p) {
  return p < 70 ? 'rgba(74,222,128,0.45)' :
         p < 90 ? 'rgba(251,191,36,0.45)' : 'rgba(239,68,68,0.5)';
}
function fmtTokens(n) {
  if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return Math.round(n).toLocaleString();
}
function fmtDuration(s) {
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  if (h >= 24) { const d = Math.floor(h / 24); return `${d}d ${h % 24}h`; }
  if (h) return `${h}h ${String(m).padStart(2,'0')}m`;
  return `${m}m`;
}

function makeChart(ctx, totalMin) {
  return new Chart(ctx, {
    type: 'line',
    data: { datasets: [
      { label: 'Usage', data: [], borderColor: C.green,
        backgroundColor: 'rgba(74,222,128,0.18)', fill: 'origin',
        tension: 0.25, borderWidth: 2, pointRadius: 0 },
      { label: 'Pace', data: [{x:0,y:0},{x:totalMin,y:100}],
        borderColor: C.dim, borderDash: [4,4], borderWidth: 1, pointRadius: 0, fill: false },
      { label: 'Projection', data: [], borderColor: C.purple, borderDash: [6,3],
        borderWidth: 1.5, pointRadius: 0, fill: false },
      { label: 'Now', data: [], borderColor: C.blue, borderWidth: 1.5,
        borderDash: [2,3], pointRadius: 0, fill: false },
    ]},
    options: {
      responsive: true, maintainAspectRatio: false, animation: { duration: 400 },
      interaction: { mode: 'nearest', intersect: false },
      plugins: {
        legend: { display: true, position: 'top', align: 'end',
          labels: { color: C.dim, boxWidth: 10, boxHeight: 2, font: { size: 11 } } },
        tooltip: { mode: 'index', intersect: false,
          backgroundColor: 'rgba(20,20,28,0.95)', borderColor: '#2a2a38', borderWidth: 1,
          callbacks: { label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(1)}%` } },
      },
      scales: {
        x: { type: 'linear', min: 0, max: totalMin,
             grid: { color: 'rgba(255,255,255,0.04)' },
             ticks: { color: C.dim, font: { size: 10 },
                      callback: v => totalMin > 600 ? `${Math.round(v/60/24)}d` : `${Math.round(v/60)}h` } },
        y: { min: 0, max: 110, grid: { color: 'rgba(255,255,255,0.04)' },
             ticks: { color: C.dim, font: { size: 10 }, callback: v => v + '%' } },
      },
    },
  });
}

let sChart, wChart;
function updatePanel(prefix, d, chart) {
  const pct = d.pct, ep = d.elapsed_pct;
  const u = document.getElementById(prefix + '-usage');
  u.style.width = Math.min(100, pct) + '%';
  u.style.background = `linear-gradient(90deg, ${pickColor(pct)}, ${pickColor(pct)})`;
  u.style.boxShadow = `0 0 12px ${pickGlow(pct)}`;
  document.getElementById(prefix + '-usage-pct').textContent = pct.toFixed(1) + '%';
  document.getElementById(prefix + '-elapsed').style.width = Math.min(100, ep) + '%';
  document.getElementById(prefix + '-elapsed-pct').textContent = ep.toFixed(1) + '%';
  document.getElementById(prefix + '-reset').textContent = fmtDuration(d.reset_seconds);
  document.getElementById(prefix + '-tokens').textContent = fmtTokens(d.total_tokens);
  document.getElementById(prefix + '-budget').textContent = fmtTokens(d.budget);
  document.getElementById(prefix + '-proj').textContent = d.projection_pct.toFixed(1) + '%';

  const pts = d.series.map(p => ({ x: p.minute, y: Math.min(120, p.pct) }));
  chart.data.datasets[0].data = pts;
  chart.data.datasets[0].borderColor = pickColor(pct);
  chart.data.datasets[0].backgroundColor = pickGlow(pct).replace('0.45', '0.18').replace('0.5','0.2');
  chart.data.datasets[2].data = pts.length
    ? [{x:0,y:0},{x:d.total_minutes, y: Math.min(120, d.projection_pct)}] : [];
  chart.data.datasets[3].data = [
    {x: d.elapsed_minutes, y: 0}, {x: d.elapsed_minutes, y: 110}];
  chart.options.scales.x.max = d.total_minutes;
  chart.update();
}

async function refresh() {
  try {
    const r = await fetch('/api/usage');
    const d = await r.json();
    if (d.error) { document.getElementById('err').innerHTML = `<div class="err">${d.error}</div>`; return; }
    document.getElementById('err').innerHTML = '';
    document.getElementById('plan').textContent = d.plan;
    if (!sChart) sChart = makeChart(document.getElementById('session-chart'), d.session.total_minutes);
    if (!wChart) wChart = makeChart(document.getElementById('weekly-chart'), d.weekly.total_minutes);
    updatePanel('session', d.session, sChart);
    updatePanel('weekly', d.weekly, wChart);
  } catch (e) {
    document.getElementById('err').innerHTML = `<div class="err">Failed to load: ${e}</div>`;
  }
}
refresh();
setInterval(refresh, 5000);

// ------- Anthropic snapshot panel -------

function parseDurationToSeconds(s) {
  if (!s) return null;
  s = String(s).trim().toLowerCase();
  let total = 0; let matched = false;
  const re = /(\d+)\s*(d|h|hr|hrs|m|min|mins)/g; let m;
  while ((m = re.exec(s)) !== null) {
    matched = true;
    const n = parseInt(m[1]);
    const u = m[2];
    if (u === 'd') total += n * 86400;
    else if (u.startsWith('h')) total += n * 3600;
    else if (u.startsWith('m')) total += n * 60;
  }
  if (!matched) {
    const n = parseFloat(s);
    if (!isNaN(n)) total = n * 60; // bare number = minutes
  }
  return total || null;
}

function renderSnap(snap) {
  if (!snap || !snap.updated_at) {
    document.getElementById('snap-updated').textContent = 'no snapshot yet';
    return;
  }
  const ageSec = (Date.now() - new Date(snap.updated_at).getTime()) / 1000;
  const ageStr = ageSec < 60 ? 'just now'
               : ageSec < 3600 ? `${Math.round(ageSec/60)}m ago`
               : `${Math.round(ageSec/3600)}h ago`;
  const u = document.getElementById('snap-updated');
  u.textContent = 'updated ' + ageStr;
  u.className = 'updated' + (ageSec > 1800 ? ' stale' : '');

  if (snap.session_pct != null) {
    const p = snap.session_pct;
    const f = document.getElementById('snap-session-fill');
    f.style.width = Math.min(100, p) + '%';
    f.style.background = `linear-gradient(90deg, ${pickColor(p)}, ${pickColor(p)})`;
    f.style.boxShadow = `0 0 12px ${pickGlow(p)}`;
    document.getElementById('snap-session-pct').textContent = p.toFixed(1) + '%';
  }
  if (snap.session_reset_seconds != null) {
    const elapsed = (Date.now() - new Date(snap.updated_at).getTime()) / 1000;
    document.getElementById('snap-session-reset').textContent =
      'resets in ' + fmtDuration(snap.session_reset_seconds - elapsed);
  }
  if (snap.weekly_pct != null) {
    const p = snap.weekly_pct;
    const f = document.getElementById('snap-weekly-fill');
    f.style.width = Math.min(100, p) + '%';
    f.style.background = `linear-gradient(90deg, ${pickColor(p)}, ${pickColor(p)})`;
    f.style.boxShadow = `0 0 12px ${pickGlow(p)}`;
    document.getElementById('snap-weekly-pct').textContent = p.toFixed(1) + '%';
  }
  if (snap.weekly_reset_seconds != null) {
    const elapsed = (Date.now() - new Date(snap.updated_at).getTime()) / 1000;
    document.getElementById('snap-weekly-reset').textContent =
      'resets in ' + fmtDuration(snap.weekly_reset_seconds - elapsed);
  }
}

// Patch refresh() to also pull snapshot
const _origRefresh = refresh;
refresh = async function() {
  await _origRefresh();
  try {
    const r = await fetch('/api/usage');
    const d = await r.json();
    if (d.snapshot) renderSnap(d.snapshot);
  } catch (e) { /* ignore */ }
};

function openSnapModal() {
  document.getElementById('snap-modal').classList.add('show');
}
function closeSnapModal() {
  document.getElementById('snap-modal').classList.remove('show');
}

async function saveSnap() {
  const payload = {
    session_pct: parseFloat(document.getElementById('in-session-pct').value) || null,
    weekly_pct: parseFloat(document.getElementById('in-weekly-pct').value) || null,
    session_reset_seconds: parseDurationToSeconds(document.getElementById('in-session-reset').value),
    weekly_reset_seconds: parseDurationToSeconds(document.getElementById('in-weekly-reset').value),
  };
  try {
    const r = await fetch('/api/snapshot', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    const snap = await r.json();
    renderSnap(snap);
    closeSnapModal();
  } catch (e) {
    alert('Save failed: ' + e);
  }
}

// Bookmarklet — scrapes claude.ai/settings/usage, opens a localhost URL
// (window.open avoids CORS + mixed-content issues that block fetch).
(function() {
  const origin = window.location.origin;
  const code = "javascript:(function(){"
    + "var t=(document.body.innerText||'').replace(/\\u00A0/g,' ');"
    + "function pct(label){"
    +   "var re=new RegExp(label+'[^]*?(\\\\d+(?:\\\\.\\\\d+)?)\\\\s*%','i');"
    +   "var m=t.match(re); return m?parseFloat(m[1]):null;"
    + "}"
    + "function reset(label){"
    +   "var re=new RegExp(label+'[^]*?Resets?\\\\s+in\\\\s*(?:(\\\\d+)\\\\s*hr)?\\\\s*(?:(\\\\d+)\\\\s*min)?','i');"
    +   "var m=t.match(re); if(!m) return null;"
    +   "return (parseInt(m[1]||0))*3600+(parseInt(m[2]||0))*60;"
    + "}"
    + "var sp=pct('Current session')||pct('Session');"
    + "var wp=pct('All models')||pct('Weekly');"
    + "var sr=reset('Current session')||reset('Session');"
    + "if(sp==null||wp==null){"
    +   "var snip=t.substring(0,600).replace(/\\s+/g,' ');"
    +   "alert('ccburn: cant find Session/Weekly %.\\n\\nURL: '+location.href+'\\n\\nIf youre not on https://claude.ai/settings/usage, go there first.\\n\\nPage text starts: '+snip);"
    +   "return;"
    + "}"
    + "var qs='session_pct='+sp+'&weekly_pct='+wp+(sr?'&session_reset_seconds='+sr:'');"
    + "window.open('" + origin + "/api/snapshot?'+qs,'ccburn');"
    + "})();";
  document.getElementById('bookmarklet').setAttribute('href', code);
  document.getElementById('bookmarklet').textContent = 'ccburn snapshot';
})();
</script>
</body>
</html>
"""


def main():
    cfg = load_config()
    ap = argparse.ArgumentParser(description="Browser-based usage dashboard for Claude Code")
    ap.add_argument("--claude-dir", default=os.environ.get(
        "CLAUDE_DIR", str(Path.home() / ".claude")))
    ap.add_argument("--plan", choices=PLAN_LIMITS.keys(),
                    default=os.environ.get("CCBURN_PLAN", cfg.get("plan", "max5")))
    ap.add_argument("--session-limit", type=int, default=None)
    ap.add_argument("--weekly-limit", type=int, default=None)
    ap.add_argument("--week-reset-day", type=int,
                    default=cfg.get("week_reset_day", 0),
                    help="0=Mon … 6=Sun (default 0)")
    ap.add_argument("--week-reset-hour", type=int,
                    default=cfg.get("week_reset_hour", 0),
                    help="0-23, local time (default 0)")
    ap.add_argument("--calibrate-session", type=float, metavar="PCT")
    ap.add_argument("--calibrate-weekly", type=float, metavar="PCT")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--auto-scrape", action="store_true",
                    help="Start ccburn_scrape.py in the background")
    ap.add_argument("--scrape-interval", type=int, default=600)
    args = ap.parse_args()

    if args.calibrate_session is not None or args.calibrate_weekly is not None:
        new_cfg = calibrate(
            Path(args.claude_dir).expanduser(), args.plan,
            args.calibrate_session, args.calibrate_weekly,
            args.week_reset_day, args.week_reset_hour,
        )
        print("Calibrated and saved to ~/.ccburn.json:")
        for k, v in new_cfg.items():
            print(f"  {k}: {v}")
        return

    default_s, default_w = PLAN_LIMITS[args.plan]
    if args.session_limit is None:
        args.session_limit = int(os.environ.get("CCBURN_SESSION_LIMIT",
                                                cfg.get("session_limit", default_s)))
    if args.weekly_limit is None:
        args.weekly_limit = int(os.environ.get("CCBURN_WEEKLY_LIMIT",
                                               cfg.get("weekly_limit", default_w)))

    app = make_app(args)
    url = f"http://{args.host}:{args.port}"
    print(f"\n  ccburn web dashboard at {url}")
    print(f"  Plan: {args.plan}   Press Ctrl+C to stop.\n")
    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    scrape_proc = None
    if args.auto_scrape:
        import subprocess
        script = Path(__file__).with_name("ccburn_scrape.py")
        try:
            scrape_proc = subprocess.Popen(
                [sys.executable, str(script), "--interval", str(args.scrape_interval)],
                cwd=str(script.parent),
            )
            print(f"  Auto-scraper running (pid {scrape_proc.pid}, every {args.scrape_interval}s)\n")
        except Exception as e:
            print(f"  Auto-scraper failed to start: {e}\n", file=sys.stderr)

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    try:
        app.run(host=args.host, port=args.port, debug=False, use_reloader=False)
    finally:
        if scrape_proc and scrape_proc.poll() is None:
            scrape_proc.terminate()


if __name__ == "__main__":
    main()
