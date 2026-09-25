"""The management overlay: one multiplier per team, applied to what the model
says that team is worth.

The model prices rosters. It cannot price managers -- R15 found that Glowacki
and Kennedy convert waiver pickups into started points at a rate the projection
layer never sees, and that our own wire activity runs at half Kennedy's volume.
This is where a view like that gets expressed, as a number rather than a nudge
to the projections themselves.

Two properties make it safe to leave switched on:

  * It is RELATIVE. The factors are renormalised so the league's total points
    per week come out exactly where they started, which keeps the calibration
    scale (fitted against what the league actually scores) valid. Bias one team
    up and the rest give way; nobody can inflate the league by mistake.

  * Uniform is identity. All 1.0 is off. So is all 1.5, or all 0.8 -- only the
    ratios between teams survive normalisation, so there is no wrong way to
    switch it off and no drift from a careless edit.

The overlay is read from the environment, never from the repository, so the
published numbers carry it without the file that defines it ever being
committed. An empty or unset overlay is exactly a no-op.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

ENV_VAR = "EV_TEAM_BIAS"


def load(blob: str | None = None, path: Path | None = None) -> dict[str, float]:
    """The overlay, from a JSON string (or $EV_TEAM_BIAS), else a local file.

    Shape: {"Parrott": 0.98, "Glowacki": 1.02} -- keys match an owner's name the
    way OUR_OWNER does, case-insensitively and by substring, so surnames are
    enough. Anything unparseable is treated as no overlay at all: a typo in a
    secret must not take the site down mid-season.
    """
    text = blob if blob is not None else os.environ.get(ENV_VAR)
    if not text and path is not None and Path(path).exists():
        text = Path(path).read_text()
    if not text or not text.strip():
        return {}
    try:
        raw = json.loads(text)
        return {str(k): float(v) for k, v in raw.items() if float(v) > 0}
    except (ValueError, TypeError, AttributeError):
        return {}


def resolve(bias: dict[str, float], teams: list[dict]) -> dict[int, float]:
    """Owner-name keys -> team_id factors. Teams nobody named sit at 1.0.

    A key that matches more than one owner is a mistake worth stopping for:
    silently biasing two teams is worse than a failed build.
    """
    out = {t["team_id"]: 1.0 for t in teams}
    for key, factor in bias.items():
        hit = [t for t in teams if key.lower() in str(t.get("owner", "")).lower()]
        if len(hit) > 1:
            raise ValueError(
                f"bias key {key!r} matches {len(hit)} owners: "
                + ", ".join(t["owner"] for t in hit))
        if hit:
            out[hit[0]["team_id"]] = factor
    return out


def normalise(factors: dict[int, float], levels: dict[int, float]) -> dict[int, float]:
    """Rescale so the league's total level is exactly preserved.

        f_i = b_i * (sum_j L_j) / (sum_j L_j * b_j)

    `levels` is each team's current points-per-week, so a strong team's bias
    moves more of the league's total than a weak one's and the sum still lands
    where it started. With every b equal this returns 1.0 for everyone, which is
    what makes "all the same" the off switch.
    """
    total = sum(levels.get(tid, 0.0) for tid in factors)
    weighted = sum(levels.get(tid, 0.0) * factors[tid] for tid in factors)
    if total <= 0 or weighted <= 0:
        return {tid: 1.0 for tid in factors}
    k = total / weighted
    return {tid: f * k for tid, f in factors.items()}


def describe(factors: dict[int, float], teams: list[dict]) -> str:
    """One line for the build log. Says nothing when the overlay is off, so a
    normal run's output does not advertise that the machinery exists."""
    named = {t["team_id"]: t.get("abbrev") or t.get("owner") for t in teams}
    moved = {tid: f for tid, f in factors.items() if abs(f - 1.0) > 0.0005}
    if not moved:
        return ""
    return "overlay: " + ", ".join(
        f"{named.get(tid, tid)} {(f - 1) * 100:+.1f}%"
        for tid, f in sorted(moved.items(), key=lambda kv: -kv[1]))
