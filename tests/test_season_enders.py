"""The override that closes the gap between a season-ending injury being news
and it being a roster move.

The bug it exists for: ESPN carries a season-ender as OUT, a weekly designation
the model deliberately treats as a one-week fact, and keeps projecting him at
full value for every week after this one. De'Von Achane read ~15/wk through week
17 in both feeds while the page listed him as out, so he was graded A in the
FULL-STRENGTH lineup and his team carried fifteen phantom points a week in the
sim."""

import json

import pytest

from evmodel import espn_live, roster_strength, season_enders


def player(name, pos, pts, pid=1, status="ACTIVE", on_ir=False, bye=99):
    return {"player_id": pid, "name": name, "pos": pos, "status": status,
            "on_ir": on_ir, "bye": bye, "starting": False, "rate": pts,
            "weekly": {w: pts for w in range(3, 18)}, "sleeper": {}, "factors": {}}


@pytest.fixture
def override(tmp_path):
    p = tmp_path / "out_for_season.json"
    p.write_text(json.dumps({"2026": {"De'Von Achane": "reported out for the year"}}))
    return p


def test_a_named_player_is_stamped_by_name_or_by_espn_id(override, tmp_path):
    ps = [player("De'Von Achane", "RB", 15.0, pid=4429160, status="OUT"),
          player("Nico Collins", "WR", 10.8, pid=7, status="OUT")]
    season_enders.mark(ps, 2026, override)
    assert ps[0]["status"] == season_enders.STATUS
    # A weekly OUT that is NOT a season-ender is untouched -- the whole point is
    # that "out this week" and "out for good" stay different facts.
    assert ps[1]["status"] == "OUT"

    by_id = tmp_path / "by_id.json"
    by_id.write_text(json.dumps({"2026": {"4429160": "same player, id form"}}))
    ps2 = [player("De'Von Achane", "RB", 15.0, pid=4429160, status="OUT")]
    season_enders.mark(ps2, 2026, by_id)
    assert ps2[0]["status"] == season_enders.STATUS


def test_the_stamp_ends_the_season_for_every_week(override):
    p = player("De'Von Achane", "RB", 15.0, pid=4429160, status="OUT")
    # Before: a weekly OUT only removes him from the week in front of us.
    assert not espn_live.playable(p, 4, current_week=4)
    assert espn_live.playable(p, 9, current_week=4)
    season_enders.mark([p], 2026, override)
    assert not espn_live.playable(p, 4, current_week=4)
    assert not espn_live.playable(p, 9, current_week=4)
    assert not espn_live.playable(p, 17, current_week=4)


def test_he_leaves_the_full_strength_lineup_and_the_slot_reprices(override):
    """FULL-STRENGTH ignores byes and weekly designations on purpose -- it is the
    ceiling, and a player back in week 7 belongs in it. A player with no season
    left does not, and the slot has to fall to the next man rather than silently
    keep his points."""
    rep = {pos: 5.0 for pos in ("QB", "RB", "WR", "TE", "K", "DST")}
    roster = [player("De'Von Achane", "RB", 15.0, pid=4429160, status="OUT"),
              player("Ashton Jeanty", "RB", 12.9, pid=8),
              player("Backup Back", "RB", 6.0, pid=9)]
    before, names = roster_strength.lineup(roster, 99, 4, rep, ignore_bye=True)
    assert "De'Von Achane" in [n for _, n in names]

    season_enders.mark(roster, 2026, override)
    after, names = roster_strength.lineup(roster, 99, 4, rep, ignore_bye=True)
    assert "De'Von Achane" not in [n for _, n in names]
    # Before: RB1 15.0, RB2 12.9, FLEX 6.0. After: everyone shifts up one and the
    # flex has nobody left, so it prices at the 5.0 streaming line. The lineup
    # loses 10.0 -- his 15.0 less the replacement that backfills the hole, which
    # is exactly what he was worth over the wire and not a point more.
    assert before - after == pytest.approx(15.0 - 5.0, abs=1e-6)


def test_a_missing_or_broken_file_is_a_no_op(tmp_path):
    ps = [player("De'Von Achane", "RB", 15.0, pid=4429160, status="OUT")]
    season_enders.mark(ps, 2026, tmp_path / "nope.json")
    assert ps[0]["status"] == "OUT"
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    season_enders.mark(ps, 2026, bad)
    assert ps[0]["status"] == "OUT"
    # and a season with no entry
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps({"2025": {"De'Von Achane": "last year"}}))
    season_enders.mark(ps, 2026, ok)
    assert ps[0]["status"] == "OUT"


def test_the_shipped_file_parses_and_names_only_real_seasons():
    doc = json.loads(season_enders.PATH.read_text())
    for key, entry in doc.items():
        if key.startswith("_"):
            continue
        assert key.isdigit() and 2020 <= int(key) <= 2100, key
        assert all(isinstance(v, str) and v for v in entry.values()), key
