"""FanDuel as a third opinion: level only, never shape, and identity by default."""

import pytest

from evmodel import config, fanduel


def player(pid, name, pos="RB", status="ACTIVE", on_ir=False, abbrev=""):
    return {"player_id": pid, "name": name, "pos": pos, "status": status,
            "on_ir": on_ir, "pro_team_abbrev": abbrev}


def test_a_line_is_scored_under_our_rules_not_fanduels():
    """Their FPTS column is built for someone else's league. The point of taking
    raw components is the -1 per sack this league is unusual for."""
    row = {"passingYards": 4000.0, "passingTouchdowns": 25.0, "interceptionsThrown": 10.0,
           "sacks": 35.0, "rushingYards": 400.0, "rushingTouchdowns": 8.0, "fumLost": 2.0}
    got = fanduel.score_skill(row)
    want = (4000*0.04 + 25*5 - 10*2 - 35*1 + 400*0.1 + 8*6 - 2*2)
    assert got == pytest.approx(want)
    # and the sacks are not a rounding detail
    assert fanduel.score_skill({**row, "sacks": 0.0}) - got == pytest.approx(35.0)


def test_kickers_are_banded_by_distance():
    """3/4/5 by range, which is what this league pays and what Sleeper's single
    total could never express."""
    row = {"fieldGoalsMade0To19": 1.0, "fieldGoalsMade20To29": 5.0,
           "fieldGoalsMade30To39": 4.0, "fieldGoalsMade40To49": 6.0,
           "fieldGoalsMade50Plus": 2.0, "extraPointsMade": 30.0}
    assert fanduel.score_kicker(row) == pytest.approx(10*3 + 6*4 + 2*5 + 30*1)


def test_a_defence_is_banded_on_points_allowed_per_game():
    """The band is a per-game rule; FanDuel reports a season total, so it is
    spread over the games before the band is read."""
    row = {"pointsAllowed": 150.0, "sacks": 40.0, "interceptions": 12.0,
           "fumblesRecovered": 6.0, "touchdowns": 3.0}
    got = fanduel.score_dst(row, games=15)         # 10 pts/game -> the 7-13 band
    want = 40*1 + 12*2 + 6*2 + 3*6 + config.dst_points_allowed(10.0)*15
    assert got == pytest.approx(want)
    assert config.dst_points_allowed(10.0) == 4.0


def test_the_league_level_survives_the_scaling():
    """Clamping truncates the bullish tail and not the bearish one, which walked
    the league total down 4.87 pts/wk before this was added. The calibration
    scale is fitted against what the league scores, so the level is an invariant."""
    ps = [player(i, n) for i, n in enumerate("ABCD")]
    ours = {i: 100.0 for i in range(4)}
    fd = {fanduel.norm("A"): 200.0, fanduel.norm("B"): 100.0,
          fanduel.norm("C"): 100.0, fanduel.norm("D"): 50.0}
    s = fanduel.scales(ps, ours, fd, weight=1.0)
    assert sum(ours[i] * s[i] for i in s) == pytest.approx(sum(ours.values()))


def test_an_injured_player_keeps_our_weekly_timing():
    """A dateless season total cannot say "out until week 7". ESPN's grid can, so
    for anyone ESPN has ruled out the grid wins outright."""
    ps = [player(1, "Hurt", status="OUT"), player(2, "Shelf", on_ir=True),
          player(3, "Fine"), player(4, "AlsoFine")]
    ours = {1: 100.0, 2: 100.0, 3: 100.0, 4: 100.0}
    fd = {fanduel.norm(n): 300.0 for n in ("Hurt", "Shelf")}
    fd.update({fanduel.norm("Fine"): 110.0, fanduel.norm("AlsoFine"): 90.0})
    s = fanduel.scales(ps, ours, fd, weight=1.0)
    assert 1 not in s and 2 not in s
    assert 3 in s and 4 in s


def test_off_is_off_and_absent_is_identity():
    ps = [player(1, "A"), player(2, "B")]
    ours = {1: 100.0, 2: 100.0}
    fd = {fanduel.norm("A"): 130.0, fanduel.norm("B"): 100.0}
    assert fanduel.scales(ps, ours, fd, weight=0.0) == {}   # the switch
    assert fanduel.scales(ps, ours, {}, weight=1.0) == {}   # nobody matched
    assert fanduel.scales([], {}, fd, weight=1.0) == {}     # no players
    thin = fanduel.scales(ps, {1: 5.0, 2: 5.0}, fd, weight=1.0)
    assert thin == {}                                        # below MIN_LEVEL


def test_defences_join_on_team_not_name():
    """"Texans D/ST" here, "Houston D/ST" there -- the abbreviation matches."""
    assert fanduel.key(player(9, "Texans D/ST", pos="DST", abbrev="HOU")) == "DST:HOU"
    assert fanduel.key(player(9, "Aaron Jones Sr.")) == fanduel.norm("Aaron Jones")


def test_a_scale_moves_the_level_and_never_the_shape():
    """The whole reason FanDuel is applied as a multiplier: a bye is a zero, and
    no multiple of zero is a week he plays. Same for a week he is expected to
    miss. FanDuel gets to argue about how good he is, never about when he plays."""
    from evmodel import roster_strength
    p = {"weekly": {4: 20.0, 5: 0.0, 6: 20.0},          # 5 is his bye
         "sleeper": {4: 10.0, 6: 10.0}, "rate": 99.0, "factors": {}}
    plain = {w: roster_strength.player_week(p, w) for w in (4, 5, 6)}
    p["fd_scale"] = 1.20
    lifted = {w: roster_strength.player_week(p, w) for w in (4, 5, 6)}
    assert lifted[4] == pytest.approx(plain[4] * 1.2)
    assert lifted[6] == pytest.approx(plain[6] * 1.2)
    assert plain[5] == 0.0 and lifted[5] == 0.0          # the bye survives
