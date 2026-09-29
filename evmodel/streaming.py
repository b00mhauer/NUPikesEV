"""Who streams kickers and defences, and what the wire is actually worth.

The model used to price every K and D/ST slot at whatever that team happened to
roster. For a manager who streams the position that is the wrong question: he is
not stuck with his D/ST, he drops it the moment a better matchup appears, so the
honest floor for his slot is the best thing available on the wire that week --
not the middling defence sitting on his bench.

Measured on this league, the gap is not small. Over weeks 4-14 the best free
agent D/ST projects 7.64 a week while one particular rostered defence averages
6.45, so treating a streamer as an owner understated him by more than a point a
week and made a D/ST look like a worthwhile trade target when it was worth
approximately nothing. R-notes in roster_strength say the same thing from the
other end: across 3,854 pickup-starts, 2018-2025, a streamed D/ST scored 8.1 and
a streamed K 8.2 -- the same as league-wide ROSTERED starters (8.1 and 8.4).
Holding these positions buys nothing, which is exactly why people stream them.

Two design choices worth stating.

STREAMING IS AN OPTION, NOT A COMMITMENT. A streamer's slot is priced at
max(his best rostered player, the wire), never at the wire alone. Nobody benches
a good defence to chase a worse one, so this can only ever help a team and never
hurts one who happens to roster someone excellent.

THE WIRE LINE IS AN UPPER BOUND. It is the best AVAILABLE projection, which
assumes he sees the matchup and wins the claim. A real streamer misses some. The
rank is tunable (EV_WIRE_RANK, 1 = best available, 2 = second best) so the
optimism can be dialled back without a commit if the tape says it should be.

Who streams is read from the environment like the management overlay, and for
the same reason: the repository is public, and an assumption about how a named
rival manages his roster is scouting, not a scoreboard. Unset means nobody
streams, which is exactly the old behaviour.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

from . import config

ENV_VAR = "EV_STREAMS"
RANK_VAR = "EV_WIRE_RANK"
STREAMABLE = ("K", "DST")
# ESPN lineup slot ids for the free-agent filter
SLOT = {"K": 17, "DST": 16}
TTL_SECONDS = 6 * 3600
POOL_LIMIT = 50


def load(blob: str | None = None) -> dict[str, list[str]]:
    """{"Parrott": ["DST"], "Glowacki": ["K", "DST"]} from $EV_STREAMS.

    Keys match an owner by case-insensitive substring, the same way the overlay
    does, so a surname is enough. Anything unparseable is read as "nobody
    streams": a typo in a secret must not change what the model says about the
    league, and silence is the safe direction here.
    """
    text = blob if blob is not None else os.environ.get(ENV_VAR)
    if not text:
        return {}
    try:
        doc = json.loads(text)
    except (ValueError, TypeError):
        return {}
    if not isinstance(doc, dict):
        return {}
    out = {}
    for owner, spec in doc.items():
        if isinstance(spec, str):
            spec = [spec]
        if not isinstance(spec, (list, tuple)):
            continue
        keep = [p.upper() for p in spec if str(p).upper() in STREAMABLE]
        if keep:
            out[str(owner)] = sorted(set(keep))
    return out


def resolve(streams: dict[str, list[str]], teams: list[dict]) -> dict[int, set[str]]:
    """Owner-name keys -> team_id sets. Teams nobody named stream nothing.

    A key matching two owners is a mistake worth stopping for, same as the
    overlay: quietly changing the wrong team's lineup rules is worse than a
    failed build.
    """
    out: dict[int, set[str]] = {t["team_id"]: set() for t in teams}
    for key, positions in streams.items():
        hit = [t for t in teams if key.lower() in str(t.get("owner", "")).lower()]
        if len(hit) > 1:
            raise ValueError(
                f"streams key {key!r} matches {len(hit)} owners: "
                + ", ".join(t["owner"] for t in hit))
        if hit:
            out[hit[0]["team_id"]] = set(positions)
    return out


def _fetch_week(season: int, week: int, pos: str, rank: int) -> float | None:
    """The rank-th best AVAILABLE projection at `pos` in `week`, or None."""
    filt = {"players": {
        "filterStatus": {"value": ["FREEAGENT", "WAIVERS"]},
        "filterSlotIds": {"value": [SLOT[pos]]},
        "limit": POOL_LIMIT,
        "sortPercOwned": {"sortAsc": False, "sortPriority": 1}}}
    url = (f"{config.READ_BASE}/seasons/{season}/segments/0/leagues/"
           f"{config.require_league()}")
    r = requests.get(url, timeout=30,
                     params={"view": "kona_player_info", "scoringPeriodId": week},
                     headers={"x-fantasy-filter": json.dumps(filt)})
    r.raise_for_status()
    vals = []
    for entry in r.json().get("players", []):
        p = entry.get("player") or {}
        for s in p.get("stats", []):
            if (s.get("statSourceId") == 1 and s.get("statSplitTypeId") == 1
                    and s.get("seasonId") == season
                    and s.get("scoringPeriodId") == week
                    and (s.get("appliedTotal") or 0) > 0):
                vals.append(float(s["appliedTotal"]))
    vals.sort(reverse=True)
    return vals[rank - 1] if len(vals) >= rank else (vals[-1] if vals else None)


def wire_levels(path, season: int, weeks: list[int], now: float,
                ttl: int = TTL_SECONDS,
                rank: int | None = None) -> tuple[dict[int, dict[str, float]], str]:
    """{week: {"K": x, "DST": y}} -- what a streamer can reach, cached.

    Returns (levels, why) so the run's log can say whether these came off the
    wire or off disk. A failed fetch returns whatever the cache holds rather
    than nothing: the wire moving slowly is the whole reason this is cacheable,
    and a stale line beats pricing a streamer as an owner.
    """
    rank = rank or max(1, int(os.environ.get(RANK_VAR) or 1))
    path = Path(path)
    cached, age = None, None
    try:
        doc = json.loads(path.read_text())
        if doc.get("season") == season and doc.get("rank") == rank:
            cached = {int(w): v for w, v in doc["levels"].items()}
            age = now - float(doc["fetched_at"])
    except (OSError, ValueError, KeyError, TypeError):
        cached = None

    want = set(weeks)
    if cached is not None and age is not None and age < ttl and want <= set(cached):
        return cached, f"cached {age / 3600:.1f}h old"

    levels: dict[int, dict[str, float]] = {}
    try:
        for w in weeks:
            row = {}
            for pos in STREAMABLE:
                v = _fetch_week(season, w, pos, rank)
                if v is not None:
                    row[pos] = v
            if row:
                levels[w] = row
    except Exception as exc:                      # noqa: BLE001 - see docstring
        if cached:
            return cached, f"fetch failed ({type(exc).__name__}), using cache"
        return {}, f"fetch failed ({type(exc).__name__}), no cache"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"season": season, "rank": rank, "fetched_at": now,
         "levels": {str(w): v for w, v in levels.items()}}, indent=1))
    return levels, f"refetched rank {rank}"
