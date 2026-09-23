"""The forward test: predictions are banked before the week, graded after, and
the verdict is allowed to say 'the layer hurts'."""

import pytest

from evmodel import projection_log as pl


def player(pid, rate, factors=None):
    return {"player_id": pid, "rate": rate, "factors": factors or {}}


def test_a_snapshot_records_both_columns():
    rows = pl.snapshot([player(1, 10.0, {4: 1.2, 5: 0.8})], [4, 5], scale=1.1)
    assert rows["1"]["4"] == [11.0, 13.2]      # flat, shaped
    assert rows["1"]["5"] == [11.0, 8.8]


def test_a_player_with_no_matchup_data_has_identical_columns():
    rows = pl.snapshot([player(2, 9.0)], [6], scale=1.0)
    assert rows["2"]["6"] == [9.0, 9.0], "no shape means the test is a no-op for him"


def test_a_week_is_only_banked_once():
    log = pl.empty(2026)
    assert pl.record(log, 3, 100, [player(1, 10.0, {4: 1.1})], [4], 1.0)
    assert not pl.record(log, 3, 200, [player(1, 10.0, {4: 1.9})], [4], 1.0)
    assert len(log["snapshots"]) == 1
    assert log["snapshots"][0]["p"]["1"]["4"] == [10.0, 11.0], "the first call stands"


def test_scoring_credits_the_column_that_was_closer():
    log = pl.empty(2026)
    pl.record(log, 3, 100, [player(1, 10.0, {4: 1.5})], [4], 1.0)   # flat 10, shaped 15
    r = pl.score(log, {(1, 4): 15.0})                                # shaped nailed it
    assert r["overall"]["mae_flat"] == 5.0
    assert r["overall"]["mae_shaped"] == 0.0
    assert r["overall"]["gain"] == 5.0
    assert r["overall"]["shaped_better_pct"] == 1.0


def test_scoring_reports_a_loss_honestly():
    log = pl.empty(2026)
    pl.record(log, 3, 100, [player(1, 10.0, {4: 1.5})], [4], 1.0)
    r = pl.score(log, {(1, 4): 10.0})                                # flat was right
    assert r["overall"]["gain"] == -5.0, "a layer that hurts must show a negative gain"
    assert r["overall"]["shaped_better_pct"] == 0.0


def test_horizon_is_the_distance_from_when_the_call_was_made():
    log = pl.empty(2026)
    pl.record(log, 3, 100, [player(1, 10.0, {4: 1.1, 9: 1.1})], [4, 9], 1.0)
    r = pl.score(log, {(1, 4): 11.0, (1, 9): 11.0})
    assert set(r["by_horizon"]) == {1, 6}
    assert r["by_horizon"][6]["n"] == 1


def test_weeks_that_have_not_happened_are_simply_not_scored():
    log = pl.empty(2026)
    pl.record(log, 3, 100, [player(1, 10.0, {4: 1.2, 12: 1.2})], [4, 12], 1.0)
    r = pl.score(log, {(1, 4): 12.0})
    assert r["overall"]["n"] == 1 and set(r["by_horizon"]) == {1}


def test_a_new_season_starts_a_new_log(tmp_path):
    p = tmp_path / "log.json"
    log = pl.empty(2025)
    pl.record(log, 3, 100, [player(1, 10.0)], [4], 1.0)
    pl.save(log, p)
    assert len(pl.load(p, 2025)["snapshots"]) == 1
    assert pl.load(p, 2026)["snapshots"] == []
