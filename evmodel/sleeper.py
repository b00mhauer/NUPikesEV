"""Sleeper — where the week-to-week SHAPE of a player's season comes from.

ESPN publishes a week-specific projection for exactly one week: the current one.
Every week after that, this model was using a flat rest-of-season rate, so week 9
and week 12 looked identical for a given player. Sleeper publishes projections
for every remaining week, differentiated by opponent, and serves them without
auth in one call per week.

WHAT WE TAKE, AND WHAT WE DELIBERATELY DO NOT. Only the shape:

    factor[player][week] = sleeper[week] / (that player's average sleeper week)

and the model's own number is then `espn_rate * factor`. The level stays ESPN's,
because ESPN scores against THIS league's rules (its appliedTotal is computed
from the league's own scoring settings) while Sleeper scores against its own. A
ratio cancels that difference and leaves the matchup information, which is the
only thing we came for. It also means a player Sleeper has never heard of, or a
week it omits, simply gets a factor of 1.0 — exactly the behaviour we had before
this file existed. There is no failure mode here that is worse than the status
quo, which is the only reason it is safe to depend on an undocumented API.

MATCHING (measured on the live league, week 3 2026):
  * `espn_id` is a trap — Sleeper carries it for barely half its players and for
    only 30% of ours; Bijan Robinson has none. Do not join on it.
  * names matched 159 of 172 rostered players, and every single miss was a D/ST.
  * so defences join on TEAM CODE (a closed set of 32, exact), and skill players
    on name + position, which took coverage to effectively 100%.
Coverage is counted on every run and reported, because the day Sleeper renames a
field this should be visible, not silent.
"""

from __future__ import annotations

import re
import time

import requests

from . import config

BASE = "https://api.sleeper.com/projections/nfl"
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
TIMEOUT = 40
RETRIES = 3
# A factor outside this band is not a matchup, it is a data problem (a player
# listed for a half-week, a scoring quirk). Clamp rather than propagate it.
FACTOR_BOUNDS = (0.55, 1.65)
# Sleeper's team codes vs ESPN's, where they differ.
TEAM_ALIAS = {"JAC": "JAX", "WSH": "WAS", "LAR": "LA"}


def norm(name: str) -> str:
    n = str(name).lower().replace(".", "").replace("'", "").replace("-", " ")
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return re.sub(r"\s+", " ", n).strip()


def fetch_week(season: int, week: int) -> list[dict]:
    """Every position for one week, in a single call."""
    params = [("season_type", "regular"), ("order_by", "ppr")]
    params += [("position[]", p) for p in POSITIONS]
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            r = requests.get(f"{BASE}/{season}/{week}", params=params, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            last = exc
            if attempt < RETRIES - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"sleeper week {week} failed: {last}")


def index(rows: list[dict]) -> tuple[dict, dict]:
    """(skill players by name+position, defences by team code) -> projected points.

    `pts_std` is standard scoring, the closest of Sleeper's three to this league,
    which matters only a little: we use these as ratios, not as points.
    """
    skill, dst = {}, {}
    for r in rows:
        p = r.get("player") or {}
        pts = r.get("stats", {}).get("pts_std")
        if pts is None:
            continue
        pos = p.get("position")
        if pos == "DEF":
            team = p.get("team")
            if team:
                dst[TEAM_ALIAS.get(team, team)] = float(pts)
        else:
            key = (norm(f"{p.get('first_name','')} {p.get('last_name','')}"), pos)
            skill[key] = float(pts)
    return skill, dst


def score_line(stats: dict) -> float:
    """Sleeper's raw projected stat line, scored under THIS league's rules."""
    return sum(pts * float(stats.get(col) or 0.0)
               for col, pts in config.SLEEPER_STAT_POINTS.items())


