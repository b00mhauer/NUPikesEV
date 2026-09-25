/**
 * ev_sim.js — the EV engine, in the browser.
 *
 * A faithful port of src/pikes/season_sim.py: same params file, same three week
 * states (final / live / future), same shrinkage toward the roster prior, same
 * bracket. It runs in the phone so the page can refetch live scores and reprice
 * the whole season without a server of ours in the path.
 *
 * tests/test_model.py runs this file under node against the Python simulator
 * on one fixture and fails if the two drift apart.
 *
 * Exports (module.exports under node, window.EVSim in the browser):
 *   simulate(params, nSims, seed, freezeLive) -> {teams: [...]}
 *   posterior(params)          buildWeeks(matchupJson, regWeeks, fallbackStates)
 *   overlayLive(params, weeks, matchupJson, proTeamsJson, nowMs)
 *   currentWeek(weeks)         weekOdds(params, week)
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.EVSim = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var TEAM_MINUTES = 9 * 60;   // nine starters, sixty minutes each
  var REG_SEASON_WEEKS = 14;

  /* ---- seeded RNG: reproducible runs, so a refresh doesn't jitter ---- */
  function rng(seed) {
    var s = seed >>> 0, spare = null;
    function unit() {
      s |= 0; s = (s + 0x6d2b79f5) | 0;
      var t = Math.imul(s ^ (s >>> 15), 1 | s);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    }
    return function normal() {          // Box-Muller, spare kept
      if (spare !== null) { var v = spare; spare = null; return v; }
      var u1 = unit() || 1e-12, u2 = unit();
      var r = Math.sqrt(-2 * Math.log(u1)), th = 2 * Math.PI * u2;
      spare = r * Math.sin(th);
      return r * Math.cos(th);
    };
  }

  /* ---- layer 2: what a team's own results have earned it ---- */
  function posterior(params) {
    var sigma2 = params.sigma * params.sigma;
    var tau2 = params.tau_prior * params.tau_prior;
    var out = {};
    params.teams.forEach(function (t) {
      var resid = 0, n = 0;
      params.weeks.forEach(function (wk) {
        if (wk.state !== "final") return;
        wk.matchups.forEach(function (m) {
          ["home", "away"].forEach(function (side) {
            if (m[side] !== t.team_id) return;
            resid += m[side + "_points"] - t.prior_weekly[String(wk.week)];
            n += 1;
          });
        });
      });
      var prec = 1 / tau2 + n / sigma2;
      out[t.team_id] = {
        shift: (resid / sigma2) / prec,
        var_: 1 / prec,
        games: n,
        weight: n / (n + sigma2 / tau2)
      };
    });
    return out;
  }

  /* ---- layer 3: the season, n times over ---- */
  function simulate(params, nSims, seed, freezeLive) {
    nSims = nSims || 15000;
    var normal = rng(seed || 20260101);
    var teams = params.teams, T = teams.length;
    var regWeeks = params.reg_season_weeks || REG_SEASON_WEEKS;
    var idx = {};
    teams.forEach(function (t, i) { idx[t.team_id] = i; });

    var post = posterior(params);
    var predSd = teams.map(function (t) {
      return Math.sqrt(params.sigma * params.sigma + post[t.team_id].var_);
    });
    var shift = teams.map(function (t) { return post[t.team_id].shift; });

    /* the week in front of us: the first one still undecided. Which side wins it
       in a given simulated season is worth keeping — it is what "what is Sunday
       worth" is computed from, without running a second set of seasons. */
    var pivotWeek = null;
    params.weeks.forEach(function (wk) {
      if (pivotWeek === null && wk.state !== "final" && wk.week <= regWeeks) pivotWeek = wk.week;
    });
    var pivotIndex = -1;

    /* one pass over the calendar: per week, each team is fixed, live or drawn */
    var plan = [];
    params.weeks.forEach(function (wk) {
      if (wk.week > regWeeks) return;
      var state = (freezeLive && wk.state === "live") ? "future" : wk.state;
      var mean = new Float64Array(T), sd = new Float64Array(T);
      var playing = new Uint8Array(T), pairs = [];
      wk.matchups.forEach(function (m) {
        var h = idx[m.home], a = idx[m.away];
        pairs.push([h, a]);
        [["home", h], ["away", a]].forEach(function (pair) {
          var side = pair[0], i = pair[1];
          playing[i] = 1;
          if (state === "final") {
            mean[i] = m[side + "_points"]; sd[i] = 0;
          } else if (state === "live") {
            /* `_frac` = share of the lineup still to kick off (espn_live);
               `_minutes` is the hosted tap's older signal, kept as a fallback. */
            var frac = (m[side + "_frac"] === undefined || m[side + "_frac"] === null)
              ? Math.max(0, m[side + "_minutes"] || 0) / TEAM_MINUTES
              : Number(m[side + "_frac"]);
            frac = Math.min(1, Math.max(0, frac));
            var proj = m[side + "_proj"];
            mean[i] = (proj === undefined || proj === null) ? m[side + "_points"] : proj;
            sd[i] = Math.sqrt((params.sigma * params.sigma + post[teams[i].team_id].var_) * frac);
          } else {
            mean[i] = teams[i].prior_weekly[String(wk.week)] + shift[i];
            sd[i] = predSd[i];
          }
        });
      });
      if (wk.week === pivotWeek) pivotIndex = plan.length;
      plan.push({ mean: mean, sd: sd, playing: playing, pairs: pairs });
    });

    var playoffMean = teams.map(function (t, i) { return t.prior_playoff + shift[i]; });
    var placeCount = [], shameCount = new Float64Array(T);
    var evSum = new Float64Array(T), winSum = new Float64Array(T), pfSum = new Float64Array(T);
    for (var i = 0; i < T; i++) placeCount.push(new Float64Array(T));

    var wins = new Float64Array(T), pf = new Float64Array(T);
    var score = new Float64Array(T), order = new Int32Array(T), place = new Int32Array(T);
    var payout = params.payout_usd || {};

    /* conditional on this week: sums and counts, split by who won it */
    var wonWeek = new Uint8Array(T), playsWeek = new Uint8Array(T);
    var evWin = new Float64Array(T), evLose = new Float64Array(T);
    var plyWin = new Float64Array(T), plyLose = new Float64Array(T);
    var nWin = new Float64Array(T), nLose = new Float64Array(T);
    if (pivotIndex >= 0) {
      plan[pivotIndex].pairs.forEach(function (pr) { playsWeek[pr[0]] = 1; playsWeek[pr[1]] = 1; });
    }

    function draw(i, weeks) {
      var tot = 0;
      for (var k = 0; k < weeks; k++) tot += playoffMean[i] + predSd[i] * normal();
      return tot;
    }

    for (var s = 0; s < nSims; s++) {
      wins.fill(0); pf.fill(0);
      for (var w = 0; w < plan.length; w++) {
        var p = plan[w];
        for (var t = 0; t < T; t++) {
          if (!p.playing[t]) continue;
          score[t] = p.sd[t] > 0 ? p.mean[t] + p.sd[t] * normal() : p.mean[t];
          pf[t] += score[t];
        }
        for (var g = 0; g < p.pairs.length; g++) {
          var h = p.pairs[g][0], a = p.pairs[g][1];
          var homeWon = score[h] > score[a], tied = score[h] === score[a];
          if (homeWon) wins[h] += 1;
          else if (!tied) wins[a] += 1;
          else { wins[h] += 0.5; wins[a] += 0.5; }
          if (w === pivotIndex) { wonWeek[h] = homeWon ? 1 : 0; wonWeek[a] = (!homeWon && !tied) ? 1 : 0; }
        }
      }

      /* seeding: record first, points-for breaks every tie */
      for (var q = 0; q < T; q++) order[q] = q;
      var key = new Float64Array(T);
      for (var q2 = 0; q2 < T; q2++) key[q2] = wins[q2] * 1e7 + pf[q2];
      Array.prototype.sort.call(order, function (x, y) { return key[y] - key[x]; });

      /* 1v4 and 2v3 over two weeks, then the final and the third-place game */
      var s1 = order[0], s2 = order[1], s3 = order[2], s4 = order[3];
      var aWin = draw(s1, 2) >= draw(s4, 2);
      var bWin = draw(s2, 2) >= draw(s3, 2);
      var finA = aWin ? s1 : s4, loseA = aWin ? s4 : s1;
      var finB = bWin ? s2 : s3, loseB = bWin ? s3 : s2;
      var champA = draw(finA, 1) >= draw(finB, 1);
      var first = champA ? finA : finB, second = champA ? finB : finA;
      var thirdA = draw(loseA, 1) >= draw(loseB, 1);
      var third = thirdA ? loseA : loseB, fourth = thirdA ? loseB : loseA;

      placeCount[first][0] += 1; placeCount[second][1] += 1;
      placeCount[third][2] += 1; placeCount[fourth][3] += 1;
      place[first] = 1; place[second] = 2; place[third] = 3; place[fourth] = 4;
      for (var pl = 5; pl <= T; pl++) {
        placeCount[order[pl - 1]][pl - 1] += 1;
        place[order[pl - 1]] = pl;
      }
      shameCount[order[T - 1]] += 1;      /* worst regular-season record */

      for (var t2 = 0; t2 < T; t2++) { winSum[t2] += wins[t2]; pfSum[t2] += pf[t2]; }

      if (pivotIndex >= 0) {
        for (var t3 = 0; t3 < T; t3++) {
          if (!playsWeek[t3]) continue;
          var pl = place[t3], cash = payout[String(pl)] || 0, made = pl <= 4 ? 1 : 0;
          if (wonWeek[t3]) { evWin[t3] += cash; plyWin[t3] += made; nWin[t3] += 1; }
          else { evLose[t3] += cash; plyLose[t3] += made; nLose[t3] += 1; }
        }
      }
    }

    var out = teams.map(function (t, i) {
      var pPlace = [], ev = 0;
      for (var pl = 0; pl < T; pl++) {
        var pr = placeCount[i][pl] / nSims;
        pPlace.push(pr);
        ev += pr * (payout[String(pl + 1)] || 0);
      }
      return {
        team_id: t.team_id, abbrev: t.abbrev, owner: t.owner, name: t.name || "",
        is_us: !!t.is_us,
        ev_usd: ev, p_place: pPlace,
        p_champ: pPlace[0], p_money: pPlace[0] + pPlace[1],
        p_playoffs: pPlace[0] + pPlace[1] + pPlace[2] + pPlace[3],
        p_shame: shameCount[i] / nSims,
        exp_wins: winSum[i] / nSims, exp_pf: pfSum[i] / nSims,
        post_shift: shift[i], post_weight: post[t.team_id].weight,
        games: post[t.team_id].games,
        stakes: (pivotIndex >= 0 && nWin[i] > 0 && nLose[i] > 0) ? {
          week: pivotWeek,
          p_win: nWin[i] / (nWin[i] + nLose[i]),
          ev_win: evWin[i] / nWin[i], ev_lose: evLose[i] / nLose[i],
          playoffs_win: plyWin[i] / nWin[i], playoffs_lose: plyLose[i] / nLose[i]
        } : null
      };
    });
    return { n_sims: nSims, seed: seed || 20260101, teams: out };
  }

  /* ---- ESPN -> week states (mirror of evmodel/espn_live.py) ---- */
  var STARTER_SLOTS = {0:1, 2:1, 4:1, 6:1, 16:1, 17:1, 23:1};
  var GAME_MS = 3 * 60 * 60 * 1000;

  /* A week ESPN has called is settled; one with points on the board but no
     verdict is in progress; anything else has not happened yet. */
  function buildWeeks(matchup, regWeeks, fallbackStates) {
    var weeks = {};
    (matchup.schedule || []).forEach(function (m) {
      var w = m.matchupPeriodId;
      if (!w || w > regWeeks || !m.home || !m.away) return;
      /* totalPointsLive, not totalPoints. ESPN leaves totalPoints at 0.0 while a
         game is being played and only settles it afterwards, so keying the state
         off it meant a week never went live: the header sat on "projected" and
         the panel kept showing the pre-game number for the whole slate. On the
         Thursday of week 3 that had three matchups displaying the wrong side as
         favourite while ESPN already had the lead changed. */
      var pts = function (t) {
        var live = t.totalPointsLive;
        return Number(live === undefined || live === null ? t.totalPoints : live) || 0;
      };
      var hp = pts(m.home), ap = pts(m.away);
      if (!weeks[w]) weeks[w] = { week: w, state: "future", matchups: [] };
      weeks[w].matchups.push({
        home: m.home.teamId, away: m.away.teamId,
        home_points: hp, away_points: ap
      });
      if ((m.winner || "UNDECIDED") !== "UNDECIDED") weeks[w].state = "final";
      else if (hp || ap) weeks[w].state = "live";
    });
    var out = Object.keys(weeks).map(Number).sort(function (a, b) { return a - b; })
      .map(function (w) { return weeks[w]; });
    if (fallbackStates) {
      out.forEach(function (wk) {
        /* trust the bake over an empty pull, never the other way round */
        if (wk.state === "future" && fallbackStates[wk.week] === "final") wk.state = "final";
      });
    }
    return out;
  }

  function kickoffs(proTeams) {
    var out = {};
    (((proTeams || {}).settings || {}).proTeams || []).forEach(function (t) {
      var games = t.proGamesByScoringPeriod || {};
      Object.keys(games).forEach(function (w) {
        (games[w] || []).forEach(function (g) {
          if (g.date) out[t.id + ":" + Number(w)] = Number(g.date);
        });
      });
    });
    return out;
  }

  function playedFraction(kickoffMs, nowMs) {
    if (!kickoffMs) return 1;                 /* bye or unknown: nothing pending */
    if (nowMs <= kickoffMs) return 0;
    return Math.min(1, (nowMs - kickoffMs) / GAME_MS);
  }

  /* The live week: ESPN's running score and its live projection, plus how much
     of each lineup has not kicked off yet — the scale for what uncertainty is
     left. Lineups come from the bake (`lineup_now`); ESPN's live matchup view
     returns stat lines with no player identity attached. */
  function overlayLive(params, weeks, matchup, proTeams, nowMs) {
    var ko = kickoffs(proTeams), lineups = {};
    params.teams.forEach(function (t) { lineups[t.team_id] = t.lineup_now || []; });

    var detail = {};
    (matchup.schedule || []).forEach(function (m) {
      ["home", "away"].forEach(function (side) {
        var team = m[side];
        if (!team || !team.teamId) return;
        var week = m.matchupPeriodId;
        var projTotal = 0, unplayed = 0;
        (lineups[team.teamId] || []).forEach(function (p) {
          var played = playedFraction(ko[p.pro_team + ":" + week], nowMs);
          projTotal += p.proj;
          unplayed += p.proj * (1 - played);
        });
        var live = team.totalPointsLive;
        detail[week + ":" + team.teamId] = {
          points: Number(live === undefined || live === null ? team.totalPoints : live) || 0,
          proj: Number(team.totalProjectedPointsLive) || projTotal ||
                Number(team.totalPoints) || 0,
          frac: projTotal > 0 ? unplayed / projTotal : 0
        };
      });
    });

    weeks.forEach(function (wk) {
      if (wk.state !== "live") return;
      wk.matchups.forEach(function (m) {
        ["home", "away"].forEach(function (side) {
          var d = detail[wk.week + ":" + m[side]];
          if (!d) return;
          m[side + "_points"] = d.points;
          m[side + "_proj"] = d.proj;
          m[side + "_frac"] = d.frac;
        });
      });
    });
    return weeks;
  }

  function currentWeek(weeks) {
    for (var i = 0; i < weeks.length; i++) if (weeks[i].state === "live") return weeks[i].week;
    for (var j = 0; j < weeks.length; j++) if (weeks[j].state === "future") return weeks[j].week;
    return weeks.length ? weeks[weeks.length - 1].week : 1;
  }

  /* ---- one week's matchup odds, analytically (no need to sim for these) ---- */
  function normalCdf(z) {
    /* Abramowitz & Stegun 7.1.26 on erf */
    var s = z < 0 ? -1 : 1, x = Math.abs(z) / Math.SQRT2;
    var t = 1 / (1 + 0.3275911 * x);
    var y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
                  - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x);
    return 0.5 * (1 + s * y);
  }

  function weekOdds(params, week) {
    var post = posterior(params), idx = {};
    params.teams.forEach(function (t, i) { idx[t.team_id] = i; });
    var wk = null;
    params.weeks.forEach(function (w) { if (w.week === week) wk = w; });
    if (!wk) return [];
    return wk.matchups.map(function (m) {
      var out = { home: m.home, away: m.away, state: wk.state,
                  home_points: m.home_points, away_points: m.away_points };
      var mu = {}, sd = {};
      ["home", "away"].forEach(function (side) {
        var t = params.teams[idx[m[side]]];
        var v = post[t.team_id].var_, s2 = params.sigma * params.sigma;
        if (wk.state === "final") { mu[side] = m[side + "_points"]; sd[side] = 0; }
        else if (wk.state === "live") {
          var frac = (m[side + "_frac"] === undefined || m[side + "_frac"] === null)
            ? Math.max(0, m[side + "_minutes"] || 0) / TEAM_MINUTES
            : Number(m[side + "_frac"]);
          frac = Math.min(1, Math.max(0, frac));
          mu[side] = (m[side + "_proj"] === undefined) ? m[side + "_points"] : m[side + "_proj"];
          sd[side] = Math.sqrt((s2 + v) * frac);
        } else {
          mu[side] = t.prior_weekly[String(week)] + post[t.team_id].shift;
          sd[side] = Math.sqrt(s2 + v);
        }
        out[side + "_proj"] = mu[side];
      });
      var sp = Math.sqrt(sd.home * sd.home + sd.away * sd.away);
      out.p_home = sp > 0 ? normalCdf((mu.home - mu.away) / sp)
                          : (mu.home > mu.away ? 1 : (mu.home < mu.away ? 0 : 0.5));
      return out;
    });
  }

  return { simulate: simulate, posterior: posterior, buildWeeks: buildWeeks,
           overlayLive: overlayLive, kickoffs: kickoffs,
           playedFraction: playedFraction, currentWeek: currentWeek,
           weekOdds: weekOdds, TEAM_MINUTES: TEAM_MINUTES };
});
