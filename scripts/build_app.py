"""Build the page — one HTML file that prices the season live in the browser.

Two builds from the same source:

The params and the tape are baked in, so it opens instantly and works off a disk
with no network. Served next to its data files it refetches them on load, so
every scheduled run reaches the phone; either way it then polls ESPN directly
(two calls: the league's matchup view and the pro schedule it caches) and
reprices 15,000 seasons in the browser between runs.

The page is open — no gate. Anyone with the link sees the league.

Usage: python scripts/build_app.py [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PARAMS = REPO / "data" / "params.json"
TAPE = REPO / "data" / "ev_history.json"
THEME = REPO / "web" / "theme.css"
OUT = REPO / "_site" / "index.html"
SIMS = 15000


def load_tape(season: int) -> dict:
    empty = {"season": season, "at": [], "week": [], "recon": [], "series": {}}
    return json.loads(TAPE.read_text()) if TAPE.exists() else empty


def head(title: str, extra_css: str = "") -> str:
    """A complete head + opening body: the theme inlined (the page must be one
    file), then this page's own layout on top, tokens only."""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{title}</title>
<style>
{THEME.read_text()}

/* ---- page layout (token-based only; no raw hex) ---- */
{extra_css}
</style></head>
<body class="pt">"""

CSS = """
body{margin:0;background:var(--bg);}
.pt-root{min-height:100vh;}
.wrap{max-width:760px;margin:0 auto;padding:0 10px 56px;}
.topbar{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:10px;
  background:var(--surface-2);border-bottom:2px solid var(--grid-strong);
  margin:0 -10px 10px;padding:9px 12px;}
.brand{font-size:var(--fs-h2);letter-spacing:.14em;text-transform:uppercase;
  color:var(--text-secondary);font-weight:700;}
.brand b{color:var(--amber);}
.state{margin-left:auto;text-align:right;font-size:var(--fs-tiny);color:var(--text-muted);
  line-height:1.3;}
.state .now{color:var(--text-secondary);}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--grid-strong);
  margin-right:5px;vertical-align:middle;}
.dot.on{background:var(--down);animation:pulse 1.6s infinite;}
.dot.warn{background:var(--warning);}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
button{background:var(--surface-3);border:1px solid var(--grid-strong);color:var(--text-secondary);
  border-radius:var(--radius);padding:6px 10px;font-family:var(--font-mono);font-size:var(--fs-small);
  cursor:pointer;}
button:hover{border-color:var(--amber);color:var(--amber);}
button.busy{color:var(--amber);border-color:var(--amber);}
button.busy span{display:inline-block;animation:spin .8s linear infinite;}
@keyframes spin{to{transform:rotate(360deg);}}
section{margin-bottom:12px;}
.pt-panel>header{display:flex;align-items:baseline;gap:8px;}
.pt-panel>header .sub{margin-left:auto;font-size:var(--fs-tiny);color:var(--text-muted);
  letter-spacing:0;text-transform:none;}

/* --- hero --- */
.hero{padding:14px 14px 12px;}
.hero .who{font-size:var(--fs-tiny);letter-spacing:.16em;text-transform:uppercase;
  color:var(--text-muted);display:flex;align-items:center;gap:8px;}
