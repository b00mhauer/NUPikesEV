"""The matchup layer: it shapes weeks without moving the level, defences match on
the team code rather than a name, and anything it cannot match falls back to
exactly the behaviour we had before it existed."""

import pytest

from evmodel import config, roster_strength, sleeper


def row(first, last, pos, pts, team=None):
    return {"player": {"first_name": first, "last_name": last, "position": pos,
                       "team": team}, "stats": {"pts_std": pts}}


def player(pid, name, pos, rate, pro_team=1, factors=None):
    return {"player_id": pid, "name": name, "pos": pos, "rate": rate,
            "pro_team": pro_team, "weekly": {}, "bye": 0, "status": "ACTIVE",
            "on_ir": False, "starting": True, "factors": factors or {}}


ABBREV = {1: "ATL", 2: "JAC", 3: "BUF"}


def test_names_normalise_across_the_two_sources():
    assert sleeper.norm("Amon-Ra St. Brown") == "amon ra st brown"
    assert sleeper.norm("Michael Pittman Jr.") == sleeper.norm("Michael Pittman")
    assert sleeper.norm("Ka'imi Fairbairn") == "kaimi fairbairn"


def test_defences_index_on_team_code_not_name():
    """Every unmatched player in the live league was a D/ST, because ESPN says
    'Texans D/ST' and Sleeper says nothing at all — only a team."""
    skill, dst = sleeper.index([
        row("Bijan", "Robinson", "RB", 18.4),
        row("Atlanta", "Falcons", "DEF", 6.3, team="ATL"),
        row("Jacksonville", "Jaguars", "DEF", 7.1, team="JAX"),
    ])
    assert skill[("bijan robinson", "RB")] == 18.4
    assert dst["ATL"] == 6.3
    assert dst["JAX"] == 7.1                      # and ESPN's "JAC" aliases onto it


def test_a_factor_is_a_shape_not_a_level():
    """The whole point: Sleeper's scoring never reaches the model, only its
    week-to-week ratios, so its level can differ from ours without harm."""
    by_week = {4: ({("a back", "RB"): 10.0}, {}),
               5: ({("a back", "RB"): 20.0}, {}),
               6: ({("a back", "RB"): 30.0}, {})}
    f, rep = sleeper.week_factors([player(1, "A Back", "RB", 12.0)], 2026,
                                  [4, 5, 6], ABBREV, by_week=by_week)
    assert f[1][4] == pytest.approx(0.55)          # 10/20, clamped at the floor
    assert f[1][5] == pytest.approx(1.0)
    assert f[1][6] == pytest.approx(1.5)
    assert rep["coverage"] == 1.0

    # doubling every Sleeper number changes nothing — it is a ratio
    doubled = {w: ({k: v * 2 for k, v in s.items()}, d) for w, (s, d) in by_week.items()}
    g, _ = sleeper.week_factors([player(1, "A Back", "RB", 12.0)], 2026,
                                [4, 5, 6], ABBREV, by_week=doubled)
    assert g[1] == f[1]


def test_a_bye_does_not_drag_the_average_down():
    """Sleeper omits a player on his bye. If that counted as a zero, every other
    week would be inflated to compensate."""
    by_week = {4: ({("a back", "RB"): 12.0}, {}),
               5: ({}, {}),                        # bye: absent, not zero
               6: ({("a back", "RB"): 12.0}, {})}
    f, _ = sleeper.week_factors([player(1, "A Back", "RB", 12.0)], 2026,
                                [4, 5, 6], ABBREV, by_week=by_week)
    assert f[1] == {4: 1.0, 6: 1.0}
    assert 5 not in f[1]



def test_one_week_of_data_is_not_a_shape():
    by_week = {4: ({("a back", "RB"): 12.0}, {}), 5: ({}, {})}
    f, _ = sleeper.week_factors([player(1, "A Back", "RB", 12.0)], 2026,
                                [4, 5], ABBREV, by_week=by_week)
    assert f == {}, "a single week says nothing about week-to-week shape"


def test_wild_factors_are_clamped_not_propagated():
    by_week = {4: ({("a back", "RB"): 1.0}, {}), 5: ({("a back", "RB"): 99.0}, {})}
    f, _ = sleeper.week_factors([player(1, "A Back", "RB", 12.0)], 2026,
                                [4, 5], ABBREV, by_week=by_week)
    lo, hi = sleeper.FACTOR_BOUNDS
    assert all(lo <= v <= hi for v in f[1].values())



def test_the_shape_moves_a_lineup_but_not_by_much():
    rep = {"QB": 10.0, "RB": 6.0, "WR": 5.0, "TE": 4.0, "K": 6.0, "DST": 5.0}
    roster = [player(i, f"P{i}", pos, 12.0)
              for i, pos in enumerate(["QB","RB","RB","WR","WR","TE","K","DST","RB"])]
    flat = roster_strength.lineup(roster, 5, 3, rep)[0]
    for p in roster:
        p["factors"] = {5: 1.1}
    shaped = roster_strength.lineup(roster, 5, 3, rep)[0]
    assert shaped == pytest.approx(flat * 1.1)


# --- the source switch -------------------------------------------------------

