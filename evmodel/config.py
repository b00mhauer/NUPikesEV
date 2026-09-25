"""League rules and the money — everything specific to one league, in one place.

The league itself is NOT hard-coded. This repository is public (GitHub Pages on
the free tier requires it), so the league id and season come from the
environment — set them as repository variables/secrets, not in the source. That
way the code is readable without handing anyone a one-click pull of a private
league's rosters.

    ESPN_LEAGUE_ID=123456   ESPN_SEASON=2026
"""

from __future__ import annotations

import os

LEAGUE_ID = int(os.environ.get("ESPN_LEAGUE_ID") or 0)
CURRENT_SEASON = int(os.environ.get("ESPN_SEASON") or 2026)
READ_BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"


def require_league() -> int:
    if not LEAGUE_ID:
        raise SystemExit(
            "ESPN_LEAGUE_ID is not set. Locally: export ESPN_LEAGUE_ID=... ; "
            "in Actions: add it as a repository secret.")
    return LEAGUE_ID


# --- roster construction ------------------------------------------------------
# QB · RB RB · WR WR · RB/WR flex · TE · K · D/ST, five bench, two IR.
POSITION = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DST"}

# VBD-style replacement level: the last player at each position who is startable
# league-wide (12 teams x the slots above). It prices a hole in a lineup at what
# streaming actually costs, not at zero.
REPLACEMENT_RANK = {"QB": 12, "RB": 30, "WR": 30, "TE": 12, "K": 12, "DST": 12}

# --- Sleeper's stat columns -> this league's points ---------------------------
# Sleeper ships the RAW projected stat line per player per week, so we can score
# it under this league's own rules rather than trusting its scoring. Mirrors
# config.STAT_POINTS in the private repo, which is the canonical map; these are
# league settings any member can read off ESPN, so nothing is given away here.
SLEEPER_STAT_POINTS = {
    "pass_yd": 0.04, "pass_td": 5.0, "pass_int": -2.0, "pass_2pt": 2.0,
    "pass_sack": -1.0,                       # the league's signature penalty
    "rush_yd": 0.1, "rush_td": 6.0, "rush_2pt": 2.0,
    "rec_yd": 0.1, "rec_td": 6.0, "rec_2pt": 2.0,
    "fum_lost": -2.0,
}

# --- Kicker and D/ST, straight from the league settings -----------------------
# The scoring map above covers the offensive stats only, which is why both these
# slots spent the season priced at whatever Sleeper's own standard scoring said.
# are this league's actual rules (ESPN league settings, pulled 2026-09-25), so a
# source that ships raw kicking and defensive components can finally be scored
# the way the league really pays.
KICKER_POINTS = {
    "pat_made": 1.0,     "pat_missed": -1.0,
    "fg_0_39": 3.0,      "fg_missed_0_39": -2.0,
    "fg_40_49": 4.0,     "fg_50_59": 5.0,      "fg_60_plus": 6.0,
}

DST_POINTS = {
    "sack": 1.0,              "interception": 2.0,   "fumble_recovered": 2.0,
    "safety": 2.0,            "blocked_kick": 2.0,
    # every way a defence scores is worth the same six
    "int_return_td": 6.0,     "fumble_return_td": 6.0, "blocked_kick_td": 6.0,
    "kick_return_td": 6.0,    "punt_return_td": 6.0,   "fumble_recovered_td": 6.0,
}

# Points allowed, as (lower bound, points). Read downwards and take the first
# bound the total clears. Note 22-27 is missing from the settings page, which
# means it is worth nothing -- the one band where a defence is neither rewarded
# nor punished.
DST_POINTS_ALLOWED = ((46, -7.0), (35, -4.0), (28, -1.0), (22, 0.0),
                      (18, 1.0), (14, 1.0), (7, 4.0), (1, 7.0), (0, 10.0))


def dst_points_allowed(points: float) -> float:
    """What a defence earns for holding an offence to `points`."""
    for lo, pts in DST_POINTS_ALLOWED:
        if points >= lo:
            return pts
    return DST_POINTS_ALLOWED[-1][1]


# --- which forecast drives the model ------------------------------------------
# "espn"    ESPN's season projection spread over the weeks he plays, shaped by
#           Sleeper's matchup ratio. The long-standing behaviour.
# "sleeper" Sleeper's weekly line, scored under SLEEPER_STAT_POINTS. Refreshed
#           weekly rather than derived from a season total that is not marked
#           down when a player misses time.
# "blend"   The weighted average of the two, per player per week. A player
#           Sleeper does not cover falls back to ESPN, never to zero.
# Set EV_PROJECTION_SOURCE as a repository variable to switch without a commit.
PROJECTION_SOURCE = (os.environ.get("EV_PROJECTION_SOURCE") or "blend").strip().lower()
BLEND_WEIGHTS = {"espn": float(os.environ.get("EV_BLEND_ESPN") or 0.5),
                 "sleeper": float(os.environ.get("EV_BLEND_SLEEPER") or 0.5)}

# --- FanDuel, the third opinion ----------------------------------------------
# A season total with no weeks in it, so it never touches shape -- it only says
# whether a player is priced too high or too low overall, as one multiplier on
# his weekly line. Deliberately 0: it moves team priors about a point a week and
# would reorder the top of the board immediately, which is either a real
# correction or a real error, and there is no telling from inside the model. At 0
# it is still pulled, scored and written into params, so weeks of it running
# alongside will say. EV_FD_WEIGHT turns it up without a commit.
FD_WEIGHT = float(os.environ.get("EV_FD_WEIGHT") or 1.0 / 3.0)

# --- the money ----------------------------------------------------------------
# Twelve owners ante one share each; the pot pays 8/3/1 shares to 1st/2nd/3rd.
# You never win your own share back, so NET shares are 7/2/0 and every other
# finish is -1. Third place is exactly break-even and the vector sums to zero
# across twelve teams — which is the model's cheapest self-check.
SHARE_USD = float(os.environ.get("EV_SHARE_USD") or 200.0)
PAYOUT_SHARES = {1: 8, 2: 3, 3: 1}
ENTRY_SHARES = 1

# Last place by REGULAR-SEASON record (points-for breaks the tie) takes the Shame
# Plaque. The playoffs decide the money only; ESPN's consolation ladder is
# ignored, the way this league ignores it.
SHAME_PLACE = 12


def net_shares(place: int, teams: int = 12) -> float:
    return PAYOUT_SHARES.get(place, 0) - ENTRY_SHARES


def payout_usd(place: int) -> float:
    return SHARE_USD * net_shares(place)
