"""ESPN direct — the live league from ESPN's own read host, projections included.

Why this exists: the preseason board goes stale the moment the season starts. ESPN
re-projects every player continuously — injuries, depth-chart moves, usage — and
serves it on the same league endpoint as the rosters, unauthenticated, with CORS
open enough that the browser can read it too. That makes ESPN the single source
for everything the EV model needs about *strength*:

    mTeam                        owners, records, points-for
    mRoster                      rosters + per-player projections + injuryStatus
    mMatchupScore                the schedule, with every week's points
    mMatchupScore&scoringPeriodId  the live week in detail
    proTeamSchedules_wl (game)   bye weeks

The stats array carries several flavours of the same season; we read them by their
(statSourceId, statSplitTypeId) pair rather than ESPN's packed `id` string:

    (0, 0) season to date, actual        (1, 0) season projection  <- re-cut weekly
    (0, 1) one week, actual              (1, 1) one week, projection

Rest-of-season rate = (season projection - season to date) / weeks he still plays.

Payload note: mRoster is ~2 MB, so the scheduled job carries it, not the phone.
The browser polls only the live matchup view (~240 KB) plus the pro schedule it
caches once, which is enough to reprice a week in progress.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from . import config

TIMEOUT = 45
RETRIES = 4
# The NFL regular season ESPN projects across (17 games + one bye). Our fantasy
# season ends at scoring period 17, but the per-week RATE must be derived over
# ESPN's own horizon or it comes out inflated.
NFL_WEEKS = 18
# Out for this week only vs. out for the season. ESPN's own projection already
# marks a player down; this decides whether he can be in a lineup at all.
OUT_THIS_WEEK = {"OUT", "DOUBTFUL", "SUSPENSION"}
OUT_FOR_NOW = {"INJURY_RESERVE", "NOT_ACTIVE"}
IR_SLOT = 21
BENCH_SLOT = 20
STARTER_SLOTS = {0, 2, 4, 6, 16, 17, 23}   # QB RB WR TE K DST RB/WR-flex


def league_url(season: int | None = None) -> str:
    season = season or config.CURRENT_SEASON
    return (f"{config.READ_BASE}/seasons/{season}/segments/0/leagues/"
            f"{config.require_league()}")


def get(view: str, season: int | None = None, url: str | None = None, **params):
    """One ESPN read -> parsed JSON, retried through a cold spell."""
    params["view"] = view
    target = url or league_url(season)
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            r = requests.get(target, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            status = getattr(exc.response, "status_code", None)
            if status is not None and status < 500:
                raise
            last = exc
            if attempt < RETRIES - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"ESPN {view} failed after {RETRIES} tries: {last}")


def fetch(season: int | None = None) -> dict:
    """Every raw response the model needs, in one bundle (also our test fixture)."""
    season = season or config.CURRENT_SEASON
    return {
        "season": season,
        "teams": get("mTeam", season),
        "rosters": get("mRoster", season),
        "schedule": get("mMatchupScore", season),
        "proteams": get("proTeamSchedules_wl", url=f"{config.READ_BASE}/seasons/{season}"),
    }


# --------------------------------------------------------------------------
# tidying
# --------------------------------------------------------------------------
# --- The forward grid --------------------------------------------------------
# ESPN's default payload carries only the current week's projection, which is why
# this model spent weeks believing ESPN had nothing to say about week 9. It does:
# pass scoringPeriodId=N and mRoster returns EVERY rostered player's week-N
# projection, maintained the same way the current week is. A.J. Brown, on IR,
# reads 0.0 through week 6 and about 10 from week 7 -- ESPN models his return.
# Season-enders (Tank Dell, Jonathon Brooks) read 0.0 straight through. Byes are
# 0.0 in the right week. None of that is true of the SEASON total, which is cut
# in preseason and never revised; only the weekly rows are live.
#
# The cost is one 2.4 MB call per week, so pulling all fifteen remaining weeks is
# ~37 MB. The publish job runs every 15 minutes during games, and repeating that
# there would be several GB a day against ESPN for numbers that barely move --
# week 12's projection is not news on a Sunday afternoon. So the grid is cached
# and refreshed on its own slow clock; the CURRENT week still comes fresh with
# every run, through the ordinary fetch().
GRID_TTL_SECONDS = 6 * 3600


def weekly_grid(season: int, weeks: list[int]) -> dict[int, dict[int, float]]:
    """player_id -> {week: ESPN's own projection for that week}.

    One mRoster read per week. Weeks ESPN has no row for are simply absent, which
    is what the caller wants: absent means "no opinion", not "zero points".
    """
    grid: dict[int, dict[int, float]] = {}
    for w in weeks:
        raw = get("mRoster", season, scoringPeriodId=w)
        for team in raw.get("teams", []):
            for entry in (team.get("roster") or {}).get("entries", []):
                player = entry["playerPoolEntry"]["player"]
                v = _stat(player, 1, 1, season, week=w)
                if v is not None:
                    grid.setdefault(player["id"], {})[w] = float(v)
    return grid


def load_grid(path, season: int, weeks: list[int], now: float,
              ttl: int = GRID_TTL_SECONDS) -> tuple[dict[int, dict[int, float]], str]:
    """The cached forward grid, refetched when it is stale, thin, or absent.

    Returns (grid, why) -- `why` says which happened, so the run's log can show
    whether these numbers came off the wire or off disk.
    """
    cached, age = None, None
    try:
        blob = json.loads(Path(path).read_text())
        if blob.get("season") == season:
            cached = {int(pid): {int(w): float(v) for w, v in wk.items()}
                      for pid, wk in blob["grid"].items()}
            age = now - float(blob.get("fetched_at") or 0)
    except (OSError, ValueError, KeyError):
        cached = None

    if cached is not None and age is not None and age < ttl:
        # A cache from before a trade or a waiver claim can be missing the players
        # who arrived since. Covering most of them is fine -- the stragglers fall
        # through to Sleeper -- but a grid that has gone thin is not worth keeping.
        covered = sum(1 for wk in cached.values() if any(w in wk for w in weeks))
        if covered >= 100:
            return cached, f"cached {age / 3600:.1f}h old"

    grid = weekly_grid(season, weeks)
    Path(path).write_text(json.dumps(
        {"season": season, "fetched_at": now, "weeks": weeks,
         "grid": {str(k): {str(w): round(v, 2) for w, v in d.items()}
                  for k, d in grid.items()}}))
    return grid, "refetched"


def bye_weeks(proteams: dict) -> dict[int, int]:
    """proTeamId -> bye week (0 = unknown / already passed in ESPN's data)."""
    teams = proteams.get("settings", {}).get("proTeams", [])
    return {t["id"]: int(t.get("byeWeek") or 0) for t in teams}


def _stat(player: dict, source: int, split: int, season: int,
          week: int | None = None) -> float | None:
    for s in player.get("stats", []):
        if (s.get("statSourceId") == source and s.get("statSplitTypeId") == split
                and s.get("seasonId") == season
                and (week is None or s.get("scoringPeriodId") == week)):
            return s.get("appliedTotal")
    return None


def player_line(entry: dict, season: int, current_week: int,
                byes: dict[int, int]) -> dict:
    """One roster entry -> what the model needs: a weekly rate, this week's own
    projection where ESPN has published one, the bye, and whether he can play."""
    player = entry["playerPoolEntry"]["player"]
    bye = byes.get(player.get("proTeamId") or 0, 0)

    proj_season = _stat(player, 1, 0, season) or 0.0
    act_season = _stat(player, 0, 0, season) or 0.0
    # Rest-of-season rate = the season projection spread over the weeks he can
    # play. NOT (projection - points already scored): that number is a forecast of
    # the WHOLE season including the weeks already played, so subtracting what a
    # player banked charges him for producing. It handed Kyler Murray, who had
    # scored -0.5, a higher rest-of-season rate than Josh Allen, who had scored
    # 77.2 -- the projection is not marked down when a player misses time, so the
    # unearned points all landed on his remaining weeks. Measured against ESPN's
    # own maintained week projection across 162 rostered players, pro-rating cuts
    # MAE from 1.10 to 0.76 and centres the median error at 0.00 (from -0.16).
    left = [w for w in range(current_week, NFL_WEEKS + 1) if w != bye]
    playable = [w for w in range(1, NFL_WEEKS + 1) if w != bye]
    rate = proj_season / len(playable) if playable else 0.0

    weekly = {}
    for w in range(current_week, NFL_WEEKS + 1):
        v = _stat(player, 1, 1, season, week=w)
        if v is not None:
            weekly[w] = float(v)

    status = str(player.get("injuryStatus") or "ACTIVE").upper()
    slot = entry.get("lineupSlotId")
    return {
        "slot": slot,
        "starting": slot in STARTER_SLOTS,
        # per-week multipliers from `sleeper`, attached later by the exporter.
        # Empty means "no matchup information", which is the old behaviour.
        "factors": {},
        "player_id": player.get("id"),
        "name": player.get("fullName", ""),
        "pos": config.POSITION.get(player.get("defaultPositionId"), "NA"),
        "pro_team": player.get("proTeamId"),
        "bye": bye,
        "rate": float(rate),
        "weekly": weekly,
        "status": status,
        "on_ir": slot == IR_SLOT,
        "proj_season": float(proj_season),
        "act_season": float(act_season),
    }


def playable(p: dict, week: int, current_week: int) -> bool:
    """Can this player be in a starting lineup in `week`?

    Season-enders are gone for good; a weekly designation only takes him out of
    the week in front of us, because by week 9 a current 'OUT' tells us nothing.
    """
    if p["on_ir"] or p["status"] in OUT_FOR_NOW:
        return False
    if p["bye"] == week:
        return False
    if week == current_week and p["status"] in OUT_THIS_WEEK:
        return False
    return True


def rosters(raw: dict, current_week: int) -> dict[int, list[dict]]:
    """team_id -> its players, tidied."""
    season = raw["season"]
    byes = bye_weeks(raw["proteams"])
    out: dict[int, list[dict]] = {}
    for team in raw["rosters"].get("teams", []):
        out[team["id"]] = [player_line(e, season, current_week, byes)
                           for e in team.get("roster", {}).get("entries", [])]
    return out


def teams(raw: dict) -> list[dict]:
    members = {m["id"]: m for m in raw["teams"].get("members", [])}
    out = []
    for t in raw["teams"].get("teams", []):
        owner_id = (t.get("owners") or [None])[0]
        m = members.get(owner_id, {})
        owner = " ".join(x for x in (m.get("firstName"), m.get("lastName")) if x)
        out.append({
            "team_id": t["id"],
            "abbrev": t.get("abbrev", f"T{t['id']}"),
            "name": (t.get("name") or "").strip(),
            "owner": owner or m.get("displayName", "") or f"Team {t['id']}",
        })
    return sorted(out, key=lambda t: t["team_id"])


def weeks(raw: dict, reg_season_weeks: int) -> list[dict]:
    """Schedule + results in the simulator's shape. A week with points on the
    board is played; ESPN's `winner` field tells us it is settled."""
    by_week: dict[int, dict] = {}
    for m in raw["schedule"].get("schedule", []):
        w = m.get("matchupPeriodId")
        if not w or w > reg_season_weeks or "home" not in m or "away" not in m:
            continue
        home, away = m["home"], m["away"]
        # totalPointsLive, not totalPoints. ESPN leaves totalPoints at 0.0 while a
        # game is being played and only settles it afterwards, so keying the state
        # off it meant a week never went live: the page sat on "projected" and kept
        # showing the pre-game number for the whole slate. live_week() below always
        # read the live field -- it was only the state that did not.
        hp = float(home.get("totalPointsLive", home.get("totalPoints")) or 0.0)
        ap = float(away.get("totalPointsLive", away.get("totalPoints")) or 0.0)
        settled = m.get("winner", "UNDECIDED") != "UNDECIDED"
        wk = by_week.setdefault(w, {"week": w, "state": "future", "matchups": []})
        wk["matchups"].append({"home": home["teamId"], "away": away["teamId"],
                               "home_points": hp, "away_points": ap})
        if settled:
            wk["state"] = "final"
        elif hp or ap:
            wk["state"] = "live"
    return [by_week[w] for w in sorted(by_week)]


def current_week(raw: dict) -> int:
    return int(raw["schedule"].get("status", {}).get("currentMatchupPeriod") or 1)


def reg_season_weeks(raw: dict) -> int:
    sched = raw["schedule"].get("settings", {}).get("scheduleSettings", {})
    return int(sched.get("matchupPeriodCount") or 14)


# --------------------------------------------------------------------------
# the live week — how much of each lineup is still on the field
# --------------------------------------------------------------------------
# A game is treated as fully played three hours after kickoff, ramping linearly
# from the whistle (the espn-api library uses the same three-hour window, as a
# step; the ramp is what lets variance bleed off through an afternoon).
GAME_MS = 3 * 60 * 60 * 1000


def kickoffs(proteams: dict) -> dict[tuple[int, int], int]:
    """(proTeamId, week) -> kickoff epoch ms."""
    out: dict[tuple[int, int], int] = {}
    for t in proteams.get("settings", {}).get("proTeams", []):
        for week, games in (t.get("proGamesByScoringPeriod") or {}).items():
            for g in games:
                if g.get("date"):
                    out[(t["id"], int(week))] = int(g["date"])
    return out


def played_fraction(kickoff_ms: int | None, now_ms: int) -> float:
    """0 = has not kicked off, 1 = in the books."""
    if not kickoff_ms:
        return 1.0                       # bye or unknown: nothing left to wait for
    if now_ms <= kickoff_ms:
        return 0.0
    return min(1.0, (now_ms - kickoff_ms) / GAME_MS)


def live_week(matchup_raw: dict, rosters_by_team: dict[int, list[dict]],
              kickoff_map: dict[tuple[int, int], int], week: int,
              now_ms: int) -> dict[int, dict]:
    """team_id -> {points, proj, frac} for the week in progress.

    `frac` is the share of the team's projected points still unplayed, taken from
    real kickoff times — the honest scale for how much uncertainty is left. A
    Sunday-morning lineup is all risk; a Monday-night one is nearly settled.

    The lineup comes from mRoster: the live matchup view returns stat lines with
    no player identity attached, so it cannot tell us who is still to kick off.
    """
    out: dict[int, dict] = {}
    for m in matchup_raw.get("schedule", []):
        if m.get("matchupPeriodId") != week:
            continue
        for side in ("home", "away"):
            team = m.get(side)
            if not team:
                continue
            tid = team["teamId"]
            proj_total, unplayed = 0.0, 0.0
            for p in rosters_by_team.get(tid, []):
                if not p["starting"]:
                    continue
                proj = p["weekly"].get(week, p["rate"])
                played = played_fraction(kickoff_map.get((p["pro_team"], week)), now_ms)
                proj_total += proj
                unplayed += proj * (1.0 - played)
            points = float(team.get("totalPointsLive", team.get("totalPoints")) or 0.0)
            proj = float(team.get("totalProjectedPointsLive") or 0.0) or proj_total or points
            out[tid] = {
                "points": points,
                "proj": proj,
                "frac": (unplayed / proj_total) if proj_total > 0 else 0.0,
                "espn_win_prob": team.get("winProbability"),
            }
    return out
