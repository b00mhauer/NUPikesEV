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


def _mean(values) -> float | None:
    """Average, or None for an empty run -- None means "no opinion", not zero."""
    vals = list(values)
    return sum(vals) / len(vals) if vals else None


def _combine(espn: float, sleeper_: float) -> float:
    """The one place PROJECTION_SOURCE is applied, so every path agrees."""
    src = config.PROJECTION_SOURCE
    if src == "espn":
        return espn
    if src == "sleeper":
        return sleeper_
    w = config.BLEND_WEIGHTS
    tot = w["espn"] + w["sleeper"]
    return (w["espn"] * espn + w["sleeper"] * sleeper_) / tot if tot else espn


def player_week(p: dict, week: int, scale: float = 1.0) -> float:
    """What this player is worth in this week.

    NOTHING here reads p["rate"]. That number is ESPN's SEASON projection spread
    over the weeks a player can still play, and the season projection is a
    preseason figure that is never marked down: Jaxson Dart, ruled OUT, carries a
    236.6 season total while ESPN's own week projection for him reads 0.00.

    ESPN's WEEKLY projections are the opposite -- maintained all season, injuries
    and byes and all -- and they exist for every week, not just the current one.
    They only look scarce because the default payload carries the current week
    alone; ask for scoringPeriodId=N and week N comes back. espn_live.weekly_grid
    pulls the lot and export merges them into p["weekly"], so by the time a player
    reaches this function both sources usually have a live number for every week
    left in the season.

    The order: ESPN's week where it exists, then Sleeper's line for that week,
    then -- for a bye, an unposted week, or the playoff sentinel -- each source's
    own average over the weeks it does carry. Wherever both speak,
    config.PROJECTION_SOURCE picks "espn", "sleeper" or "blend". A player neither
    source can price falls through to 0.0, which makes the optimizer skip him and
    price the slot at replacement rather than at an August guess.
    """
    # FanDuel's contribution, and the only thing it is allowed to do: move the
    # level. A multiplier cannot resurrect a zero, so byes and the weeks a player
    # is expected to miss come through untouched. Absent or FD_WEIGHT=0 -> 1.0.
    fd = float(p.get("fd_scale") or 1.0)

    del scale       # vestigial: it lifted the season RATE onto the weekly scale,
                    # and nothing here reads the rate any more. Kept in the
                    # signature because every caller still threads it through.
    sl = p.get("sleeper") or {}
    espn_wk = p["weekly"].get(week)          # ESPN maintains this: current week only
    sleep_wk = sl.get(week)

    # Both sources have a live number for this week -> PROJECTION_SOURCE decides.
    if espn_wk is not None and sleep_wk is not None:
        return fd * (_combine(float(espn_wk), float(sleep_wk)))

    if espn_wk is not None:
        return fd * (float(espn_wk))
    if sleep_wk is not None:
        return fd * (float(sleep_wk))

    # Neither source prices THIS week. Either it is a bye, a week one of them has
    # not posted, or the caller asked for the playoff sentinel -- a week number no
    # real schedule has, meaning "what is he worth at full strength". Answer with
    # each source's own average over the weeks it does carry, then combine them the
    # same way a real week is combined, so the full-strength board and the weekly
    # board cannot disagree about which source they believe.
    #
    # Zeros are dropped from the ESPN side on purpose. ESPN writes 0.0 for a bye
    # and for a week it expects a player to miss; Sleeper just omits those weeks.
    # Averaging ESPN's zeros in would quietly answer a different question -- what
    # he is worth per CALENDAR week, absences included -- and would put a player
    # returning in week 7 below a replacement who plays every week, in a lineup
    # that is explicitly about full strength.
    espn_avg = _mean(v for v in p["weekly"].values() if v > 0)
    sleep_avg = _mean(sl.values())

    if espn_avg is not None and sleep_avg is not None:
        return fd * (_combine(espn_avg, sleep_avg))
    if espn_avg is not None:
        return fd * (espn_avg)
    if sleep_avg is not None:
        return fd * (sleep_avg)

    # Nothing current about him at all. 0.0 makes the optimizer skip him so the
    # slot is priced at the streaming line -- the honest treatment of no
    # information, and never a stale August total.
    return fd * (0.0)


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

