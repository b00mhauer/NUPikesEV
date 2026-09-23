"""How good is this roster, this week — the EV model's prior.

Takes the tidy players from `espn_live` and answers one question per team per
week: what does its best legal lineup project to score? That is the number the
season simulator draws around, so everything that makes a roster better or worse
— a trade, a waiver add, a bye, a torn ACL — reaches the money through here.

The lineup it must fill:
  QB · RB RB · WR WR · RB/WR flex · TE · K · D/ST
A player who cannot play that week (bye, IR, ruled out) is simply not available,
and the optimizer falls through to the next man; if the roster genuinely cannot
fill a slot, the hole is priced at replacement level — what streaming costs you.
"""

from __future__ import annotations

from . import config, espn_live

STARTERS = [("QB", 1), ("RB", 2), ("WR", 2), ("TE", 1), ("K", 1), ("DST", 1)]
FLEX = ("RB", "WR")
# How hard this season's own scoring is allowed to pull the projection level.
# ESPN's numbers are already in league scoring, so this is a gentle correction
# that earns trust as weeks accumulate rather than over-fitting two games.
CALIBRATION_PRIOR_WEEKS = 4


# ESPN publishes two different things and they are NOT on the same scale: a
# week projection is what a player scores IF HE STARTS, while the season
# projection (which the rest-of-season rate is cut from) already discounts the
# games it expects him to miss. Measured on the live league the gap is a flat
# ~1.13 at every position (QB 1.12, RB 1.13, WR 1.13, TE 1.17, K 1.15, DST 1.17)
# — a scale, not noise. Since the lineup optimizer handles absences explicitly
# (bye, ruled out, IR -> next man up), the conditional number is the coherent
# input, so the rate is lifted onto it. Without this, week 3 reads ~100 and week
# 5 reads ~81 for the same roster.
RATE_SCALE_BOUNDS = (1.0, 1.35)


def rate_scale(players: list[dict], week: int) -> float:
    """Median (week projection / rest-of-season rate) across the league."""
    ratios = sorted(p["weekly"][week] / p["rate"] for p in players
                    if week in p["weekly"] and p["rate"] > 3.0)
    if not ratios:
        return 1.0
    mid = ratios[len(ratios) // 2] if len(ratios) % 2 else \
        (ratios[len(ratios) // 2 - 1] + ratios[len(ratios) // 2]) / 2
    return min(max(mid, RATE_SCALE_BOUNDS[0]), RATE_SCALE_BOUNDS[1])


def player_week(p: dict, week: int, scale: float = 1.0) -> float:
    """ESPN's own projection for that week where it has published one, else his
    rest-of-season rate lifted onto the same (conditional) scale."""
    if week in p["weekly"]:
        return float(p["weekly"][week])
    return float(p["rate"]) * scale


def replacement_levels(all_players: list[dict]) -> dict[str, float]:
    """The weekly line of the last startable player at each position, league-wide
    — the same baseline the board ranks in (config.REPLACEMENT_RANK), which is
    roughly what a manager can stream into a hole."""
    out = {}
    ranks = dict(config.REPLACEMENT_RANK)
    ranks.setdefault("K", 12)
    ranks.setdefault("DST", 12)
    for pos, rank in ranks.items():
        pool = sorted((p["rate"] for p in all_players if p["pos"] == pos), reverse=True)
        out[pos] = pool[min(rank, len(pool)) - 1] if pool else 0.0
    return out


def lineup(players: list[dict], week: int, current_week: int,
           replacement: dict[str, float], ignore_bye: bool = False,
           scale: float = 1.0) -> tuple[float, list]:
    """Best legal lineup for one week -> (projected points, the names in it)."""
    avail: dict[str, list[tuple[float, str]]] = {}
    for p in players:
        if ignore_bye:
            if p["on_ir"] or p["status"] in espn_live.OUT_FOR_NOW:
                continue
        elif not espn_live.playable(p, week, current_week):
            continue
        avail.setdefault(p["pos"], []).append((player_week(p, week, scale), p["name"]))
    for pos in avail:
        avail[pos].sort(reverse=True)

    total, picked, used = 0.0, [], {}
    for pos, count in STARTERS:
        pool = avail.get(pos, [])
        for slot in range(count):
            if slot < len(pool):
                total += pool[slot][0]
                picked.append((pos, pool[slot][1]))
            else:
                total += replacement.get(pos, 0.0) * scale
                picked.append((pos, f"(streamed {pos})"))
        used[pos] = min(count, len(pool))

    rest = sorted([x for pos in FLEX for x in avail.get(pos, [])[used.get(pos, 0):]],
                  reverse=True)
    if rest:
        total += rest[0][0]
        picked.append(("FLEX", rest[0][1]))
    else:
        total += max(replacement["RB"], replacement["WR"]) * scale
        picked.append(("FLEX", "(streamed)"))
    return total, picked


def weekly_priors(players: list[dict], weeks: list[int], current_week: int,
                  replacement: dict[str, float], scale: float = 1.0) -> dict[int, float]:
    return {w: lineup(players, w, current_week, replacement, scale=scale)[0]
            for w in weeks}


def playoff_prior(players: list[dict], current_week: int,
                  replacement: dict[str, float], scale: float = 1.0) -> float:
    """Weeks 15-17: NFL byes are over, so the roster is at full strength."""
    return lineup(players, 99, current_week, replacement, ignore_bye=True, scale=scale)[0]


def calibration(priors: dict[int, dict[int, float]], finished: list[tuple[int, int, float]],
                fallback_mean: float = 93.4) -> tuple[float, str]:
    """One league-wide scale so the model's level matches what this league scores.

    `finished` is (team_id, week, points) for every completed team-week. The
    correction is shrunk toward 1 by how little has been played, so two games
    cannot yank the whole league's projection around.
    """
    weeks_played = len({w for _, w, _ in finished})
    if not finished:
        mean_projected = (sum(v for t in priors.values() for v in t.values())
                          / max(1, sum(len(t) for t in priors.values())))
        raw = fallback_mean / mean_projected if mean_projected else 1.0
        return raw, "no games played (2021-25 league average)"

    actual = sum(pts for _, _, pts in finished) / len(finished)
    projected = sum(priors[t][w] for t, w, _ in finished if w in priors.get(t, {}))
    n = sum(1 for t, w, _ in finished if w in priors.get(t, {}))
    if not n or not projected:
        return 1.0, "no overlap between projections and played weeks"
    raw = actual / (projected / n)
    shrunk = 1.0 + (raw - 1.0) * weeks_played / (weeks_played + CALIBRATION_PRIOR_WEEKS)
    return shrunk, (f"{weeks_played} played week(s) of this season "
                    f"(raw {raw:.3f}, shrunk toward 1)")
