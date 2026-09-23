"""The matchup layer: it shapes weeks without moving the level, defences match on
the team code rather than a name, and anything it cannot match falls back to
exactly the behaviour we had before it existed."""

import pytest

from evmodel import roster_strength, sleeper


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


def test_an_unmatched_player_falls_back_to_the_flat_rate():
    by_week = {4: ({}, {}), 5: ({}, {})}
    f, rep = sleeper.week_factors([player(1, "Nobody Known", "WR", 9.0)], 2026,
                                  [4, 5], ABBREV, by_week=by_week)
    assert f == {} and rep["coverage"] == 0.0
    p = player(1, "Nobody Known", "WR", 9.0)
    assert roster_strength.player_week(p, 5, scale=1.1) == pytest.approx(9.9)


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


def test_espn_still_wins_the_week_it_actually_projects():
    """ESPN publishes the current week and nothing else; where it speaks, it is
    the authority, because it is already in this league's scoring."""
    p = player(1, "A Back", "RB", 10.0, factors={3: 1.4, 4: 1.4})
    p["weekly"] = {3: 15.0}
    assert roster_strength.player_week(p, 3, scale=1.13) == 15.0        # ESPN's
    assert roster_strength.player_week(p, 4, scale=1.13) == pytest.approx(15.82)


def test_the_shape_moves_a_lineup_but_not_by_much():
    rep = {"QB": 10.0, "RB": 6.0, "WR": 5.0, "TE": 4.0, "K": 6.0, "DST": 5.0}
    roster = [player(i, f"P{i}", pos, 12.0)
              for i, pos in enumerate(["QB","RB","RB","WR","WR","TE","K","DST","RB"])]
    flat = roster_strength.lineup(roster, 5, 3, rep)[0]
    for p in roster:
        p["factors"] = {5: 1.1}
    shaped = roster_strength.lineup(roster, 5, 3, rep)[0]
    assert shaped == pytest.approx(flat * 1.1)
