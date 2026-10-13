"""Exit 0 if an NFL game is on the field right now, 1 if not.

The publish workflow uses this to decide whether a run should keep re-pricing
every five minutes (games in progress) or price once and go home. It is a
separate script rather than a python -c in the YAML because the thing it
replaces was a backslash-continued one-liner that could only run AFTER the first
sweep -- it read data/params.json, which is gitignored and does not exist on a
fresh runner.

Prints a one-line reason either way, so the run log says why it chose.
"""

from __future__ import annotations

import datetime
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evmodel import espn_live  # noqa: E402


def main() -> int:
    now = int(time.time() * 1000)
    raw = espn_live.fetch()
    week = espn_live.current_week(raw)
    kicks = sorted(k for (_t, w), k in espn_live.kickoffs(raw["proteams"]).items()
                   if w == week)
    if espn_live.games_in_play(raw, now):
        print(f"week {week}: a game is in play -> sweep")
        return 0
    if kicks:
        last = (now - kicks[-1]) / 3600000
        when = ("the last kickoff was %.1fh ago" % last if last > 0
                else "the next kickoff is in %.1fh" % -last)
        print(f"week {week}: nothing on the field ({when}) -> one pass")
    else:
        print(f"week {week}: no kickoffs known -> one pass")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
