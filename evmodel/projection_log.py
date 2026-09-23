"""Does the matchup layer actually help? Record the predictions, score them later.

The claim that Sleeper's week-to-week shape improves anything is, right now,
unproven. The test that would settle it has to compare, on the horizon the model
actually uses (one to eleven weeks out) and on the quantity it actually uses
(a player's deviation from his own average week):

    flat    what we would project with no matchup information at all
    shaped  the same number multiplied by Sleeper's factor

against what the player really scored. Both are recorded BEFORE the week is
played, so there is no question of hindsight creeping in — which is exactly the
objection that sank the first attempt at measuring this, where ESPN's retained
projections for completed weeks could not be shown to be as-of-kickoff.

One snapshot per week made, so each target week accumulates predictions at
several horizons and the decay is visible. Scoring happens separately, once the
weeks land (`scripts/score_projections.py`).

    {"season": 2026,
     "snapshots": [{"made": 3, "at": epoch,
                    "p": {"<player_id>": {"<target_week>": [flat, shaped]}}}]}
"""

from __future__ import annotations

import json
from pathlib import Path


def empty(season: int) -> dict:
    return {"season": season, "snapshots": []}


def load(path: str | Path, season: int) -> dict:
    p = Path(path)
    if not p.exists():
        return empty(season)
    data = json.loads(p.read_text())
    return data if data.get("season") == season else empty(season)


def save(log: dict, path: str | Path) -> None:
    Path(path).write_text(json.dumps(log, separators=(",", ":")))


def already_logged(log: dict, made_week: int) -> bool:
    return any(s["made"] == made_week for s in log["snapshots"])


def snapshot(players: list[dict], weeks: list[int], scale: float) -> dict:
    """flat vs shaped for every player and every future week, as of now."""
    rows = {}
    for p in players:
        factors = p.get("factors") or {}
        per_week = {}
        for w in weeks:
            flat = float(p["rate"]) * scale
            if flat <= 0:
                continue
            shaped = flat * float(factors.get(w, 1.0))
            per_week[str(w)] = [round(flat, 2), round(shaped, 2)]
        if per_week:
            rows[str(p["player_id"])] = per_week
    return rows


def record(log: dict, made_week: int, at: int, players: list[dict],
           weeks: list[int], scale: float) -> bool:
    """Append this week's predictions. Returns False if the week is already in."""
    if already_logged(log, made_week):
        return False
    log["snapshots"].append({"made": made_week, "at": at,
                             "p": snapshot(players, weeks, scale)})
    log["snapshots"].sort(key=lambda s: s["made"])
    return True


def score(log: dict, actuals: dict[tuple[int, int], float]) -> dict:
    """Grade both columns against reality.

    `actuals` maps (player_id, week) -> points actually scored. Returns overall
    and per-horizon error for each, plus how often the shaped number landed
    closer than the flat one — which is the question in its plainest form.
    """
    by_h: dict[int, dict] = {}
    tot = {"n": 0, "flat": 0.0, "shaped": 0.0, "shaped_closer": 0}
    for snap in log["snapshots"]:
        for pid, weeks in snap["p"].items():
            for w, (flat, shaped) in weeks.items():
                key = (int(pid), int(w))
                if key not in actuals:
                    continue
                act = actuals[key]
                h = int(w) - snap["made"]
                b = by_h.setdefault(h, {"n": 0, "flat": 0.0, "shaped": 0.0,
                                        "shaped_closer": 0})
                ef, es = abs(flat - act), abs(shaped - act)
                for d in (b, tot):
                    d["n"] += 1
                    d["flat"] += ef
                    d["shaped"] += es
                    d["shaped_closer"] += 1 if es < ef else 0

    def finish(d):
        if not d["n"]:
            return None
        return {"n": d["n"], "mae_flat": round(d["flat"]/d["n"], 3),
                "mae_shaped": round(d["shaped"]/d["n"], 3),
                "shaped_better_pct": round(d["shaped_closer"]/d["n"], 3),
                "gain": round((d["flat"] - d["shaped"])/d["n"], 3)}

    return {"overall": finish(tot),
            "by_horizon": {h: finish(b) for h, b in sorted(by_h.items()) if finish(b)}}
