"""Streaming assumptions: who does it, what the wire is worth, and the rule that
a streamer is never made worse off by being flagged."""

import json

import pytest

from evmodel import roster_strength, streaming

TEAMS = [{"team_id": 9, "owner": "Mike Parrott"},
         {"team_id": 1, "owner": "Jack McQuiston"},
         {"team_id": 6, "owner": "michael glowacki"}]


def player(name, pos, pts):
    return {"player_id": hash(name) % 10**6, "name": name, "pos": pos,
            "status": "ACTIVE", "on_ir": False, "bye": 99, "starting": False,
            "rate": pts, "weekly": {w: pts for w in range(3, 18)},
            "sleeper": {}, "factors": {}}


ROSTER = [player("QB1", "QB", 20.0), player("RB1", "RB", 14.0), player("RB2", "RB", 11.0),
          player("WR1", "WR", 12.0), player("WR2", "WR", 10.0), player("TE1", "TE", 7.0),
          player("K1", "K", 8.0), player("D1", "DST", 6.0), player("FLEX1", "RB", 9.0)]
REP = {p: 5.0 for p in ("QB", "RB", "WR", "TE", "K", "DST")}


def test_load_is_forgiving_and_silence_means_nobody_streams():
    assert streaming.load('{"Parrott": ["DST"]}') == {"Parrott": ["DST"]}
    assert streaming.load('{"Parrott": "DST"}') == {"Parrott": ["DST"]}
    # a typo in a secret must not change the league; it reads as nobody streams
    assert streaming.load("{not json") == {}
    assert streaming.load(None) == {}
    assert streaming.load("[]") == {}
    # positions nobody streams are dropped rather than silently honoured
    assert streaming.load('{"Parrott": ["QB", "DST"]}') == {"Parrott": ["DST"]}


def test_resolve_matches_by_surname_and_refuses_an_ambiguous_key():
    assert streaming.resolve({"Parrott": ["DST"]}, TEAMS) == {9: {"DST"}, 1: set(), 6: set()}
    with pytest.raises(ValueError, match="matches 2 owners"):
        streaming.resolve({"mi": ["K"]}, TEAMS)     # Mike Parrott, michael glowacki


def test_a_streamer_takes_the_wire_when_it_beats_his_own_man():
    own, _ = roster_strength.lineup(ROSTER, 5, 4, REP)
    up, names = roster_strength.lineup(ROSTER, 5, 4, REP,
                                       streams={"DST"}, wire={"DST": 8.5})
    assert up - own == pytest.approx(8.5 - 6.0)
    assert "(wire DST)" in [n for _, n in names]


def test_streaming_is_an_option_and_never_costs_a_team_anything():
    """Nobody benches a good defence to chase a worse one, so flagging a manager
    as a streamer can only help him. This is what makes the assumption safe to
    set from a secret without auditing every roster."""
    for line in (0.0, 3.0, 5.9, 6.0, 12.0):
        held, _ = roster_strength.lineup(ROSTER, 5, 4, REP)
        streamed, _ = roster_strength.lineup(ROSTER, 5, 4, REP,
                                             streams={"DST"}, wire={"DST": line})
        assert streamed >= held - 1e-9, line
    # and a position he does NOT stream is untouched by a juicy wire
    same, _ = roster_strength.lineup(ROSTER, 5, 4, REP, streams={"DST"}, wire={"K": 30.0})
    base, _ = roster_strength.lineup(ROSTER, 5, 4, REP)
    assert same == pytest.approx(base)


def test_an_empty_hole_still_prices_at_replacement_for_a_non_streamer():
    thin = [p for p in ROSTER if p["pos"] != "DST"]
    total, names = roster_strength.lineup(thin, 5, 4, REP)
    assert "(streamed DST)" in [n for _, n in names]
    with_wire, names2 = roster_strength.lineup(thin, 5, 4, REP,
                                               streams={"DST"}, wire={"DST": 7.5})
    assert with_wire - total == pytest.approx(7.5 - REP["DST"])


def test_wire_levels_cache_must_cover_the_weeks_asked_for(tmp_path):
    """The bug this guards is one the forward grid already had: a cache that is
    young enough but covers the wrong weeks must not answer as a hit."""
    f = tmp_path / "wire.json"
    f.write_text(json.dumps({"season": 2026, "rank": 1, "fetched_at": 1_000_000.0,
                             "levels": {"4": {"K": 8.0, "DST": 7.0}}}))
    hit, why = streaming.wire_levels(f, 2026, [4], 1_000_060.0)
    assert hit == {4: {"K": 8.0, "DST": 7.0}} and "cached" in why
    # week 5 is not in it, so this must NOT come back as a cache hit -- it has
    # to go to the wire, which is stubbed here so the suite never touches ESPN.
    calls = []
    streaming._fetch_week = lambda season, week, pos, rank: (
        calls.append((week, pos)) or 7.5)
    got, why2 = streaming.wire_levels(f, 2026, [4, 5], 1_000_060.0, ttl=3600)
    assert "cached" not in why2
    assert sorted(calls) == [(4, "DST"), (4, "K"), (5, "DST"), (5, "K")]
    assert got == {4: {"K": 7.5, "DST": 7.5}, 5: {"K": 7.5, "DST": 7.5}}


def test_a_failed_wire_fetch_falls_back_to_the_cache_rather_than_to_nothing(tmp_path):
    """An undocumented endpoint can change without warning. A stale line beats
    pricing a streamer as an owner, and both beat failing the build."""
    f = tmp_path / "wire.json"
    f.write_text(json.dumps({"season": 2026, "rank": 1, "fetched_at": 1.0,
                             "levels": {"4": {"K": 8.0, "DST": 7.0}}}))
    def boom(*a, **k):
        raise RuntimeError("espn said no")
    streaming._fetch_week = boom
    got, why = streaming.wire_levels(f, 2026, [4, 5], 9_999_999.0)
    assert got == {4: {"K": 8.0, "DST": 7.0}} and "fetch failed" in why
    # with no cache at all it degrades to "nobody streams", not an exception
    got2, why2 = streaming.wire_levels(tmp_path / "absent.json", 2026, [4], 9_999_999.0)
    assert got2 == {} and "no cache" in why2
