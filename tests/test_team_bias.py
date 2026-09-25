"""The management overlay: it has to be relative, and it has to be switchable
off without leaving a trace."""

import pytest

from evmodel import team_bias

TEAMS = [{"team_id": 1, "owner": "Mike Parrott", "abbrev": "PAE"},
         {"team_id": 2, "owner": "michael glowacki", "abbrev": "GLOW"},
         {"team_id": 3, "owner": "Matt kennedy", "abbrev": "KENN"}]
LEVELS = {1: 96.2, 2: 89.1, 3: 96.9}


def apply(bias):
    return team_bias.normalise(team_bias.resolve(bias, TEAMS), LEVELS)


def league(factors):
    return sum(LEVELS[t] * factors[t] for t in factors)


def test_the_league_total_is_exactly_preserved():
    """The calibration scale is fitted against what the league actually scores.
    An overlay that moved the league's level would quietly invalidate it, so the
    factors are renormalised until the total lands back where it started."""
    before = sum(LEVELS.values())
    for bias in ({"Parrott": 0.98, "Glowacki": 1.02},
                 {"Kennedy": 1.30},
                 {"Parrott": 0.5, "Glowacki": 0.9, "Kennedy": 2.0}):
        assert league(apply(bias)) == pytest.approx(before)


def test_uniform_is_identity_whatever_the_number():
    """The off switch. All 1.0 is off -- and so is all 1.5 and all 0.8, because
    only the ratios survive normalisation. There is no wrong way to switch it
    off, and a careless edit that scales everyone cannot drift the league."""
    for v in (1.0, 1.5, 0.8, 42.0):
        f = apply({t["owner"]: v for t in TEAMS})
        assert all(x == pytest.approx(1.0) for x in f.values())
    assert all(x == pytest.approx(1.0) for x in apply({}).values())


def test_the_ordering_asked_for_is_the_ordering_delivered():
    f = apply({"Parrott": 0.98, "Glowacki": 1.02})
    assert f[1] < f[3] < f[2]          # Parrott down, Kennedy untouched, Glow up
    # untouched teams still move a little -- that is the point of relative
    assert f[3] != pytest.approx(1.0, abs=1e-6)


def test_an_ambiguous_key_stops_the_build():
    """Silently biasing two teams is worse than a failed run."""
    with pytest.raises(ValueError, match="matches 3 owners"):
        team_bias.resolve({"m": 1.1}, TEAMS)


def test_a_broken_overlay_is_no_overlay_rather_than_no_site():
    for junk in ("", "   ", "not json", '{"Parrott": "high"}', None):
        assert team_bias.load(junk) == {}
    assert team_bias.load('{"Parrott": 0.98}') == {"Parrott": 0.98}
    assert team_bias.load('{"Parrott": -1}') == {}      # a negative level is nonsense


def test_an_overlay_that_is_off_says_nothing_in_the_log():
    """A normal run's output should not advertise that the machinery exists."""
    assert team_bias.describe(apply({}), TEAMS) == ""
    assert "GLOW" in team_bias.describe(apply({"Glowacki": 1.02}), TEAMS)
