"""The tape — every team's EV over time, so a season reads like a stock chart.

A single columnar file (`ev_history_2026.json`): one timestamp array, and per
team one array per series. Columnar because the alternative — a list of objects
— repeats twelve teams' worth of keys on every tick and turns a season of
15-minute snapshots into megabytes a phone has to download.

    {"season": 2026,
     "at":    [epoch_seconds, ...],          # one per snapshot
     "week":  [3, 3, 4, ...],
     "series": {"ev": {"9": [...]}, "champ": {...}, "shame": {...},
                "prior": {...}, "wins": {...}}}

Two rules keep it small and honest:
  * a snapshot is only appended when something MOVED (or enough time passed that
    the line would otherwise lie about being flat)
  * snapshots older than KEEP_DENSE_DAYS are thinned to one a day, so the recent
    tape stays minute-by-minute while the season keeps its shape
"""

from __future__ import annotations

import json
from pathlib import Path

SERIES = ("ev", "champ", "shame", "prior", "wins")
KEEP_DENSE_DAYS = 14
DAY = 86400
# Append even when nothing moved, so a quiet week still draws a line.
HEARTBEAT_SECONDS = 6 * 3600
# What counts as movement, in dollars of EV, for any single team.
MOVE_USD = 2.0


def empty(season: int) -> dict:
    # `recon` marks points the model reconstructed after the fact (what it WOULD
    # have said at the end of week N, using today's projections) rather than
    # recorded live. The app draws those dimmed — they are a reconstruction, not
    # a measurement, and the difference matters.
    return {"season": season, "at": [], "week": [], "recon": [],
            "series": {k: {} for k in SERIES}}


def load(path: str | Path, season: int) -> dict:
    p = Path(path)
    if not p.exists():
        return empty(season)
    data = json.loads(p.read_text())
    if data.get("season") != season:          # new season, new tape
        return empty(season)
    for k in SERIES:
        data.setdefault("series", {}).setdefault(k, {})
    data.setdefault("recon", [0] * len(data.get("at", [])))
    return data


def snapshot(result: dict, params: dict) -> dict:
    """One tick: what every team is worth right now."""
    priors = {t["team_id"]: t.get("prior_playoff", 0.0) for t in params["teams"]}
    return {tid: {
        "ev": round(t["ev_usd"], 1),
        "champ": round(t["p_champ"], 4),
        "shame": round(t["p_shame"], 4),
        "prior": round(priors.get(t["team_id"], 0.0), 1),
        "wins": round(t["exp_wins"], 2),
    } for tid, t in ((t["team_id"], t) for t in result["teams"])}


def moved(hist: dict, tick: dict, at: int) -> bool:
    """Is this tick worth recording?"""
    if not hist["at"]:
        return True
    if at - hist["at"][-1] >= HEARTBEAT_SECONDS:
        return True
    ev = hist["series"]["ev"]
    for tid, vals in tick.items():
        prev = ev.get(str(tid))
        if not prev or abs(vals["ev"] - prev[-1]) >= MOVE_USD:
            return True
    return False


def append(hist: dict, tick: dict, at: int, week: int, recon: bool = False) -> dict:
    n = len(hist["at"])
    hist["at"].append(at)
    hist["week"].append(week)
    hist["recon"].append(1 if recon else 0)
    for name in SERIES:
        col = hist["series"][name]
        for tid, vals in tick.items():
            key = str(tid)
            arr = col.setdefault(key, [])
            while len(arr) < n:               # a team that joined late lines up
                arr.append(None)
            arr.append(vals[name])
        for key, arr in col.items():          # and one that vanished stays aligned
            while len(arr) < n + 1:
                arr.append(arr[-1] if arr else None)
    return hist


def compact(hist: dict, now: int) -> dict:
    """Thin everything older than KEEP_DENSE_DAYS to one snapshot a day."""
    cutoff = now - KEEP_DENSE_DAYS * DAY
    keep, seen_days = [], set()
    for i, at in enumerate(hist["at"]):
        if at >= cutoff:
            keep.append(i)
            continue
        day = at // DAY
        if day not in seen_days:              # the first tick of that day
            seen_days.add(day)
            keep.append(i)
    if len(keep) == len(hist["at"]):
        return hist
    hist["at"] = [hist["at"][i] for i in keep]
    hist["week"] = [hist["week"][i] for i in keep]
    hist["recon"] = [hist["recon"][i] for i in keep]
    for name in SERIES:
        col = hist["series"][name]
        for key, arr in col.items():
            col[key] = [arr[i] for i in keep if i < len(arr)]
    return hist


def sort_by_time(hist: dict) -> dict:
    """Keep the tape in order — a backfill lands before ticks already recorded."""
    order = sorted(range(len(hist["at"])), key=lambda i: hist["at"][i])
    if order == list(range(len(hist["at"]))):
        return hist
    hist["at"] = [hist["at"][i] for i in order]
    hist["week"] = [hist["week"][i] for i in order]
    hist["recon"] = [hist["recon"][i] for i in order]
    for name in SERIES:
        col = hist["series"][name]
        for key, arr in col.items():
            col[key] = [arr[i] for i in order if i < len(arr)]
    return hist


def save(hist: dict, path: str | Path) -> None:
    Path(path).write_text(json.dumps(hist, separators=(",", ":")))


def delta(hist: dict, team_id: int, since: int, series: str = "ev") -> float | None:
    """Change in a series since `since` (epoch seconds) — the app's '+$40 today'."""
    col = hist["series"].get(series, {}).get(str(team_id))
    if not col or not hist["at"]:
        return None
    base = None
    for at, v in zip(hist["at"], col):
        if at <= since and v is not None:
            base = v
    if base is None:
        base = next((v for v in col if v is not None), None)
    last = next((v for v in reversed(col) if v is not None), None)
    if base is None or last is None:
        return None
    return last - base