def index_points(rows: list[dict]) -> tuple[dict, dict]:
    """Same shape as index(), but the value is points in OUR scoring.

    index() deliberately keeps Sleeper's own pts_std because it is only ever used
    as a ratio, where the scoring cancels. This one is a LEVEL, so it has to be
    scored under our rules: the league's -1 per sack and its lack of PPR move a
    QB or a receiver by several points a week."""
    skill, dst = {}, {}
    for r in rows:
        p = r.get("player") or {}
        st = r.get("stats") or {}
        if st.get("pts_ppr") is None and st.get("pts_std") is None:
            continue
        pos = p.get("position")
        if pos == "DEF":
            team = p.get("team")
            if team:
                # SLEEPER_STAT_POINTS has no defensive columns, so keep their total
                dst[TEAM_ALIAS.get(team, team)] = float(st.get("pts_std") or 0.0)
        else:
            key = (norm(f"{p.get('first_name','')} {p.get('last_name','')}"), pos)
            # Kickers the same way, and for the same reason: STAT_POINTS covers the
            # offensive stat ids only, so scoring a kicker's line under it returns
            # 0.0 for every kicker -- which would silently zero the slot for every
            # week ESPN does not publish. Their standard total is the honest stand-in
            # until the league's own FG-by-distance rules are in STAT_POINTS.
            skill[key] = float(st.get("pts_std") or 0.0) if pos == "K" \
                else score_line(st)
    return skill, dst


def week_points(players: list[dict], season: int, weeks: list[int],
                team_abbrev: dict[int, str],
                by_week: dict[int, tuple[dict, dict]] | None = None) -> tuple[dict, dict]:
    """player_id -> {week: points in OUR scoring}, plus a coverage report.

    The LEVEL counterpart to week_factors(). A single week is meaningful here,
    unlike a shape which needs two, so coverage runs higher."""
    if by_week is None:
        by_week = {w: index_points(fetch_week(season, w)) for w in weeks}
    out: dict[int, dict[int, float]] = {}
    matched = set()
    for p in players:
        pid, pos = p["player_id"], p["pos"]
        series = {}
        for w in weeks:
            skill, dst = by_week[w]
            if pos == "DST":
                code = team_abbrev.get(p["pro_team"])
                val = dst.get(TEAM_ALIAS.get(code, code)) if code else None
            else:
                val = skill.get((norm(p["name"]), pos))
            if val is not None:
                series[w] = float(val)
        if series:
            out[pid] = series
            matched.add(pid)
    report = {"coverage": len(matched) / len(players) if players else 0.0,
              "matched": len(matched), "players": len(players), "error": None}
    return out, report


def week_factors(players: list[dict], season: int, weeks: list[int],
                 team_abbrev: dict[int, str],
                 by_week: dict[int, tuple[dict, dict]] | None = None) -> tuple[dict, dict]:
    """player_id -> {week: factor}, plus a coverage report.

    `players` are the tidy rows from espn_live.rosters(); `team_abbrev` maps an
    ESPN proTeamId to its code, so defences can be matched on the team. Pass
    `by_week` (week -> index() output) to compute factors without the network.
    """
    if by_week is None:
        by_week = {w: index(fetch_week(season, w)) for w in weeks}

    raw: dict[int, dict[int, float]] = {}
    matched = set()
    for p in players:
        pid, pos = p["player_id"], p["pos"]
        series = {}
        for w in weeks:
            skill, dst = by_week[w]
            if pos == "DST":
                code = team_abbrev.get(p["pro_team"])
                val = dst.get(TEAM_ALIAS.get(code, code)) if code else None
            else:
                val = skill.get((norm(p["name"]), pos))
            if val is not None and val > 0:
                series[w] = val
        if len(series) >= 2:          # one week alone says nothing about shape
            raw[pid] = series
            matched.add(pid)

    factors: dict[int, dict[int, float]] = {}
    for pid, series in raw.items():
        mean = sum(series.values()) / len(series)
        if mean <= 0:
            continue
        lo, hi = FACTOR_BOUNDS
        factors[pid] = {w: min(max(v / mean, lo), hi) for w, v in series.items()}

    report = {
        "players": len(players),
        "matched": len(matched),
        "coverage": round(len(matched) / len(players), 3) if players else 0.0,
        "weeks": len(weeks),
        "unmatched": sorted(p["name"] for p in players if p["player_id"] not in matched)[:12],
    }
    return factors, report
