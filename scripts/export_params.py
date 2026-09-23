"""Build the model's params file, straight from ESPN.

Everything about strength comes from ESPN's own continuously re-cut projections
(`evmodel.espn_live`): rosters, rest-of-season and per-week numbers, injury
designations, byes. That is what makes the model react when a starter is ruled
out instead of pricing a roster that stopped existing in August.

Writes `data/params.json` — the input to the simulator, the snapshot recorder
and (encrypted) the published page. It is never committed in the clear: this
repository is public.

Usage:  python scripts/export_params.py [--season 2026]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from evmodel import (config, espn_live, projection_log, roster_strength,  # noqa: E402
                     season_sim, sleeper)

OUT = REPO / "data" / "params.json"
FORWARD_LOG = REPO / "data" / "projection_log.json"
# Whose card sits at the top of the page. Set it in the environment, not here —
# the repository is public and there is no reason to publish anyone's name.
OUR_OWNER = os.environ.get("EV_OWNER", "")
PLAYOFF_WEEK = 99          # the optimizer's "no byes left" sentinel


def notable_injuries(players: list[dict], scale: float) -> list[dict]:
    """Who is unavailable, and what his absence is worth per week — the line the
    app points at when a team's EV drops."""
    out = []
    for p in players:
        if p["on_ir"] or p["status"] in espn_live.OUT_FOR_NOW | espn_live.OUT_THIS_WEEK:
            out.append({"name": p["name"], "pos": p["pos"], "status": p["status"],
                        "on_ir": p["on_ir"],
                        "cost": round(roster_strength.player_week(p, PLAYOFF_WEEK, scale), 1)})
    return sorted(out, key=lambda x: -x["cost"])[:6]


