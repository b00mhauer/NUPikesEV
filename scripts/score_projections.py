"""Grade the matchup layer against what actually happened.

Reads the forward log (predictions recorded before each week was played) and the
real weekly scores from ESPN, then reports whether the shaped number beat the
flat one — overall and by how far ahead the prediction was made.

Until several weeks have landed this will say "not enough yet", which is the
correct answer rather than a number worth quoting.

    python scripts/score_projections.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from evmodel import config, espn_live, projection_log  # noqa: E402

FORWARD_LOG = REPO / "data" / "projection_log.json"

MIN_SAMPLE = 200


def actuals(season: int) -> dict[tuple[int, int], float]:
    """(player_id, week) -> points actually scored, in this league's scoring."""
    raw = espn_live.get("mRoster", season)
    out = {}
    for team in raw.get("teams", []):
        for e in team.get("roster", {}).get("entries", []):
            p = e["playerPoolEntry"]["player"]
            for s in p.get("stats", []):
                if (s.get("statSourceId") == 0 and s.get("statSplitTypeId") == 1
                        and s.get("seasonId") == season
                        and s.get("appliedTotal") is not None):
                    out[(p["id"], s["scoringPeriodId"])] = float(s["appliedTotal"])
    return out


def main() -> None:
    season = config.CURRENT_SEASON
    log = projection_log.load(FORWARD_LOG, season)
    if not log["snapshots"]:
        raise SystemExit("nothing logged yet — run the export first")

    report = projection_log.score(log, actuals(season))
    o = report["overall"]
    if not o:
        raise SystemExit("predictions are logged but none of those weeks have been played")

    print(f"forward test, {season} — predictions made before the week, scored after\n")
    print(f"  sample: {o['n']} player-weeks from {len(log['snapshots'])} snapshot(s)")
    print(f"  flat   MAE {o['mae_flat']:.2f}")
    print(f"  shaped MAE {o['mae_shaped']:.2f}   ({o['gain']:+.2f} per player-week)")
    print(f"  shaped landed closer {o['shaped_better_pct']:.0%} of the time\n")

    if o["n"] < MIN_SAMPLE:
        print(f"  NOT ENOUGH YET (want {MIN_SAMPLE}+). Do not quote this.")
    elif abs(o["gain"]) < 0.05:
        print("  verdict: no measurable difference. The layer is not earning its keep.")
    elif o["gain"] > 0:
        print("  verdict: the matchup shape helps.")
    else:
        print("  verdict: the matchup shape HURTS — it should come out.")

    if report["by_horizon"]:
        print("\n  by how far ahead the call was made:")
        print(f"    {'weeks out':>10}{'n':>7}{'flat':>8}{'shaped':>8}{'gain':>8}{'better':>8}")
        for h, b in report["by_horizon"].items():
            print(f"    {h:>10}{b['n']:>7}{b['mae_flat']:>8.2f}{b['mae_shaped']:>8.2f}"
                  f"{b['gain']:>+8.2f}{b['shaped_better_pct']:>8.0%}")


if __name__ == "__main__":
    main()
