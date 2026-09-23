"""Build the page — one HTML file that prices the season live in the browser.

Two builds from the same source:

  --locked (the default, and what gets published) carries NO league data at all:
    a lock screen, the engine, and fetchers for the encrypted files beside it.
    Everything real — teams, rosters, projections, the tape, even the league id —
    arrives as ciphertext and is decrypted with the passphrase. This is the only
    build that may touch a public url.

  --local bakes the plaintext in, for opening off your own disk.

Once unlocked the page polls ESPN directly (two calls: the league's matchup view
and the pro schedule it caches) and reprices 15,000 seasons in the browser, so it
keeps moving between scheduled runs.

Usage: python scripts/build_app.py [--locked|--local] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PARAMS = REPO / "data" / "params.json"
TAPE_PLAIN = REPO / "data" / "ev_history.json"      # only ever exists locally
THEME = REPO / "web" / "theme.css"
OUT = REPO / "_site" / "index.html"
SIMS = 15000


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
.wrap{max-width:760px;margin:0 auto;padding:0 10px 40px;}
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
section{margin-bottom:12px;}
.pt-panel>header{display:flex;align-items:baseline;gap:8px;}
.pt-panel>header .sub{margin-left:auto;font-size:var(--fs-tiny);color:var(--text-muted);
  letter-spacing:0;text-transform:none;}

/* --- hero --- */
.hero{padding:14px 14px 12px;}
.hero .who{font-size:var(--fs-tiny);letter-spacing:.16em;text-transform:uppercase;
  color:var(--text-muted);}
.hero .money{font-size:var(--fs-mega);line-height:1;color:var(--amber);
  font-variant-numeric:tabular-nums;margin:2px 0 1px;}
.hero .money.neg{color:var(--down);}
.hero .swing{font-size:var(--fs-small);color:var(--text-secondary);}
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
.own{color:var(--text-muted);font-size:var(--fs-tiny);display:block;}
.ev{font-variant-numeric:tabular-nums;font-weight:700;}
.ev.pos{color:var(--up);} .ev.neg{color:var(--down);}
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
/* --- the lock (published builds only) --- */
#lock{position:fixed;inset:0;z-index:200;display:flex;align-items:center;justify-content:center;
  background:var(--bg);padding:24px;}
#lock[hidden]{display:none;}   /* an author display: rule beats the hidden attribute */
#lock .box{max-width:340px;width:100%;text-align:center;}
#lock h1{font-size:var(--fs-h1);color:var(--amber);letter-spacing:.14em;text-transform:uppercase;margin:0 0 6px;}
#lock p{color:var(--text-muted);font-size:var(--fs-small);margin:0 0 16px;line-height:1.5;}
#lock input{width:100%;background:var(--surface-2);border:1px solid var(--grid-strong);
  color:var(--text-primary);padding:11px 12px;font-family:var(--font-mono);font-size:16px;
  border-radius:var(--radius);margin-bottom:9px;}
#lock input:focus{outline:1px solid var(--amber);}
#lock button{width:100%;background:var(--amber);color:var(--amber-ink);border-color:var(--amber);
  font-weight:700;padding:11px;}
#lock .err{color:var(--down);font-size:var(--fs-tiny);min-height:1.2em;margin-top:8px;}
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
  .lineup{grid-template-columns:repeat(2,1fr);}
  .spark{width:48px;}
  .wrap{padding:0 7px 30px;}
  .hide-s{display:none;}
  .chips{grid-template-columns:repeat(2,1fr);}
}
"""

LOCK = """
<div id="lock" hidden><div class="box">
  <h1>Pike EV</h1>
  <p>This page is public; the numbers are not. Enter the passphrase to unlock.</p>
  <input id="pass" type="password" autocomplete="current-password" placeholder="passphrase"
         enterkeyhint="go" autofocus>
  <button id="unlock">Unlock</button>
  <div class="err" id="lockerr"></div>
</div></div>
"""

