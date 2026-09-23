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