.hero .who .nm{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.hero .money{font-size:var(--fs-mega);line-height:1;color:var(--amber);
  font-variant-numeric:tabular-nums;margin:2px 0 1px;}
.hero .money.neg{color:var(--down);}
.hero .swing{font-size:var(--fs-small);color:var(--text-secondary);}
.hero .pick{flex:0 0 auto;font-size:var(--fs-tiny);color:var(--text-muted);cursor:pointer;
  border:1px solid var(--grid-strong);border-radius:var(--radius);padding:2px 7px;
  letter-spacing:.06em;}
.hero .pick:hover{color:var(--amber);border-color:var(--amber);}
.stakes{margin-top:11px;border-top:1px solid var(--grid);padding-top:9px;}
.stakes .lab{font-size:var(--fs-tiny);color:var(--text-muted);text-transform:uppercase;
  letter-spacing:.08em;}
.stakes .two{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:5px;}
.stakes .side{background:var(--surface-2);border:var(--border);border-radius:var(--radius);
  padding:7px 9px;}
.stakes .side .k{font-size:var(--fs-tiny);color:var(--text-muted);text-transform:uppercase;}
.stakes .side .v{font-size:var(--fs-h2);font-variant-numeric:tabular-nums;}
.stakes .side .v.pos{color:var(--up);} .stakes .side .v.neg{color:var(--down);}
/* A win can still leave you under water and a loss can leave you well above it,
   so the sign of the number decides the colour. Exactly $0 stays neutral. */
.stakes .side small{display:block;color:var(--text-muted);font-size:var(--fs-tiny);}
.mu .swingline{grid-column:1/-1;text-align:center;font-size:var(--fs-tiny);color:var(--text-muted);
  padding-top:2px;}
.mu .book{grid-column:1/-1;display:flex;justify-content:center;gap:10px;flex-wrap:wrap;
  padding-top:3px;font-size:var(--fs-tiny);color:var(--text-muted);
  font-variant-numeric:tabular-nums;}
.mu .book b{color:var(--text-secondary);font-weight:400;}
.mu .book .ml{color:var(--text-primary);}
.book-note{padding:0 12px 10px;font-size:9px;color:var(--text-muted);text-align:center;}
.mu .swingline b{color:var(--amber);}
/* --- the crawl: every team as a quote, bottom of the screen --- */
.ticker{position:fixed;left:0;right:0;bottom:0;height:34px;z-index:30;overflow:hidden;
  display:flex;align-items:center;background:var(--surface-2);
  border-top:2px solid var(--amber-dim);box-shadow:0 -6px 18px rgba(0,0,0,.45);}
.ticker .tag{flex:0 0 auto;align-self:stretch;display:flex;align-items:center;padding:0 10px;
  background:var(--amber);color:var(--amber-ink);font-size:var(--fs-tiny);font-weight:700;
  letter-spacing:.12em;text-transform:uppercase;z-index:2;}
.ticker .win{flex:1;overflow:hidden;}
.ticker .track{display:inline-flex;white-space:nowrap;will-change:transform;
  animation:crawl 80s linear infinite;}
.ticker .track:hover,.ticker.paused .track{animation-play-state:paused;}
.ticker .q{padding:0 15px;font-size:var(--fs-small);color:var(--text-muted);
  font-variant-numeric:tabular-nums;}
.ticker .q b{color:var(--text-primary);letter-spacing:.05em;}
.ticker .q .v{color:var(--text-secondary);}
.ticker .q .up{color:var(--up);} .ticker .q .dn{color:var(--down);}
.ticker .q.note b{color:var(--amber);}
@keyframes crawl{from{transform:translateX(0);}to{transform:translateX(-50%);}}
@media (prefers-reduced-motion: reduce){.ticker .track{animation:none;}}
.movers{padding:8px 12px 12px;font-size:var(--fs-small);}
.movers .m{display:flex;gap:8px;padding:3px 0;align-items:baseline;}
.movers .m .tm{width:58px;color:var(--text-secondary);}
.movers .m .d{font-variant-numeric:tabular-nums;font-weight:700;width:64px;}
.movers .m .why{color:var(--text-muted);font-size:var(--fs-tiny);}
.chips{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-top:11px;}
.chip{background:var(--surface-2);border:var(--border);border-radius:var(--radius);padding:7px 6px;
  text-align:center;}
.chip .k{font-size:var(--fs-tiny);color:var(--text-muted);text-transform:uppercase;
  letter-spacing:.06em;display:block;}
.chip .v{font-size:var(--fs-h2);color:var(--text-primary);font-variant-numeric:tabular-nums;}
.chip.bad .v{color:var(--down);}
.chip.good .v{color:var(--up);}

/* --- money table --- */
.pt-table td,.pt-table th{padding:5px 6px;}
.pt-table tr.us td{background:color-mix(in srgb,var(--amber) 11%,transparent);}
.pt-table tr.us td:first-child{box-shadow:inset 3px 0 0 var(--amber);}
.pt-table tr.row{cursor:pointer;}
.tm{font-weight:700;color:var(--text-primary);}
.hurt{color:var(--down);font-size:var(--fs-tiny);}
.own{color:var(--text-muted);font-size:var(--fs-tiny);display:block;}
.ev{font-variant-numeric:tabular-nums;font-weight:700;}
.ev.pos{color:var(--up);} .ev.neg{color:var(--down);}
.d24{font-size:var(--fs-tiny);color:var(--text-muted);text-transform:uppercase;
     letter-spacing:.04em;margin:2px 0 5px;}
.d24 b{font-variant-numeric:tabular-nums;color:var(--text-primary);letter-spacing:0;}
.d24 b.pos{color:var(--up);} .d24 b.neg{color:var(--down);}
.rng{display:flex;gap:4px;margin:0 0 4px;}
.rb{background:var(--surface-2);border:var(--border);border-radius:var(--radius);
    color:var(--text-muted);font:inherit;font-size:var(--fs-tiny);letter-spacing:.04em;
    padding:2px 8px;cursor:pointer;}
.rb.on{color:var(--amber);border-color:var(--amber);}
.rb.off{opacity:.35;cursor:default;}
.detail td{background:var(--surface-2);white-space:normal;}
.dist{display:flex;align-items:flex-end;gap:2px;height:44px;margin:6px 0 4px;}
.dist i{flex:1;background:var(--grid-strong);border-radius:1px 1px 0 0;min-height:1px;
  position:relative;}
.dist i.p1{background:var(--up);} .dist i.p2{background:var(--series-1);}
.dist i.p3{background:var(--series-3);} .dist i.p12{background:var(--down);}
.dist-ax{display:flex;gap:2px;font-size:var(--fs-tiny);color:var(--text-muted);}
.dist-ax span{flex:1;text-align:center;}
.facts{display:grid;grid-template-columns:repeat(2,1fr);gap:3px 12px;font-size:var(--fs-tiny);
  color:var(--text-secondary);margin-top:6px;}
.facts b{color:var(--text-primary);font-variant-numeric:tabular-nums;}

/* --- shame + week --- */
.bars{padding:8px 12px 12px;}
.bar{display:flex;align-items:center;gap:8px;margin:5px 0;font-size:var(--fs-small);}
.bar .lab{width:58px;color:var(--text-secondary);}
.bar .track{flex:1;height:13px;background:var(--surface-3);border-radius:var(--radius);overflow:hidden;}
.bar .fill{display:block;height:100%;background:var(--down);min-width:2px;}
.bar .pct{width:46px;text-align:right;font-variant-numeric:tabular-nums;color:var(--text-primary);}
.mu{padding:4px 12px 12px;}
.mu .m{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:8px;
  padding:7px 0;border-bottom:1px solid var(--grid);font-size:var(--fs-small);}
.mu .m:last-child{border-bottom:none;}
.mu .side{display:flex;flex-direction:column;}
.mu .side.r{text-align:right;}
.mu .side small{color:var(--text-muted);font-size:var(--fs-tiny);}
.mu .win{color:var(--up);} .mu .lose{color:var(--text-muted);}
.mu .odds{font-variant-numeric:tabular-nums;color:var(--text-secondary);font-size:var(--fs-tiny);
  text-align:center;min-width:74px;}
details.pt-panel{padding:0;}
details summary{padding:7px 12px;cursor:pointer;color:var(--amber);font-size:var(--fs-tiny);
  letter-spacing:.1em;text-transform:uppercase;list-style:none;}
details summary::-webkit-details-marker{display:none;}
details summary::after{content:" +";}
details[open] summary::after{content:" \\2212";}
.note{padding:0 12px 12px;font-size:var(--fs-small);color:var(--text-secondary);line-height:1.5;}
.note b{color:var(--text-primary);} .note code{color:var(--amber);font-size:var(--fs-tiny);}
.note ul{margin:6px 0;padding-left:18px;} .note li{margin:3px 0;}
/* --- the tape --- */
.spark{display:block;width:62px;height:20px;overflow:visible;}
.spark .ln{fill:none;stroke:var(--text-secondary);stroke-width:1.5;}
.spark .ln.up{stroke:var(--up);} .spark .ln.dn{stroke:var(--down);}
.spark .zero{stroke:var(--grid-strong);stroke-width:1;stroke-dasharray:2 2;}
.spark .dot{fill:var(--text-primary);}
.chart{width:100%;height:auto;max-height:150px;overflow:visible;margin:4px 0 2px;}
.chart .ln{fill:none;stroke:var(--amber);stroke-width:2;}
.chart .ln.recon{stroke-dasharray:3 3;opacity:.55;}
.chart .zero{stroke:var(--grid-strong);stroke-width:1;stroke-dasharray:3 3;}
.chart .gl{stroke:var(--grid);stroke-width:1;}
.chart text{fill:var(--text-muted);font-size:9px;font-family:var(--font-mono);}
.chart .area{fill:var(--amber);opacity:.10;}
.inj{margin-top:7px;font-size:var(--fs-tiny);color:var(--text-secondary);}
.inj b{color:var(--down);} .inj .row{display:flex;gap:6px;padding:1px 0;}
.inj .row span:last-child{margin-left:auto;font-variant-numeric:tabular-nums;color:var(--text-muted);}
.lineup{margin-top:7px;font-size:var(--fs-tiny);color:var(--text-muted);
  display:grid;grid-template-columns:repeat(3,1fr);gap:1px 8px;}
.lineup b{color:var(--text-secondary);font-weight:400;}
@media(max-width:430px){
  .topbar{margin-left:-7px;margin-right:-7px;}   /* must match the wrap's padding */
  .lineup{grid-template-columns:repeat(2,1fr);}
  .spark{width:48px;}
  .wrap{padding:0 7px 48px;}
  .hide-s{display:none;}
  .chips{grid-template-columns:repeat(2,1fr);}
}
"""

BODY = """
<div class="wrap">
 <div class="topbar">
  <div class="brand">PIKE <b>EV</b></div>
  <div class="state"><span class="now"><span class="dot" id="dot"></span><span id="phase">loading</span></span><br><span id="stamp"></span></div>
  <button id="refresh" title="refresh now"><span>↻</span></button>
 </div>

 <section class="pt-panel hero" id="hero"></section>

 <section class="pt-panel">
  <header>The money<span class="sub" id="moneysub"></span></header>
  <table class="pt-table"><thead><tr>
    <th>Team</th><th class="pt-num">EV</th><th>Tape</th><th class="pt-num">Champ</th>
    <th class="pt-num hide-s">Money</th><th class="pt-num hide-s">Playoff</th><th class="pt-num">Plaque</th>
  </tr></thead><tbody id="money"></tbody></table>
 </section>

 <section class="pt-panel" id="moverspanel" hidden>
  <header>Movers<span class="sub" id="moversub">last 24 hours</span></header>
  <div class="movers" id="movers"></div>
 </section>

 <section class="pt-panel">
  <header>Shame plaque<span class="sub">worst regular-season record</span></header>
  <div class="bars" id="shame"></div>
 </section>

 <section class="pt-panel">
  <header>Week <span id="wknum"></span><span class="sub" id="wksub"></span></header>
  <div class="mu" id="week"></div>
  <div class="book-note" id="booknote"></div>
 </section>

 <div class="ticker" id="ticker" hidden>
  <span class="tag">Pike EV</span>
  <span class="win"><span class="track" id="tickertrack"></span></span>
 </div>

 <details class="pt-panel"><summary>The model</summary><div class="note" id="model"></div></details>
 <details class="pt-panel"><summary>The money rules</summary><div class="note">
   <p>Twelve owners, one <b>$SHARE</b> share each. The pot pays <b>8 / 3 / 1</b> shares to
   1st / 2nd / 3rd &mdash; and since you never win your own share back, what actually lands in
   your pocket is <b>7 / 2 / 0</b>: <b>+$P1</b>, <b>+$P2</b>, <b>break even</b>, and
   <b>&minus;$ENTRY</b> for everyone else. Third place is a push. The EV column is
   just that vector weighted by the odds, so it sums to zero across the league.</p>
   <p>The playoffs decide the money only. The <b>Shame Plaque</b> is the worst
   <b>regular-season</b> record (points-for breaks the tie) &mdash; ESPN's consolation
   ladder is ignored, the way the league ignores it.</p>
 </div></details>
</div>
"""

SCRIPT = r"""
var P0 = __PARAMS__, H0 = __HISTORY__, SIMS = __SIMS__, SEED = 20260101;
var SEASON = P0.season;
var params = JSON.parse(JSON.stringify(P0));
var hist = JSON.parse(JSON.stringify(H0));
var baked = {}; P0.weeks.forEach(function(w){ baked[w.week] = w.state; });
var result = null, baseline = null, lastOk = P0.generated_at, feedOk = null, busy = false;
var dataStamp = P0.generated_at;

/* A published build starts with no data at all, so every clock on the page has
   to survive having nothing to show. */
var fmtWhen = function(v){
  if(!v) return null;
  var d = new Date(v);
  return isNaN(d.getTime()) ? null
    : d.toLocaleString([], {month:"short", day:"numeric", hour:"numeric", minute:"2-digit"});
};
var fmtUsd = function(v){ var s = v < 0 ? "−" : "+"; return s + "$" + Math.abs(Math.round(v)).toLocaleString(); };
var pct = function(v, d){ return (100*v).toFixed(d === undefined ? 1 : d) + "%"; };
var el = function(id){ return document.getElementById(id); };
var esc = function(t){ return String(t).replace(/[&<>"]/g, function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c]; }); };

/* ---------- data: the bake next to us (Actions refreshes it), then live scores ---------- */
function json(url){
  return fetch(url, {cache: "no-store"}).then(function(r){
    if(!r.ok) throw new Error(url + " " + r.status);
    return r.json();
  });
}

/* Served from GitHub Pages the model data sits beside the page and is rewritten
   by the scheduled job; opened from disk those fetches fail and the bake stands. */
/* Served beside its data files (which the scheduled run rewrites), the page
   picks up fresh numbers without being rebuilt. Opened from a disk those fetches
   fail and the bake stands. */
function pullBake(){
  if(location.protocol === "file:") return Promise.resolve();
  return Promise.all([json("ev_season.json").catch(function(){ return null; }),
                      json("ev_history.json").catch(function(){ return null; })])
    .then(function(res){
      if(res[0] && res[0].teams && (!dataStamp || res[0].generated_at >= dataStamp)){
        params = res[0]; dataStamp = params.generated_at;
        baked = {}; params.weeks.forEach(function(w){ baked[w.week] = w.state; });
      }
      if(res[1] && res[1].at) hist = res[1];
    });
}

var PRO_TEAMS = null;      /* the pro schedule barely changes: fetch once */

function espn(view, extra){
  return json(params.read_base + "/seasons/" + params.season + "/segments/0/leagues/" +
              params.league_id + "?view=" + view + (extra || ""));
}

function proTeams(){
  if(PRO_TEAMS) return Promise.resolve(PRO_TEAMS);
  return json(params.read_base + "/seasons/" + params.season + "?view=proTeamSchedules_wl")
    .then(function(p){ PRO_TEAMS = p; return p; });
}

/* Between scheduled runs, keep the week in progress honest. One matchup call
   carries every week's result AND the live fields for the current period, so
   this is two requests, and only one of them repeats. */
function pullLive(){
  if(!params.league_id || !params.teams.length) return Promise.resolve();
  return Promise.all([espn("mMatchupScore", "&scoringPeriodId=" + params.current_week),
                      proTeams()])
    .then(function(res){
      var sched = res[0], pro = res[1];
      params.weeks = EVSim.buildWeeks(sched, params.reg_season_weeks, baked);
      EVSim.overlayLive(params, params.weeks, sched, pro, Date.now());
      params.weeks.forEach(function(w){ baked[w.week] = w.state; });
      params.current_week = EVSim.currentWeek(params.weeks);
    });
}

function refresh(){
  if(busy) return Promise.resolve();
  busy = true; paint();
  return pullBake()
    .then(pullLive)
    .then(function(){ lastOk = new Date().toISOString(); feedOk = true; })
    .catch(function(e){ feedOk = false; console.warn("feed", e); })
    .then(function(){ busy = false; run(); });
}

/* ---------- run ---------- */
function run(){
  result = EVSim.simulate(params, SIMS, SEED, false);
  var liveWeek = params.weeks.filter(function(w){ return w.state === "live"; })[0];
  baseline = liveWeek ? EVSim.simulate(params, SIMS, SEED, true) : null;
  paint();
}

function byId(res, id){ return res.teams.filter(function(t){ return t.team_id === id; })[0]; }
function teamParams(id){ return params.teams.filter(function(t){ return t.team_id === id; })[0]; }

/* ---------- the tape ---------- */
function series(tid, name){
  var col = (hist.series || {})[name || "ev"] || {};
  return (col[String(tid)] || []).map(Number);
}

function sinceWeekStart(tid){
  /* what this week has done to the number */
  var vals = series(tid, "ev"), weeks = hist.week || [];
  if(vals.length < 2) return null;
  var wk = params.current_week, base = null;
  for(var i = 0; i < vals.length; i++) if(weeks[i] < wk) base = vals[i];
  return base === null ? null : vals[vals.length-1] - base;
}

/* Change across a window, measured end to end over the SAME points the chart
   draws -- so the number and the line always agree about what they describe.
   `short` means the tape does not reach back as far as the range asked for, in
   which case we name the span we actually have instead of overclaiming: a "last
   24 hours" that really covers 12 is a lie the reader cannot see. */
function changeOver(tid, secs){
  var w = windowed(tid, secs);
  if(w.vals.length < 2) return null;
  var span = w.at[w.at.length - 1] - w.at[0];
  return {d: w.vals[w.vals.length - 1] - w.vals[0],
          hours: Math.max(1, Math.round(span / 3600)),
          /* not a faithful window: either the tape is too young to fill it, or we
             anchored past its edge to have two points. Say the span we really cover. */
          short: secs !== null && (!w.exact || span < secs * 0.75),
          fromRecon: !!w.recon[0]};
}

function path(vals, w, h, pad){
  var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
  if(hi === lo){ lo -= 1; hi += 1; }
  var n = vals.length;
  return {
    pts: vals.map(function(v, i){
      return [n === 1 ? w/2 : (i/(n-1))*w, h - pad - ((v-lo)/(hi-lo))*(h-2*pad)];
    }),
    y: function(v){ return h - pad - ((v-lo)/(hi-lo))*(h-2*pad); },
    lo: lo, hi: hi
  };
}

function sparkline(tid){
  var vals = series(tid, "ev");
  if(vals.length < 2) return '<svg class="spark" viewBox="0 0 62 20"></svg>';
  var p = path(vals, 62, 20, 2);
  var d = p.pts.map(function(pt, i){ return (i ? "L" : "M") + pt[0].toFixed(1) + " " + pt[1].toFixed(1); }).join(" ");
  var rising = vals[vals.length-1] >= vals[0];
  var zero = (p.lo <= 0 && p.hi >= 0) ?
    '<line class="zero" x1="0" y1="' + p.y(0).toFixed(1) + '" x2="62" y2="' + p.y(0).toFixed(1) + '"/>' : "";
  var last = p.pts[p.pts.length-1];
  return '<svg class="spark" viewBox="0 0 62 20">' + zero +
    '<path class="ln ' + (rising ? "up" : "dn") + '" d="' + d + '"/>' +
    '<circle class="dot" cx="' + last[0].toFixed(1) + '" cy="' + last[1].toFixed(1) + '" r="1.6"/></svg>';
}

/* Stock-tape ranges. One selection shared by every open panel -- simpler than
   per-team state, and switching re-renders the chart in place rather than
   repainting, so the panel you are reading stays open. The tape is thinned after
   14 days (ev_history.KEEP_DENSE_DAYS), so the short ranges are dense while ALL
   gets sparser the further back you look.

   The shortest range is 6H, not 1H. A point is only written when the number moves
   $2 or the 6h heartbeat fires, and off-peak the job runs every 3 hours -- so an
   hour of tape holds one point six days a week and 1H sat permanently greyed.
   6H matches the heartbeat, which is the shortest window the tape can fill. */
var RANGES = [["6H", 21600], ["1D", 86400], ["1W", 604800], ["ALL", null]];
var chartRange = "ALL";

/* Which panels the reader has open, by team id. paint() rebuilds the whole table,
   and it runs every 60s while a week is live -- without this an expanded panel
   slams shut mid-read on Sunday, and the first tap on a row (which also claims
   your team, repainting) never appeared to expand at all. */
var openRows = {};

function windowed(tid, secs){
  var vals = series(tid, "ev"), at = hist.at || [], recon = hist.recon || [];
  if(at.length !== vals.length) return {vals: vals, at: at, recon: recon, exact: true};
  if(secs === null) return {vals: vals, at: at, recon: recon, exact: true};
  var cut = at[at.length - 1] - secs, first = -1;
  for(var i = 0; i < vals.length; i++){ if(at[i] >= cut){ first = i; break; } }
  if(first < 0) first = vals.length - 1;
  /* A window holding one point cannot draw a line, and with a 6h heartbeat that
     happens whenever the previous point sits just outside. Reach back one further
     so the line enters from the left edge, and flag it: the span is then wider
     than the range asked for, which the label has to say rather than round away. */
  var exact = true;
  if(vals.length - first < 2 && first > 0){ first -= 1; exact = false; }
  return {vals: vals.slice(first), at: at.slice(first),
          recon: recon.slice(first), exact: exact};
}

/* One place builds the panel's chart block, so the click redraw and the first
   render cannot drift apart. */
function chartBody(tid){
  return rangeButtons(tid) + rangeCallout(tid) + chart(tid);
}

function rangeButtons(tid){
  return '<div class="rng">' + RANGES.map(function(pair){
    var n = windowed(tid, pair[1]).vals.length;
    var off = n < 2;
    return '<button type="button" class="rb' + (pair[0] === chartRange ? " on" : "") +
      (off ? " off" : "") + '" data-range="' + pair[0] + '"' + (off ? " disabled" : "") +
      ' title="' + (off ? "not enough tape yet" : n + " points") + '">' + pair[0] + "</button>";
  }).join("") + "</div>";
}

function chart(tid){
  var secs = null;
  RANGES.forEach(function(pr){ if(pr[0] === chartRange) secs = pr[1]; });
  var w = windowed(tid, secs);
  var vals = w.vals, recon = w.recon, at = w.at;
  if(vals.length < 2) return '<div class="pt-mono-label">' + (secs === null ?
    "The tape starts filling as the season runs." :
    "Not enough tape in this window yet.") + "</div>";
  var W = 300, H = 120, p = path(vals, W, H, 12);
  var firstLive = recon.indexOf(0); if(firstLive < 0) firstLive = vals.length - 1;
  var seg = function(from, to, cls){
    if(to <= from) return "";
    var d = p.pts.slice(from, to+1).map(function(pt, i){
      return (i ? "L" : "M") + pt[0].toFixed(1) + " " + pt[1].toFixed(1); }).join(" ");
    return '<path class="ln ' + cls + '" d="' + d + '"/>';
  };
  var zeroY = (p.lo <= 0 && p.hi >= 0) ? p.y(0) : null;
  /* Inside a day every point carries the same date, so the axis has to switch
     to a clock or both ends read identically. */
  var sameDay = (at[at.length-1] - at[0]) <= 86400;
  var when = function(i){
    var d = new Date(at[i]*1000);
    return sameDay ? d.toLocaleTimeString([], {hour:"numeric", minute:"2-digit"})
                   : d.toLocaleDateString([], {month:"short", day:"numeric"});
  };
  return '<svg class="chart" viewBox="0 0 ' + W + ' ' + H + '">' +
    (zeroY !== null ? '<line class="zero" x1="0" y1="' + zeroY.toFixed(1) + '" x2="' + W + '" y2="' + zeroY.toFixed(1) + '"/>' : "") +
    seg(0, firstLive, "recon") + seg(firstLive, vals.length-1, "") +
    '<text x="0" y="9">' + fmtUsd(p.hi) + '</text>' +
    '<text x="0" y="' + (H-2) + '">' + fmtUsd(p.lo) + '</text>' +
    '<text x="' + W + '" y="' + (H-2) + '" text-anchor="end">' + when(at.length-1) + '</text>' +
    '<text x="60" y="' + (H-2) + '">' + when(0) + '</text></svg>' +
    '<div class="pt-mono-label">Dashed = reconstructed from completed weeks on today\'s projections; solid = recorded live.</div>';
}

/* ---------- render ---------- */
function paint(){
  if(!result) return;
  var teams = result.teams.slice().sort(function(a,b){ return b.ev_usd - a.ev_usd; });
  var live = params.weeks.filter(function(w){ return w.state === "live"; })[0];
  var done = params.weeks.filter(function(w){ return w.state === "final"; }).length;

  el("dot").className = "dot" + (busy ? " warn" : (live ? " on" : ""));
  el("phase").textContent = busy ? "refreshing" :
      (live ? "live · week " + live.week : "week " + params.current_week + " · pre-kick");
  /* a refresh can finish inside a second, so "just now" is the only way a tap
     visibly lands */
  var age = lastOk ? (Date.now() - new Date(lastOk).getTime()) : null;
  var when = (age !== null && age >= 0 && age < 60000)
    ? "just now" : (fmtWhen(lastOk) || fmtWhen(dataStamp));
  el("stamp").textContent = busy ? "checking…"
    : (feedOk === false ? "offline · " : "updated ") + (when || "—");
  el("refresh").className = busy ? "busy" : "";

  paintHero(teams);

  el("moneysub").textContent = done + " of " + params.reg_season_weeks + " weeks played · " +
      (SIMS/1000) + "k seasons";

  el("money").innerHTML = teams.map(function(t){
    var sw = baseline ? t.ev_usd - byId(baseline, t.team_id).ev_usd : sinceWeekStart(t.team_id);
    var q = teamParams(t.team_id) || {};
    var hurt = (q.injuries || []).filter(function(i){ return i.on_ir || i.status === "OUT"; }).length;
    return '<tr class="row' + (String(t.team_id) === MINE ? " us" : "") + '" data-id="' + t.team_id + '">' +
      '<td><span class="tm">' + esc(t.abbrev) + (hurt ? ' <span class="hurt" title="' + hurt +
        ' out">✖</span>' : "") + '</span><span class="own">' + esc(t.owner) + '</span></td>' +
      '<td class="pt-num ev ' + (t.ev_usd >= 0 ? "pos" : "neg") + '">' + fmtUsd(t.ev_usd) +
        (sw !== null && Math.abs(sw) >= 1 ? '<span class="own">' + fmtUsd(sw) + '</span>' : "") + '</td>' +
      '<td>' + sparkline(t.team_id) + '</td>' +
      '<td class="pt-num">' + pct(t.p_champ) + '</td>' +
      '<td class="pt-num hide-s">' + pct(t.p_money) + '</td>' +
      '<td class="pt-num hide-s">' + pct(t.p_playoffs, 0) + '</td>' +
      '<td class="pt-num">' + pct(t.p_shame) + '</td></tr>' +
    '<tr class="detail" data-for="' + t.team_id + '" hidden><td colspan="7">' + detail(t) + '</td></tr>';
  }).join("");

  /* Delegated on the table, which survives the redraw. Binding per button and
     re-binding after would capture the REPLACED node, so the range would read
     stale and freeze after one click. */
  el("money").onclick = function(e){
    var b = e.target.closest ? e.target.closest(".rb") : null;
    if(!b) return;
    e.stopPropagation();                   /* the row's own handler closes the panel */
    if(b.disabled || b.classList.contains("off")) return;
    chartRange = b.dataset.range;
    /* redraw every open panel in place; a full paint() would collapse them all */
    Array.prototype.forEach.call(document.querySelectorAll(".chartbox"), function(box){
      var id = Number(box.dataset.team);
      box.innerHTML = chartBody(id);
    });
  };

  restoreRows();

  Array.prototype.forEach.call(document.querySelectorAll("tr.row"), function(tr){
    tr.onclick = function(){
      var id = tr.dataset.id;
      openRows[id] = !openRows[id];
      var d = document.querySelector('tr.detail[data-for="' + id + '"]');
      if (d) d.hidden = !openRows[id];
      /* first tap also claims the team, which repaints -- restoreRows() reopens it */
      if (!MINE) pickTeam(id);
    };
  });

  var shame = result.teams.slice().sort(function(a,b){ return b.p_shame - a.p_shame; }).slice(0, 6);
  var top = shame[0].p_shame || 1;
  el("shame").innerHTML = shame.map(function(t){
    return '<div class="bar"><span class="lab">' + esc(t.abbrev) + '</span>' +
      '<span class="track"><span class="fill" style="width:' + (100*t.p_shame/top).toFixed(1) + '%"></span></span>' +
      '<span class="pct">' + pct(t.p_shame) + '</span></div>';
  }).join("");

  var wk = live ? live.week : params.current_week;
  el("wknum").textContent = wk;
  el("wksub").textContent = live ? "live" : (baked[wk] === "final" ? "final" : "projected");
  var note = el("booknote");
  if (note) note.textContent = baked[wk] === "final" ? ""
    : "fair odds \u2014 no juice, so the two sides add to 100%";
  var names = {}; params.teams.forEach(function(t){ names[t.team_id] = t.abbrev; });
  el("week").innerHTML = EVSim.weekOdds(params, wk).map(function(m){
    var hw = m.p_home >= 0.5;
    var score = function(side, win){
      var pts = m[side + "_points"], proj = m[side + "_proj"];
      var shown = (m.state === "future") ? proj.toFixed(0) : pts.toFixed(1);
      return '<span class="' + (win ? "win" : "lose") + '">' + esc(names[m[side]]) + " " + shown + "</span>" +
        (m.state === "live" ? "<small>proj " + proj.toFixed(0) + "</small>" : "");
    };
    /* what the game is worth: the bigger of the two sides' win-minus-lose swing */
    var stake = 0;
    result.teams.forEach(function(t){
      if ((t.team_id === m.home || t.team_id === m.away) && t.stakes){
        stake = Math.max(stake, t.stakes.ev_win - t.stakes.ev_lose);
      }
    });
    return '<div class="m"><span class="side">' + score("away", !hw) + '</span>' +
      '<span class="odds">' + (hw ? "" : "◂ ") + pct(Math.max(m.p_home, 1-m.p_home), 0) +
        (hw ? " ▸" : "") + '</span>' +
      '<span class="side r">' + score("home", hw) + '</span>' +
      bookRow(m, names) +
      (stake >= 1 ? '<span class="swingline">worth <b>$' +
        Math.round(stake).toLocaleString() + '</b> to the winner</span>' : "") + '</div>';
  }).join("");

  paintMovers(teams);
  paintTicker(teams);

  var m = params.model || {};
  // how much of any team's estimate is its own results is a league-wide fact —
  // every team has played the same number of weeks — so read it off the first one
  var ref = teams[0] || {games: 0, post_weight: 0};
  el("model").innerHTML =
    "<p>Every owner's number is <b>" + (SIMS/1000) + ",000 simulated seasons</b> played out on the real " +
    "remaining schedule, then priced with the 8/3/1 share vector.</p><ul>" +
    "<li><b>Strength comes from ESPN, live.</b> Each roster runs through a bye-aware lineup " +
      "optimizer on ESPN's own projections — re-cut continuously, so a ruled-out starter " +
      "drops that team the moment ESPN marks him. Players on IR or ruled out are not in the " +
      "lineup; the next man up is, at his projection. Currently <b>" +
      Object.keys(m.injury_counts || {}).map(function(k){
        return m.injury_counts[k] + " " + k.toLowerCase().replace("_", " "); }).join(", ") +
      "</b> across the league.</li>" +
    "<li><b>Results shrink toward the roster</b>, they do not replace it: weekly scores swing about <b>" +
      params.sigma.toFixed(1) + " pts</b>, far more than the roster projection still leaves uncertain " +
      "(about <b>" + params.tau_prior.toFixed(1) + "</b>), so after <b>" + ref.games + " games</b> a " +
      "team's own results carry <b>" + pct(ref.post_weight, 0) + "</b> of its estimate. A hot start is mostly schedule.</li>" +
    "<li><b>During games</b> the week in progress uses ESPN's live projection, with the uncertainty " +
      "scaled by the share of each lineup that has not kicked off yet — real kickoff times, so " +
      "a Sunday-morning slate is all risk and a Monday-night one is nearly settled.</li>" +
    "<li><b>Playoffs:</b> top four by record (points-for breaks ties), two-week semifinals (1v4, 2v3), " +
      "then the final and the third-place game. The <b>Shame Plaque</b> is the worst regular-season " +
      "record — the bracket never touches it.</li>" +
    "<li><b>Calibration:</b> ESPN's week projections and its rest-of-season rate sit on different " +
      "scales (the season number discounts games it expects a player to miss), so rates are lifted " +
      "<code>×" + (m.rate_scale || 1) + "</code> onto the weekly scale; the league level is then " +
      "nudged <code>×" + (m.calibration_scale || 1) + "</code> — " + esc(m.calibrated_to || "") + ".</li>" +
    "<li><b>Soft spots:</b> the form term compares each team's results against <i>today's</i> roster, " +
      "so a rebuilt team reads slightly off; a weekly OUT only removes a player from the current week " +
      "(season-long absences come through IR and through ESPN's own projection). Data as of <b>" +
      (fmtWhen(dataStamp) || "not loaded") +
      "</b>.</li></ul>";
}

/* The card at the top is whoever is looking. Until someone picks a team it
   cycles the whole league, so a first-time visitor sees everybody and a regular
   sees himself. */
var MINE = null, rotateAt = 0;
try { MINE = localStorage.getItem("pike-ev-team"); } catch(e) {}

function pickTeam(id){
  MINE = id === null ? null : String(id);
  try {
    if (MINE === null) localStorage.removeItem("pike-ev-team");
    else localStorage.setItem("pike-ev-team", MINE);
  } catch(e) {}
  paint();
}

function restoreRows(){
  Array.prototype.forEach.call(document.querySelectorAll("tr.detail"), function(d){
    d.hidden = !openRows[d.getAttribute("data-for")];
  });
}

function heroTeam(teams){
  if (MINE) {
    var mine = teams.filter(function(t){ return String(t.team_id) === MINE; })[0];
    if (mine) return mine;
  }
  return teams[rotateAt % teams.length];
}

/* Quiet by design: only when a full day of live tape exists and the number
   actually moved a dollar. Sits next to the week delta, never replaces it. */
function heroDay(tid){
  var x = changeOver(tid, 86400);
  if(!x || x.short || Math.abs(x.d) < 1) return "";
  return ' · <span class="' + (x.d >= 0 ? "pt-up" : "pt-down") + '">' +
         fmtUsd(x.d) + ' 24h</span>';
}

var RANGE_LABEL = {"6H": "last 6 hours", "1D": "last 24 hours",
                   "1W": "last week", "ALL": "season to date"};

/* Reads the selected range, so tapping 1H / 1D / 1W / ALL re-answers the
   question for that window rather than always reporting the day. */
function rangeCallout(tid){
  var secs = null;
  RANGES.forEach(function(pr){ if(pr[0] === chartRange) secs = pr[1]; });
  var x = changeOver(tid, secs);
  if(!x) return '<div class="d24">not enough tape in this window</div>';
  var lab = x.short ? "last " + x.hours + "h of tape" : RANGE_LABEL[chartRange];
  if(Math.abs(x.d) < 1) return '<div class="d24">' + lab + ' <b>unchanged</b></div>';
  return '<div class="d24">' + lab + ' <b class="' + signCls(x.d) + '">' +
         fmtUsd(x.d) + "</b></div>";
}

function paintHero(teams){
  var t = heroTeam(teams);
  var swing = baseline ? t.ev_usd - byId(baseline, t.team_id).ev_usd
                       : sinceWeekStart(t.team_id);
  var swingLabel = baseline ? "today" : "this week";
  var names = {}; params.teams.forEach(function(x){ names[x.team_id] = x.abbrev; });
  var opp = opponentOf(t.team_id);

  el("hero").innerHTML =
    '<div class="who"><span class="nm">' + esc(t.name) + " \u00b7 " + esc(t.owner) + "</span>" +
      '<span class="pick" id="pickbtn">' + (MINE ? "change" : "this is me") + "</span></div>" +
    '<div class="money' + (t.ev_usd < 0 ? " neg" : "") + '">' + fmtUsd(t.ev_usd) + '</div>' +
    '<div class="swing">expected winnings' +
      (swing === null || Math.abs(swing) < 1 ? "" : ' \u00b7 <span class="' +
        (swing >= 0 ? "pt-up" : "pt-down") + '">' + fmtUsd(swing) + " " + swingLabel + "</span>") +
      heroDay(t.team_id) +
      (MINE ? "" : ' \u00b7 <span class="pt-mono-label">tap a row to pin your team</span>') + '</div>' +
    '<div class="chips">' +
      chip("champ", pct(t.p_champ), "good") + chip("in the money", pct(t.p_money), "good") +
      chip("playoffs", pct(t.p_playoffs), "") + chip("plaque", pct(t.p_shame), "bad") +
    '</div>' +
    (t.stakes ? stakesBlock(t, opp ? names[opp] : "") : "");

  var btn = el("pickbtn");
  if (btn) btn.onclick = function(e){
    e.stopPropagation();
    pickTeam(MINE ? null : t.team_id);
  };
}

/* Both boxes hold a conditional EV -- what the season is worth IF this week goes
   that way -- not a delta. So either can be positive: a strong team can lose and
   stay in the money, a weak one can win and still be down a share. */
function signCls(n){ return n > 0 ? " pos" : n < 0 ? " neg" : ""; }

/* What Sunday is actually worth: the same seasons, split by who won this week. */
function stakesBlock(t, oppName){
  var s = t.stakes;
  return '<div class="stakes"><div class="lab">Week ' + s.week +
    (oppName ? " vs " + esc(oppName) : "") + " \u00b7 " + pct(s.p_win, 0) + " to win</div>" +
    '<div class="two">' +
      '<div class="side w"><span class="k">if win</span><span class="v' + signCls(s.ev_win) + '">' +
        fmtUsd(s.ev_win) +
        '</span><small>' + pct(s.playoffs_win, 0) + ' playoffs</small></div>' +
      '<div class="side l"><span class="k">if lose</span><span class="v' + signCls(s.ev_lose) + '">' +
        fmtUsd(s.ev_lose) +
        '</span><small>' + pct(s.playoffs_lose, 0) + ' playoffs</small></div>' +
    "</div></div>";
}

function opponentOf(teamId){
  var wk = params.weeks.filter(function(w){ return w.week === params.current_week; })[0];
  if (!wk) return null;
  var found = null;
  wk.matchups.forEach(function(m){
    if (m.home === teamId) found = m.away;
    if (m.away === teamId) found = m.home;
  });
  return found;
}

/* ---- the book ----------------------------------------------------------
   A probability is a price. These are FAIR odds — no juice, no hold — which is
   why the two sides of a game do not add to more than 100%: nobody is taking a
   rake here. Rounded the way a book rounds, coarser the longer the shot. */
function american(p){
  if(!(p > 0 && p < 1)) return "\u2014";
  var v = p >= 0.5 ? -100 * p / (1 - p) : 100 * (1 - p) / p;
  var step = Math.abs(v) >= 1000 ? 100 : (Math.abs(v) >= 300 ? 25 : 5);
  var r = Math.round(v / step) * step;
  if (r > -100 && r < 100) r = v < 0 ? -100 : 100;   /* no book prices inside even */
  return (r > 0 ? "+" : "") + r.toLocaleString();
}

function line(n){                       /* spreads and totals sit on the half point */
  var h = Math.round(n * 2) / 2;
  return (h > 0 ? "+" : "") + h.toFixed(1);
}

function bookRow(m, names){
  if (m.state === "final") return "";
  var margin = m.home_proj - m.away_proj;            /* + = home favoured */
  var fav = margin >= 0 ? m.home : m.away;
  var spread = line(-Math.abs(margin));
  var total = (Math.round((m.home_proj + m.away_proj) * 2) / 2).toFixed(1);
  return '<span class="book">' +
    "<span><b>" + esc(names[fav]) + "</b> " + spread + "</span>" +
    "<span><b>O/U</b> " + total + "</span>" +
    '<span class="ml">' + esc(names[m.away]) + " " + american(1 - m.p_home) + "</span>" +
    '<span class="ml">' + esc(names[m.home]) + " " + american(m.p_home) + "</span>" +
    "</span>";
}

function chip(k, v, cls){
  return '<div class="chip ' + cls + '"><span class="k">' + k + '</span><span class="v">' + v + "</span></div>";
}

/* Who gained and who bled since the last completed week — the league's tape,
   reduced to the two lines people actually repeat to each other. */
/* Movers keeps its own range, separate from the chart's: you may well want the
   season shape on one team's line while the board shows who moved in the last
   6 hours. Default is the day. */
var moverRange = "1D";

function paintMovers(teams){
  var secs = null;
  RANGES.forEach(function(pr){ if(pr[0] === moverRange) secs = pr[1]; });

  /* Nothing to toggle until the tape can difference at all -- better hidden than
     four dead buttons in week 1. */
  var usable = teams.some(function(t){ return changeOver(t.team_id, null) !== null; });
  if(!usable){ el("moverspanel").hidden = true; return; }

  var rows = [];
  teams.forEach(function(t){
    var x = changeOver(t.team_id, secs);
    if(x && Math.abs(x.d) >= 1) rows.push({t: t, d: x.d, short: x.short, hours: x.hours});
  });
  el("moversub").textContent = (rows.length && rows[0].short)
    ? "last " + rows[0].hours + "h of tape" : RANGE_LABEL[moverRange];

  var btns = '<div class="rng">' + RANGES.map(function(pr){
    return '<button type="button" class="rb mb' + (pr[0] === moverRange ? " on" : "") +
      '" data-mrange="' + pr[0] + '">' + pr[0] + "</button>";
  }).join("") + "</div>";

  el("moverspanel").hidden = false;
  if(!rows.length){
    el("movers").innerHTML = btns +
      '<div class="pt-mono-label">nothing moved a dollar in this window.</div>';
    return;
  }
  rows.sort(function(a, b){ return b.d - a.d; });
  var show = rows.slice(0, 3).concat(rows.slice(-3)).filter(function(r, i, arr){
    return arr.indexOf(r) === i;
  });
  el("moverspanel").hidden = false;
  el("movers").innerHTML = btns + show.map(function(r){
    return '<div class="m"><span class="tm">' + esc(r.t.abbrev) + '</span>' +
      '<span class="d ' + (r.d >= 0 ? "pt-up" : "pt-down") + '">' + fmtUsd(r.d) + "</span>" +
      '<span class="why">' + pct(r.t.p_playoffs, 0) + " playoffs \u00b7 " +
      pct(r.t.p_shame, 0) + " plaque</span></div>";
  }).join("");
}

/* The crawl. Rebuilt only when its text actually changes — re-rendering it
   mid-scroll restarts the animation and looks broken. */
var tickerKey = "";

function paintTicker(teams){
  var quotes = teams.map(function(t){
    var d = sinceWeekStart(t.team_id);
    var move = (d === null || Math.abs(d) < 1) ? ""
      : ' <span class="' + (d >= 0 ? "up" : "dn") + '">' + (d >= 0 ? "\u25b2" : "\u25bc") +
        "$" + Math.abs(Math.round(d)).toLocaleString() + "</span>";
    return '<span class="q"><b>' + esc(t.abbrev) + '</b> <span class="v">' +
      fmtUsd(t.ev_usd) + "</span>" + move + "</span>";
  });

  var plaque = teams.slice().sort(function(a, b){ return b.p_shame - a.p_shame; })[0];
  if (plaque) quotes.push('<span class="q note"><b>PLAQUE WATCH</b> <span class="v">' +
    esc(plaque.abbrev) + " " + pct(plaque.p_shame, 0) + "</span></span>");

  var biggest = null;
  teams.forEach(function(t){
    if (!t.stakes) return;
    var sw = t.stakes.ev_win - t.stakes.ev_lose;
    if (!biggest || sw > biggest.sw) biggest = {t: t, sw: sw};
  });
  if (biggest) quotes.push('<span class="q note"><b>BIGGEST SWING</b> <span class="v">' +
    esc(biggest.t.abbrev) + " $" + Math.round(biggest.sw).toLocaleString() + " on week " +
    biggest.t.stakes.week + "</span></span>");

  var html = quotes.join("");
  if (html === tickerKey) return;             /* nothing new: let it keep rolling */
  tickerKey = html;
  el("tickertrack").innerHTML = html + html;  /* twice, so -50% loops seamlessly */
  el("ticker").hidden = false;
}

function detail(t){
  var q = teamParams(t.team_id) || {};
  var p = t.p_place, max = Math.max.apply(null, p);
  var bars = p.map(function(v, i){
    var cls = i === 0 ? " p1" : i === 1 ? " p2" : i === 2 ? " p3" : (i === p.length-1 ? " p12" : "");
    return '<i class="' + cls.trim() + '" style="height:' + Math.max(1, 100*v/max).toFixed(1) +
      '%" title="' + (i+1) + ": " + pct(v) + '"></i>';
  }).join("");
  var ax = p.map(function(v, i){ return "<span>" + (i+1) + "</span>"; }).join("");

  var inj = (q.injuries || []).length ?
    '<div class="inj"><b>Out / hurt</b>' + q.injuries.map(function(i){
      return '<div class="row"><span>' + esc(i.name) + " <em>" + esc(i.pos) + "</em></span>" +
        '<span>' + esc(i.on_ir ? "IR" : i.status.toLowerCase()) + " · " + i.cost.toFixed(1) + "/wk</span></div>";
    }).join("") + "</div>" : "";

  var lineup = (q.starters || []).length ?
    '<div class="lineup">' + q.starters.map(function(s){
      return "<div><b>" + esc(s.slot) + "</b> " + esc(s.name.split(" ").slice(-1)[0]) + " " + s.proj.toFixed(1) + "</div>";
    }).join("") + "</div>" : "";

  return '<div class="pt-mono-label">EV, over time</div>' +
    '<div class="chartbox" data-team="' + t.team_id + '">' + chartBody(t.team_id) + '</div>' +
    '<div class="pt-mono-label">Finish distribution</div><div class="dist">' + bars + "</div>" +
    '<div class="dist-ax">' + ax + "</div>" +
    '<div class="facts">' +
      "<div>Title <b>" + american(t.p_champ) + "</b></div>" +
      "<div>Playoffs <b>" + american(t.p_playoffs) + "</b></div>" +
      "<div>Plaque <b>" + american(t.p_shame) + "</b></div>" +
      "<div>Win total <b>o/u " + (Math.round(t.exp_wins * 2) / 2).toFixed(1) + "</b></div>" +
      "<div>Projected record <b>" + t.exp_wins.toFixed(1) + " – " +
        (params.reg_season_weeks - t.exp_wins).toFixed(1) + "</b></div>" +
      "<div>Projected points <b>" + Math.round(t.exp_pf) + "</b></div>" +
      "<div>Roster projects <b>" + (q.prior_playoff || 0).toFixed(1) + "/wk</b></div>" +
      "<div>Form vs. projection <b>" + (t.post_shift >= 0 ? "+" : "−") +
        Math.abs(t.post_shift).toFixed(1) + " pts/wk</b></div>" +
    "</div>" + inj +
    '<div class="pt-mono-label" style="margin-top:7px">Full-strength lineup</div>' + lineup;
}

/* ---------- boot ---------- */
/* #movers survives every repaint (only its innerHTML is replaced), so one
   delegated handler here outlives the buttons it serves. */
el("movers").onclick = function(e){
  var b = e.target.closest ? e.target.closest(".mb") : null;
  if(!b || !result) return;
  moverRange = b.dataset.mrange;
  paintMovers(result.teams.slice().sort(function(x, y){ return y.ev_usd - x.ev_usd; }));
};

el("refresh").onclick = function(){ refresh(); };
el("ticker").onclick = function(){ el("ticker").classList.toggle("paused"); };
document.addEventListener("visibilitychange", function(){
  if(!document.hidden && Date.now() - new Date(lastOk).getTime() > 60000) refresh();
});
run();
refresh();
setInterval(function(){          /* cycle the hero until someone claims a team */
  if(MINE || !result) return;
  rotateAt += 1;
  paintHero(result.teams.slice().sort(function(a, b){ return b.ev_usd - a.ev_usd; }));
}, 6000);

setInterval(function(){
  var live = params.weeks.filter(function(w){ return w.state === "live"; })[0];
  if(live || Date.now() - new Date(lastOk).getTime() > 600000) refresh();
}, 60000);
"""


def build() -> str:
    """`locked` builds the published page: no data baked in, only a lock screen
    and the fetchers for the encrypted files beside it."""
    params = json.loads(PARAMS.read_text())
    history = load_tape(params["season"])
    engine = (REPO / "scripts" / "ev_sim.js").read_text()
    share = int(params["share_usd"])
    body = (BODY.replace("$SHARE", f"${share}")
                .replace("$P1", f"{share*7:,}")
                .replace("$P2", f"{share*2:,}")
                .replace("$ENTRY", f"{share:,}"))
    script = (SCRIPT.replace("__PARAMS__", json.dumps(params, separators=(",", ":")))
                    .replace("__HISTORY__", json.dumps(history, separators=(",", ":")))
                    .replace("__SIMS__", str(SIMS)))
    week = params["current_week"]
    title = f"Pike EV — {params['season']} week {week}"
    return (head(title, CSS) + body
            + "<script>" + engine + "</script>"
            + "<script>" + script + "</script>"
            + "</body></html>\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build())
    kb = args.out.stat().st_size / 1024
    print(f"[page] {args.out.name}  ({kb:.0f} KB, {SIMS} sims/run)")

    # The page bakes a copy of both files AND re-fetches them at runtime for
    # anything fresher. Publishing them here keeps a local build self-consistent:
    # otherwise stale siblings left in _site override the freshly baked data and
    # the tape silently reads older than it is.
    for src, name in ((PARAMS, "ev_season.json"), (TAPE, "ev_history.json")):
        if src.exists():
            (args.out.parent / name).write_text(src.read_text())
            print(f"[page] {name}  (from {src.relative_to(REPO)})")
        else:
            print(f"[page] WARNING {name} not written - {src.relative_to(REPO)} missing")


if __name__ == "__main__":
    main()