BODY = """
<div class="wrap">
 <div class="topbar">
  <div class="brand">PIKE <b>EV</b></div>
  <div class="state"><span class="now"><span class="dot" id="dot"></span><span id="phase">loading</span></span><br><span id="stamp"></span></div>
  <button id="refresh" title="refresh now">↻</button>
 </div>

 <section class="pt-panel hero" id="hero"></section>

 <section class="pt-panel">
  <header>The money<span class="sub" id="moneysub"></span></header>
  <table class="pt-table"><thead><tr>
    <th>Team</th><th class="pt-num">EV</th><th>Tape</th><th class="pt-num">Champ</th>
    <th class="pt-num hide-s">Money</th><th class="pt-num">Playoff</th><th class="pt-num">Plaque</th>
  </tr></thead><tbody id="money"></tbody></table>
 </section>

 <section class="pt-panel">
  <header>Shame plaque<span class="sub">worst regular-season record</span></header>
  <div class="bars" id="shame"></div>
 </section>

 <section class="pt-panel">
  <header>Week <span id="wknum"></span><span class="sub" id="wksub"></span></header>
  <div class="mu" id="week"></div>
 </section>

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
function pullBake(){
  /* a local build already has everything baked in; a locked one loads via unlock() */
  return Promise.resolve();
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

function chart(tid){
  var vals = series(tid, "ev"), recon = hist.recon || [], at = hist.at || [];
  if(vals.length < 2) return '<div class="pt-mono-label">The tape starts filling as the season runs.</div>';
  var W = 300, H = 120, p = path(vals, W, H, 12);
  var firstLive = recon.indexOf(0); if(firstLive < 0) firstLive = vals.length - 1;
  var seg = function(from, to, cls){
    if(to <= from) return "";
    var d = p.pts.slice(from, to+1).map(function(pt, i){
      return (i ? "L" : "M") + pt[0].toFixed(1) + " " + pt[1].toFixed(1); }).join(" ");
    return '<path class="ln ' + cls + '" d="' + d + '"/>';
  };
  var zeroY = (p.lo <= 0 && p.hi >= 0) ? p.y(0) : null;
  var when = function(i){
    return new Date(at[i]*1000).toLocaleDateString([], {month:"short", day:"numeric"});
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
  var when = new Date(lastOk);
  el("stamp").textContent = (feedOk === false ? "feed down · " : "") +
      when.toLocaleString([], {month:"short", day:"numeric", hour:"numeric", minute:"2-digit"});

  var us = teams.filter(function(t){ return t.is_us; })[0] || teams[0];
  var swing = baseline ? us.ev_usd - byId(baseline, us.team_id).ev_usd : sinceWeekStart(us.team_id);
  var swingLabel = baseline ? "today" : "this week";
  el("hero").innerHTML =
    '<div class="who">' + esc(us.name) + " · " + esc(us.owner) + '</div>' +
    '<div class="money' + (us.ev_usd < 0 ? " neg" : "") + '">' + fmtUsd(us.ev_usd) + '</div>' +
    '<div class="swing">expected winnings' +
      (swing === null || Math.abs(swing) < 1 ? "" : ' · <span class="' + (swing >= 0 ? "pt-up" : "pt-down") +
        '">' + fmtUsd(swing) + " " + swingLabel + "</span>") + '</div>' +
    '<div class="chips">' +
      chip("champ", pct(us.p_champ), "good") + chip("in the money", pct(us.p_money), "good") +
      chip("playoffs", pct(us.p_playoffs), "") + chip("plaque", pct(us.p_shame), "bad") +
    '</div>';

  el("moneysub").textContent = done + " of " + params.reg_season_weeks + " weeks played · " +
      (SIMS/1000) + "k seasons";

  el("money").innerHTML = teams.map(function(t){
    var sw = baseline ? t.ev_usd - byId(baseline, t.team_id).ev_usd : sinceWeekStart(t.team_id);
    var q = teamParams(t.team_id) || {};
    var hurt = (q.injuries || []).filter(function(i){ return i.on_ir || i.status === "OUT"; }).length;
    return '<tr class="row' + (t.is_us ? " us" : "") + '" data-id="' + t.team_id + '">' +
      '<td><span class="tm">' + esc(t.abbrev) + (hurt ? ' <b class="pt-down" title="' + hurt +
        ' out">✖</b>' : "") + '</span><span class="own">' + esc(t.owner) + '</span></td>' +
      '<td class="pt-num ev ' + (t.ev_usd >= 0 ? "pos" : "neg") + '">' + fmtUsd(t.ev_usd) +
        (sw !== null && Math.abs(sw) >= 1 ? '<span class="own">' + fmtUsd(sw) + '</span>' : "") + '</td>' +
      '<td>' + sparkline(t.team_id) + '</td>' +
      '<td class="pt-num">' + pct(t.p_champ) + '</td>' +
      '<td class="pt-num hide-s">' + pct(t.p_money) + '</td>' +
      '<td class="pt-num">' + pct(t.p_playoffs, 0) + '</td>' +
      '<td class="pt-num">' + pct(t.p_shame) + '</td></tr>' +
    '<tr class="detail" data-for="' + t.team_id + '" hidden><td colspan="7">' + detail(t) + '</td></tr>';
  }).join("");

  Array.prototype.forEach.call(document.querySelectorAll("tr.row"), function(tr){
    tr.onclick = function(){
      var d = document.querySelector('tr.detail[data-for="' + tr.dataset.id + '"]');
      d.hidden = !d.hidden;
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
  var names = {}; params.teams.forEach(function(t){ names[t.team_id] = t.abbrev; });
  el("week").innerHTML = EVSim.weekOdds(params, wk).map(function(m){
    var hw = m.p_home >= 0.5;
    var score = function(side, win){
      var pts = m[side + "_points"], proj = m[side + "_proj"];
      var shown = (m.state === "future") ? proj.toFixed(0) : pts.toFixed(1);
      return '<span class="' + (win ? "win" : "lose") + '">' + esc(names[m[side]]) + " " + shown + "</span>" +
        (m.state === "live" ? "<small>proj " + proj.toFixed(0) + "</small>" : "");
    };
    return '<div class="m"><span class="side">' + score("away", !hw) + '</span>' +
      '<span class="odds">' + (hw ? "" : "◂ ") + pct(Math.max(m.p_home, 1-m.p_home), 0) +
        (hw ? " ▸" : "") + '</span>' +
      '<span class="side r">' + score("home", hw) + '</span></div>';
  }).join("");

  var m = params.model || {};
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
      "(about <b>" + params.tau_prior.toFixed(1) + "</b>), so after <b>" + us.games + " games</b> a " +
      "team's own results carry <b>" + pct(us.post_weight, 0) + "</b> of its estimate. A hot start is mostly schedule.</li>" +
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
      new Date(dataStamp).toLocaleString([], {month:"short", day:"numeric", hour:"numeric", minute:"2-digit"}) +
      "</b>.</li></ul>";
}

function chip(k, v, cls){
  return '<div class="chip ' + cls + '"><span class="k">' + k + '</span><span class="v">' + v + "</span></div>";
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

  return '<div class="pt-mono-label">EV, over time</div>' + chart(t.team_id) +
    '<div class="pt-mono-label">Finish distribution</div><div class="dist">' + bars + "</div>" +
    '<div class="dist-ax">' + ax + "</div>" +
    '<div class="facts">' +
      "<div>Projected record <b>" + t.exp_wins.toFixed(1) + " – " +
        (params.reg_season_weeks - t.exp_wins).toFixed(1) + "</b></div>" +
      "<div>Projected points <b>" + Math.round(t.exp_pf) + "</b></div>" +
      "<div>Roster projects <b>" + (q.prior_playoff || 0).toFixed(1) + "/wk</b></div>" +
      "<div>Form vs. projection <b>" + (t.post_shift >= 0 ? "+" : "−") +
        Math.abs(t.post_shift).toFixed(1) + " pts/wk</b></div>" +
    "</div>" + inj +
    '<div class="pt-mono-label" style="margin-top:7px">Full-strength lineup</div>' + lineup;
}

/* ---------- the lock: published builds carry no data, only ciphertext ---------- */
var KEY = null, LOCKED = __LOCKED__, STORE = "pike-ev-pass";

function b64(s){ return Uint8Array.from(atob(s), function(c){ return c.charCodeAt(0); }); }

function deriveKey(pass, salt, iter){
  return crypto.subtle.importKey("raw", new TextEncoder().encode(pass), "PBKDF2",
                                 false, ["deriveKey"])
    .then(function(km){
      return crypto.subtle.deriveKey(
        {name: "PBKDF2", salt: b64(salt), iterations: iter, hash: "SHA-256"},
        km, {name: "AES-GCM", length: 256}, false, ["decrypt"]);
    });
}

function openBlob(blob, pass){
  /* the salt is stable across runs, so the key derives once per page load */
  var keyReady = KEY ? Promise.resolve(KEY)
                     : deriveKey(pass, blob.salt, blob.iter).then(function(k){ KEY = k; return k; });
  return keyReady.then(function(key){
    return crypto.subtle.decrypt({name: "AES-GCM", iv: b64(blob.iv)}, key, b64(blob.ct));
  }).then(function(buf){ return JSON.parse(new TextDecoder().decode(buf)); });
}

function unlock(pass){
  KEY = null;
  return json("ev_season.json.enc")
    .then(function(blob){ return openBlob(blob, pass); })
    .then(function(p){
      params = p; dataStamp = p.generated_at;
      baked = {}; params.weeks.forEach(function(w){ baked[w.week] = w.state; });
      return json("ev_history.json.enc")
        .then(function(hb){ return openBlob(hb, pass); })
        .then(function(h){ hist = h; })
        .catch(function(){ /* the tape is optional */ });
    })
    .then(function(){
      try { localStorage.setItem(STORE, pass); } catch(e) {}
      el("lock").hidden = true;
      run();
      pullLive().catch(function(){}).then(function(){ run(); });
    });
}

function boot(){
  if(!LOCKED){ run(); refresh(); return; }
  el("lock").hidden = false;
  var saved = null;
  try { saved = localStorage.getItem(STORE); } catch(e) {}
  var submit = function(){
    var pass = el("pass").value;
    if(!pass) return;
    el("lockerr").textContent = "unlocking…";
    unlock(pass).catch(function(e){
      KEY = null;
      el("lockerr").textContent = (e && e.name === "OperationError")
        ? "Wrong passphrase." : "Could not load the data.";
    });
  };
  el("unlock").onclick = submit;
  el("pass").addEventListener("keydown", function(e){ if(e.key === "Enter") submit(); });
  if(saved){ el("pass").value = saved; submit(); }
}

/* ---------- boot ---------- */
el("refresh").onclick = function(){
  if(!LOCKED) return refresh();
  var pass = null; try { pass = localStorage.getItem(STORE); } catch(e) {}
  if(pass) unlock(pass).catch(function(){});
};
document.addEventListener("visibilitychange", function(){
  if(!document.hidden && Date.now() - new Date(lastOk).getTime() > 60000) refresh();
});
boot();
setInterval(function(){
  if(LOCKED && el("lock").hidden === false) return;
  var live = params.weeks.filter(function(w){ return w.state === "live"; })[0];
  if(live || Date.now() - new Date(lastOk).getTime() > 600000) el("refresh").onclick();
}, 60000);
"""