def build(season: int) -> dict:
    raw = espn_live.fetch(season)
    current = espn_live.current_week(raw)
    reg_weeks = espn_live.reg_season_weeks(raw)
    weeks = espn_live.weeks(raw, reg_weeks)
    rosters = espn_live.rosters(raw, current)

    all_players = [p for team in rosters.values() for p in team]
    replacement = roster_strength.replacement_levels(all_players)
    scale = roster_strength.rate_scale(all_players, current)

    # Matchup shape for the weeks ESPN does not project (everything after this
    # one). If Sleeper is unreachable or has renamed something, every factor
    # stays 1.0 and the model falls back to the flat rate it used before — so
    # this is allowed to fail, but never quietly.
    span_ahead = [w for w in range(current + 1, reg_weeks + 1)]
    shape = {"coverage": 0.0, "matched": 0, "players": len(all_players), "error": None}
    if span_ahead:
        try:
            abbrev = {t["id"]: t["abbrev"]
                      for t in raw["proteams"]["settings"]["proTeams"]}
            factors, shape = sleeper.week_factors(all_players, season, span_ahead, abbrev)
            for p in all_players:
                p["factors"] = factors.get(p["player_id"], {})
        except Exception as exc:                      # noqa: BLE001 - never fatal
            shape = {"coverage": 0.0, "matched": 0, "players": len(all_players),
                     "error": f"{type(exc).__name__}: {exc}"[:120]}
            print(f"[params] WARNING sleeper unavailable ({shape['error']}) — "
                  "future weeks fall back to the flat rate")

    # the live week (if any) gets real scores + the share still to kick off
    live = {}
    for wk in weeks:
        if wk["state"] == "live":
            detail = espn_live.get("mMatchupScore", season, scoringPeriodId=wk["week"])
            live = espn_live.live_week(detail, rosters, espn_live.kickoffs(raw["proteams"]),
                                       wk["week"], int(time.time() * 1000))
            for m in wk["matchups"]:
                for side in ("home", "away"):
                    d = live.get(m[side])
                    if not d:
                        continue
                    m[f"{side}_points"] = d["points"]
                    m[f"{side}_proj"] = d["proj"]
                    m[f"{side}_frac"] = d["frac"]

    # Forward test: bank this week's predictions before the weeks are played,
    # so the matchup layer can be graded later with no hindsight.
    if span_ahead:
        log = projection_log.load(FORWARD_LOG, season)
        if projection_log.record(log, current, int(time.time()), all_players,
                                 span_ahead, scale):
            projection_log.save(log, FORWARD_LOG)
            print(f"[params] forward test: banked week {current} "
                  f"({len(span_ahead)} weeks ahead)")

    span = list(range(1, reg_weeks + 1))
    rep_week = roster_strength.replacement_by_week(all_players, span, current)
    raw_priors = {tid: roster_strength.weekly_priors(pl, span, current, rep_week, scale)
                  for tid, pl in rosters.items()}

    finished = [(m[side], wk["week"], m[f"{side}_points"])
                for wk in weeks if wk["state"] == "final"
                for m in wk["matchups"] for side in ("home", "away")]
    cal, basis = roster_strength.calibration(raw_priors, finished)

    out_teams = []
    for t in espn_live.teams(raw):
        tid = t["team_id"]
        players = rosters.get(tid, [])
        prior = {str(w): round(raw_priors[tid][w] * cal, 3) for w in span}
        out_teams.append({
            **t,
            "is_us": bool(OUR_OWNER) and OUR_OWNER.lower() in t["owner"].lower(),
            "prior_weekly": prior,
            "prior_playoff": round(
                roster_strength.playoff_prior(players, current, replacement, scale) * cal, 3),
            "injuries": notable_injuries(players, scale),
            "lineup_now": [
                {"pro_team": p["pro_team"],
                 "proj": round(roster_strength.player_week(p, current, scale), 2)}
                for p in players if p["starting"]
            ],
            "starters": [
                # `pos` is the position the player actually PLAYS, which is what the
                # grade compares him against -- a flex RB is graded against RBs, not
                # against other teams' flex slots.
                {"name": name, "slot": slot,
                 "pos": next((p["pos"] for p in players if p["name"] == name), slot),
                 "proj": round(next((roster_strength.player_week(p, PLAYOFF_WEEK, scale)
                                     for p in players if p["name"] == name), 0.0), 1)}
                for slot, name in roster_strength.lineup(
                    players, PLAYOFF_WEEK, current, replacement,
                    ignore_bye=True, scale=scale)[1]
            ],
        })

    params = {
        "season": season,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reg_season_weeks": reg_weeks,
        "current_week": current,
        "sigma": season_sim.SIGMA_WEEK,
        "tau_prior": season_sim.TAU_PRIOR,
        "share_usd": config.SHARE_USD,
        "payout_usd": {str(p): config.payout_usd(p) for p in range(1, len(out_teams) + 1)},
        "source": "espn",
        # the browser needs these to poll ESPN itself — and they travel INSIDE the
        # encrypted payload, so the published page names no league until unlocked
        "league_id": config.require_league(),
        "read_base": config.READ_BASE,
        "teams": out_teams,
        "weeks": weeks,
        "model": {
            "calibration_scale": round(cal, 4),
            "calibrated_to": basis,
            "rate_scale": round(scale, 4),
            "replacement": {k: round(v, 2) for k, v in replacement.items()},
            "players": len(all_players),
            "matchup_source": "sleeper" if shape.get("matched") else "none (flat rate)",
            "matchup_coverage": shape.get("coverage", 0.0),
            "matchup_error": shape.get("error"),
            "injury_counts": dict(Counter(p["status"] for p in all_players
                                          if p["status"] != "ACTIVE")),
        },
    }
    season_sim.check_params(params)
    return params


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--season", type=int, default=config.CURRENT_SEASON)
    args = ap.parse_args()

    params = build(args.season)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(params, indent=1))
    # Actions logs are PUBLIC on a public repo, so this says how much, never what:
    # no team, no owner, no number that prices anybody's season.
    m = params["model"]
    print(f"[params] week {params['current_week']}  {len(params['teams'])} teams  "
          f"{m['players']} players  rate x{m['rate_scale']}  "
          f"level x{m['calibration_scale']}  "
          f"matchup {m['matchup_source']} {m['matchup_coverage']:.0%}  "
          f"{OUT.stat().st_size//1024} KB")


if __name__ == "__main__":
    main()
