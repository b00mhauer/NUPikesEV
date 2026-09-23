"""Record one tick of the tape — every team's EV, right now.

Runs the model on the current params and appends to `data/ev_history.json` when
something has actually moved (or enough time has passed that a flat line would be
a lie). It is the only file here that cannot be rebuilt from ESPN, so it is the
one thing committed back to the repo.

Usage:
  python scripts/record_snapshot.py               # export fresh, then record
  python scripts/record_snapshot.py --no-export   # record off the params on disk
  python scripts/record_snapshot.py --backfill    # reconstruct completed weeks
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from evmodel import config, espn_live, ev_history, season_sim  # noqa: E402

PARAMS = REPO / "data" / "params.json"
TAPE = REPO / "data" / "ev_history.json"
SIMS = 20000
SEED = 20260101


def load_tape(season: int) -> dict:
    return ev_history.load(TAPE, season)


def save_tape(hist: dict) -> None:
    TAPE.parent.mkdir(parents=True, exist_ok=True)
    ev_history.save(hist, TAPE)


def week_end(params: dict, week: int) -> int:
    """When week N stopped mattering: three hours after its last kickoff."""
    ko = espn_live.kickoffs(espn_live.get(
        "proTeamSchedules_wl", params["season"],
        url=f"{config.READ_BASE}/seasons/{params['season']}"))
    last = max((ts for (_, w), ts in ko.items() if w == week), default=None)
    return int((last + espn_live.GAME_MS) / 1000) if last else int(time.time())


def backfill(params: dict, hist: dict, sims: int) -> int:
    """One reconstructed point per completed week, so the tape has a shape on day
    one. Rosters and projections are TODAY's — only results roll back — so these
    are marked `recon` and the app draws them dimmed."""
    added = 0
    for week in [w["week"] for w in params["weeks"] if w["state"] == "final"]:
        at = week_end(params, week)
        if at in hist["at"]:
            continue
        rolled = dict(params, weeks=[w if w["week"] <= week else dict(w, state="future")
                                     for w in params["weeks"]])
        r = season_sim.simulate(rolled, sims, seed=SEED)
        ev_history.append(hist, ev_history.snapshot(r, params), at, week, recon=True)
        added += 1
    return added


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--season", type=int, default=config.CURRENT_SEASON)
    ap.add_argument("--no-export", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--sims", type=int, default=SIMS)
    args = ap.parse_args()

    if not args.no_export:
        subprocess.run([sys.executable, str(REPO / "scripts/export_params.py"),
                        "--season", str(args.season)], cwd=REPO, check=True)

    params = season_sim.load_params(PARAMS)
    result = season_sim.simulate(params, args.sims, seed=SEED)
    hist = load_tape(args.season)
    tick = ev_history.snapshot(result, params)
    now = int(time.time())

    # An empty tape seeds itself: the first run of the season (or the first run
    # ever) reconstructs the completed weeks so the chart has a shape on day one.
    want_backfill = args.backfill or not hist["at"]
    added = backfill(params, hist, args.sims) if want_backfill else 0
    recorded = args.force or ev_history.moved(hist, tick, now)
    if recorded:
        ev_history.append(hist, tick, now, params["current_week"])
    if recorded or added:
        ev_history.sort_by_time(hist)
        ev_history.compact(hist, now)
        save_tape(hist)

    # public logs: counts only, never a team, an owner or a dollar figure
    print(f"[tape] {'+1' if recorded else 'no move'}"
          f"{f' +{added} backfilled' if added else ''}  "
          f"{len(hist['at'])} points on file")


if __name__ == "__main__":
    main()