def build(locked: bool = False) -> str:
    """`locked` builds the published page: no data baked in, only a lock screen
    and the fetchers for the encrypted files beside it."""
    params = json.loads(PARAMS.read_text())
    history = json.loads(TAPE_PLAIN.read_text()) if TAPE_PLAIN.exists() \
        else {"season": params["season"], "at": [], "week": [], "recon": [], "series": {}}
    engine = (REPO / "scripts" / "ev_sim.js").read_text()
    if locked:
        # the shell must carry NOTHING but the league's shape: no priors, no
        # results, no tape. Everything real arrives encrypted.
        # note what is NOT in this list: league_id, read_base, teams, weeks,
        # model, generated_at. The published shell cannot even name the league.
        params = {k: v for k, v in params.items()
                  if k in ("season", "reg_season_weeks", "current_week", "sigma",
                           "tau_prior", "share_usd", "payout_usd")}
        params.update(teams=[], weeks=[], model={}, generated_at="")
        history = {"season": params["season"], "at": [], "week": [], "recon": [], "series": {}}
    share = int(params["share_usd"])
    body = ((LOCK if locked else "") + BODY).replace("$SHARE", f"${share}")
    body = (body.replace("$SHARE", f"${share}")
                .replace("$P1", f"{share*7:,}")
                .replace("$P2", f"{share*2:,}")
                .replace("$ENTRY", f"{share:,}"))
    script = (SCRIPT.replace("__LOCKED__", "true" if locked else "false")
                    .replace("__PARAMS__", json.dumps(params, separators=(",", ":")))
                    .replace("__HISTORY__", json.dumps(history, separators=(",", ":")))
                    .replace("__SIMS__", str(SIMS)))
    week = params["current_week"]
    title = f"Pike EV — {params['season']} week {week}" if not locked else "Pike EV"
    return (head(title, CSS) + body
            + "<script>" + engine + "</script>"
            + "<script>" + script + "</script>"
            + "</body></html>\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--local", action="store_true",
                    help="bake the plaintext in, for opening off your own disk "
                         "(NEVER publish this build)")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    locked = not args.local
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build(locked=locked))
    kb = args.out.stat().st_size / 1024
    print(f"[page] {args.out.name}  ({kb:.0f} KB, {SIMS} sims/run, "
          f"{'locked' if locked else 'LOCAL PLAINTEXT'})")


if __name__ == "__main__":
    main()