# The optimizer's "no byes left" week: past the NFL schedule, so it never
# matches a published week and always falls through to the season-long view.
PLAYOFF_SENTINEL = 99


def replacement_levels(all_players: list[dict], week: int = PLAYOFF_SENTINEL,
                      scale: float = 1.0) -> dict[str, float]:
    """The weekly line a manager can stream into a hole, per position.

    For the positions the board ranks in (config.REPLACEMENT_RANK) this is the
    last startable player league-wide. For K/D/ST — rostered ~1 per team, and
    close to interchangeable — it is the median of the rostered pool; see
    STREAMED_AT_MEDIAN above for the evidence.

    Ranked by player_week(), the same live valuation the lineup uses, so the line
    and the players it is compared against are on one scale. It used to rank on
    p["rate"], which put the wire in ESPN's never-revised preseason space while
    the lineup moved to live weekly numbers."""
    out = {}
    for pos in list(config.REPLACEMENT_RANK) + list(STREAMED_AT_MEDIAN):
        pool = sorted((player_week(p, week, scale)
                       for p in all_players if p["pos"] == pos), reverse=True)
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
                total += replacement.get(pos, 0.0)
                picked.append((pos, f"(streamed {pos})"))
        used[pos] = min(count, len(pool))

    rest = sorted([x for pos in FLEX for x in avail.get(pos, [])[used.get(pos, 0):]],
                  reverse=True)
    if rest:
        total += rest[0][0]
        picked.append(("FLEX", rest[0][1]))
    else:
        total += max(replacement["RB"], replacement["WR"])
        picked.append(("FLEX", "(streamed)"))
    return total, picked


def replacement_by_week(all_players: list[dict], weeks: list[int],
                        current_week: int,
                        scale: float = 1.0) -> dict[int, dict[str, float]]:
    """The streaming line, recomputed for each week's PLAYABLE pool.

    A bye is usually the reason a slot is empty in the first place, and the same
    bye thins the pool you would stream from — so a season-long line reads too
    generous in exactly the weeks it gets used. Measured on the live league, a
    static K line sat 0.3-0.9 above that week's playable median in 8 of 12
    remaining weeks."""
    return {w: replacement_levels([p for p in all_players
                                   if espn_live.playable(p, w, current_week)],
                                  week=w, scale=scale)
            for w in weeks}


def weekly_priors(players: list[dict], weeks: list[int], current_week: int,
                  replacement: dict[int, dict[str, float]],
                  scale: float = 1.0) -> dict[int, float]:
    """`replacement` is per-week (see replacement_by_week)."""
    return {w: lineup(players, w, current_week, replacement[w], scale=scale)[0]
            for w in weeks}


PLAYOFF_WEEKS = (15, 16, 17)


def playoff_prior(players: list[dict], current_week: int,
                  replacement: dict[str, float], scale: float = 1.0,
                  weeks: tuple[int, ...] = PLAYOFF_WEEKS) -> float:
    """What a roster is worth per week once the bracket starts.

    This used to be the week-99 sentinel -- "full strength, no byes" -- because
    nothing had ever asked ESPN or Sleeper for weeks 15-17. Both carry them in
    full, so the three weeks the title is actually decided in are now priced
    from their own projections rather than from a stand-in: real matchups, real
    depth-chart changes, and whoever is expected back by December.

    Averaged rather than summed, because the simulator wants a per-week mean.
    Falls back to the sentinel when those weeks are missing, which keeps an old
    params file and any caller without the extended grid working unchanged.
    """
    real = [lineup(players, w, current_week, replacement, ignore_bye=True, scale=scale)[0]
            for w in weeks
            if any(w in (p.get("weekly") or {}) or w in (p.get("sleeper") or {})
                   for p in players)]
    if real:
        return sum(real) / len(real)
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
