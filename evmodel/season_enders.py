"""Players whose season is over, before the feed says so.

The model already handles season-enders well: ESPN moves a player to IR, its own
weekly projections read 0.00 for every week left, `playable` returns False
forever, and he leaves the lineup, the grade grid and the priors together. Tank
Dell, Jonathon Brooks and De'Zhaun Stribling are all handled that way today.

The gap is timing. A season-ending injury is NEWS days before it is a roster
move. Until the team makes the move ESPN carries the weekly designation OUT,
which the model deliberately treats as a one-week fact -- a week-4 OUT says
nothing about week 9, and treating every OUT as terminal would delete half the
league every Sunday. So in between, a player who will never take another snap
keeps his full projection.

That is not theoretical. De'Von Achane, reported out for the season, still read
15.1 / 13.9 / 15.7 ... 16.0 in ESPN's grid for every remaining week and 16.02 in
Sleeper's. He was graded A in a lineup labelled FULL-STRENGTH on the same page
that listed him as out -- and, worse than the grade, his team carried about
fifteen points a week of him in the season simulation.

A name here is treated exactly as IR: gone from every lineup, every week, the
grade grid, the replacement pools and the priors. It makes no claim about
magnitude and adjusts no projection. The only thing it asserts is "this player
has no season left", which is the one fact the feeds are slow on and a human
reading the news has immediately.

Removing a name once ESPN catches up is tidy but not required: by then `on_ir`
says the same thing and the override is a no-op.
"""

from __future__ import annotations

import json
from pathlib import Path

from .sleeper import norm

# Kept as a literal in espn_live.OUT_FOR_NOW too, so that module needs no import
# of this one.
STATUS = "OUT_SEASON"

PATH = Path(__file__).resolve().parents[1] / "data" / "out_for_season.json"


def load(season: int, path: Path = PATH) -> dict[str, str]:
    """{name or ESPN player id: why} for one season. Missing file -> {}."""
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    entry = doc.get(str(season))
    return dict(entry) if isinstance(entry, dict) else {}


def mark(players: list[dict], season: int, path: Path = PATH) -> list[dict]:
    """Stamp STATUS on anyone the override names, in place.

    Matched on ESPN player id or on the normalised name, so an entry can be
    written either way -- the id is unambiguous, the name is what a human has.
    """
    over = load(season, path)
    if not over:
        return players
    ids = {str(k) for k in over}
    names = {norm(k) for k in over}
    for p in players:
        if str(p.get("player_id")) in ids or norm(p.get("name", "")) in names:
            p["status"] = STATUS
    return players
