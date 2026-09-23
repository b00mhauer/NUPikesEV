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
    """What this player is worth in this week.

    ESPN's own projection wins where it exists (the current week only). Beyond
    that it is his rest-of-season rate, lifted onto the weekly scale and then
    shaped by the matchup — `factors` from `sleeper`, which default to 1.0 and
    therefore to the flat rate this model used before they existed.
    """
    if week in p["weekly"]:
        return float(p["weekly"][week])
    factor = float(p.get("factors", {}).get(week, 1.0))
    return float(p["rate"]) * scale * factor


# K and D/ST are rostered almost exactly one per team, so a rank-12 line at
# those positions lands on the WORST owned player in the league — not a
# replacement line at all. (config.REPLACEMENT_RANK works for RB/WR because
# teams carry ~4.5 each, putting rank 30 mid-pool.) Two things say the streamed
# line belongs near the middle of the pool instead: the position barely predicts
# itself week to week (own-scoring split-half r = 0.11 D/ST, 0.20 K, against
# 0.30-0.36 for QB/RB/WR), so one of them is close to interchangeable with
# another; and measured over 3,854 pickup-starts, 2018-2025, a streamed D/ST
# scored 8.1 and a streamed K 8.2 — the same as league-wide ROSTERED starters at
# those positions (8.1 and 8.4). Rank 12 priced a D/ST hole at 5.5 against a 7.0
# projection for starters, i.e. a hole was cheaper than reality by ~1.5/week.
STREAMED_AT_MEDIAN = ("K", "DST")


def replacement_levels(all_players: list[dict]) -> dict[str, float]:
    """The weekly line a manager can stream into a hole, per position.

    For the positions the board ranks in (config.REPLACEMENT_RANK) this is the
    last startable player league-wide. For K/D/ST — rostered ~1 per team, and
    close to interchangeable — it is the median of the rostered pool; see
    STREAMED_AT_MEDIAN above for the evidence."""
    out = {}
    for pos in list(config.REPLACEMENT_RANK) + list(STREAMED_AT_MEDIAN):
        pool = sorted((p["rate"] for p in all_players if p["pos"] == pos), reverse=True)
        if not pool:
            out[pos] = 0.0
            continue
        rank = (len(pool) + 1) // 2 if pos in STREAMED_AT_MEDIAN \
            else config.REPLACEMENT_RANK[pos]
        out[pos] = pool[min(rank, len(pool)) - 1]
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


def replacement_by_week(all_players: list[dict], weeks: list[int],
                        current_week: int) -> dict[int, dict[str, float]]:
    """The streaming line, recomputed for each week's PLAYABLE pool.

    A bye is usually the reason a slot is empty in the first place, and the same
    bye thins the pool you would stream from — so a season-long line reads too
    generous in exactly the weeks it gets used. Measured on the live league, a
    static K line sat 0.3-0.9 above that week's playable median in 8 of 12
    remaining weeks."""
    return {w: replacement_levels([p for p in all_players
                                   if espn_live.playable(p, w, current_week)])
            for w in weeks}


def weekly_priors(players: list[dict], weeks: list[int], current_week: int,
                  replacement: dict[int, dict[str, float]],
                  scale: float = 1.0) -> dict[int, float]:
    """`replacement` is per-week (see replacement_by_week)."""
    return {w: lineup(players, w, current_week, replacement[w], scale=scale)[0]
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