def test_sleeper_lines_are_scored_under_our_rules_not_theirs():
    """The whole point of taking Sleeper's raw stat line: our -1 per sack and our
    lack of PPR make their own total wrong for us by several points a week."""
    line = {"pass_yd": 250.0, "pass_td": 2.0, "pass_int": 1.0, "pass_sack": 3.0,
            "rush_yd": 30.0, "rush_td": 0.5, "rec": 8.0, "pts_ppr": 99.0}
    got = sleeper.score_line(line)
    want = 250*0.04 + 2*5.0 + 1*(-2.0) + 3*(-1.0) + 30*0.1 + 0.5*6.0
    assert got == pytest.approx(want)
    assert got != 99.0                      # their number is never used as a level


def test_the_preseason_rate_never_reaches_a_lineup():
    """ESPN's season projection is a preseason figure it does not revise: Jaxson
    Dart, ruled OUT, carries a 236.6 season total while ESPN's own week number
    for him is 0.00. Spreading that across the weeks ESPN does not publish --
    every week but the current one -- is what this guards against."""
    stale = {"weekly": {}, "rate": 99.0, "factors": {}, "sleeper": {}}
    assert roster_strength.player_week(stale, 8, 1.13) == 0.0
    live = dict(stale, sleeper={8: 12.0})
    assert roster_strength.player_week(live, 8, 1.13) == pytest.approx(12.0)


def test_espn_wins_the_week_it_actually_publishes():
    """Its weekly figure IS maintained -- it is the season total that is not."""
    p = {"weekly": {3: 17.5}, "rate": 99.0, "factors": {}, "sleeper": {3: 11.0}}
    try:
        config.PROJECTION_SOURCE = "espn"
        assert roster_strength.player_week(p, 3, 1.13) == pytest.approx(17.5)
        config.PROJECTION_SOURCE = "sleeper"
        assert roster_strength.player_week(p, 3, 1.13) == pytest.approx(11.0)
        config.PROJECTION_SOURCE = "blend"
        assert roster_strength.player_week(p, 3, 1.13) == pytest.approx(14.25)
    finally:
        config.PROJECTION_SOURCE = "blend"


def test_a_week_sleeper_skipped_uses_that_players_own_average():
    """A bye or an unposted week, not an unknown player -- his other weeks are
    better evidence than anything we could invent."""
    p = {"weekly": {}, "rate": 99.0, "factors": {}, "sleeper": {4: 10.0, 5: 14.0}}
    assert roster_strength.player_week(p, 9, 1.13) == pytest.approx(12.0)


def test_a_player_neither_source_prices_falls_to_zero_not_to_august():
    """Zero makes the optimizer skip him and price the slot at the streaming
    line, which is the honest treatment of no information."""
    p = {"weekly": {}, "rate": 88.0, "factors": {}, "sleeper": {}}
    for src in ("espn", "sleeper", "blend"):
        config.PROJECTION_SOURCE = src
        assert roster_strength.player_week(p, 12, 1.13) == 0.0
    config.PROJECTION_SOURCE = "blend"


def test_kickers_are_not_silently_zeroed():
    """STAT_POINTS holds the offensive stat ids only -- no FG, no XP -- so running
    a kicker's line through score_line() returns 0.0 for every kicker alive. Under
    the live-only model that is not a rounding error: it would zero the K slot in
    every week ESPN does not publish, i.e. the whole rest of the season."""
    rows = [{"player": {"first_name": "Foot", "last_name": "Baller",
                        "position": "K", "team": "BUF"},
             "stats": {"pts_std": 9.2, "pts_ppr": 9.2, "fgm": 2.0, "xpm": 3.0}}]
    skill, dst = sleeper.index_points(rows)
    assert skill[(sleeper.norm("Foot Baller"), "K")] == pytest.approx(9.2)
    assert sleeper.score_line(rows[0]["stats"]) == 0.0    # the trap this guards


def test_the_full_strength_sentinel_blends_both_sources_too():
    """Week 99 is no real week -- it is the optimizer's "no byes, no absences"
    sentinel behind the full-strength lineup and the letter grades. Before ESPN's
    forward grid existed, nothing answered for it but Sleeper, so the graded board
    and the EV board quietly believed different sources. Both now average their
    own weeks and combine under the same rule.

    ESPN's zeros are dropped: it writes 0.0 for a bye and for a week it expects a
    player to miss, while Sleeper simply omits those weeks. Averaging the zeros in
    would answer a different question than "what is he worth at full strength".
    """
    p = {"weekly": {4: 20.0, 5: 0.0, 6: 20.0},      # 0.0 is his bye
         "sleeper": {4: 10.0, 6: 10.0}, "rate": 99.0, "factors": {}}
    try:
        config.PROJECTION_SOURCE = "espn"
        assert roster_strength.player_week(p, 99) == pytest.approx(20.0)
        config.PROJECTION_SOURCE = "sleeper"
        assert roster_strength.player_week(p, 99) == pytest.approx(10.0)
        config.PROJECTION_SOURCE = "blend"
        assert roster_strength.player_week(p, 99) == pytest.approx(15.0)
    finally:
        config.PROJECTION_SOURCE = "blend"
