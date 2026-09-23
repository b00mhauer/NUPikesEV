"""The in-season data layer: ESPN's shapes read correctly, lineups are legal,
the tape stays honest, and nothing publishes in the clear."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from evmodel import espn_live, ev_history, roster_strength

REPO = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# ESPN shapes (synthetic fixtures — the real bundle is 2 MB)
# --------------------------------------------------------------------------
def stat(source, split, total, season=2026, week=None):
    s = {"statSourceId": source, "statSplitTypeId": split, "seasonId": season,
         "appliedTotal": total}
    if week is not None:
        s["scoringPeriodId"] = week
    return s


def entry(pid, name, pos_id, pro_team, slot, proj_season, actual, week_proj=None,
          injury="ACTIVE", week=3):
    stats = [stat(1, 0, proj_season), stat(0, 0, actual)]
    if week_proj is not None:
        stats.append(stat(1, 1, week_proj, week=week))
    return {"lineupSlotId": slot,
            "playerPoolEntry": {"player": {
                "id": pid, "fullName": name, "defaultPositionId": pos_id,
                "proTeamId": pro_team, "injuryStatus": injury, "stats": stats}}}


PROTEAMS = {"settings": {"proTeams": [
    {"id": 1, "abbrev": "ATL", "byeWeek": 7,
     "proGamesByScoringPeriod": {"3": [{"date": 1_790_295_300_000}]}},
    {"id": 2, "abbrev": "BUF", "byeWeek": 9,
     "proGamesByScoringPeriod": {"3": [{"date": 1_790_450_000_000}]}},
]}}


def test_rest_of_season_rate_spreads_the_projection_over_the_weeks_he_plays():
    byes = espn_live.bye_weeks(PROTEAMS)
    p = espn_live.player_line(
        entry(1, "Back One", 2, 1, 2, proj_season=235.0, actual=31.4, week_proj=15.1),
        2026, current_week=3, byes=byes)
    # 18 weeks less the week-7 bye = 17 he can play; the season forecast covers all
    # of them, so his weekly rate is that forecast spread across them.
    assert p["rate"] == pytest.approx(235.0 / 17)
    assert p["weekly"] == {3: 15.1}
    assert p["bye"] == 7 and p["pos"] == "RB" and p["starting"] is True


def test_producing_does_not_cut_a_players_rest_of_season_rate():
    """The bug this guards: subtracting banked points from a whole-season forecast
    charges a player for scoring. It rated Kyler Murray (-0.5 on the year) above
    Josh Allen (77.2) because the projection is not marked down when someone misses
    time, so his unearned points piled onto the weeks that were left."""
    byes = espn_live.bye_weeks(PROTEAMS)
    idle = espn_live.player_line(
        entry(2, "Idle", 1, 2, 0, proj_season=270.0, actual=0.0), 2026, 3, byes)
    hot = espn_live.player_line(
        entry(3, "Hot", 1, 2, 0, proj_season=270.0, actual=90.0), 2026, 3, byes)
    assert idle["rate"] == pytest.approx(hot["rate"])
    # and the better forecast still wins, whatever either has banked
    better = espn_live.player_line(
        entry(4, "Better", 1, 2, 0, proj_season=340.0, actual=120.0), 2026, 3, byes)
    assert better["rate"] > idle["rate"]
    assert all(x["rate"] >= 0 for x in (idle, hot, better))


def test_who_can_play_and_when():
    byes = espn_live.bye_weeks(PROTEAMS)
    make = lambda **kw: espn_live.player_line(                       # noqa: E731
        entry(3, "Guy", 2, 1, kw.pop("slot", 2), 200.0, 20.0, **kw), 2026, 3, byes)

    fit = make()
    assert espn_live.playable(fit, 3, 3) and espn_live.playable(fit, 4, 3)
    assert not espn_live.playable(fit, 7, 3)                  # his bye

    out = make(injury="OUT")
    assert not espn_live.playable(out, 3, 3), "ruled out this week"
    assert espn_live.playable(out, 4, 3), "a weekly OUT says nothing about week 4"

    ir = make(injury="INJURY_RESERVE")
    assert not espn_live.playable(ir, 3, 3) and not espn_live.playable(ir, 9, 3)

    stashed = make(slot=espn_live.IR_SLOT)
    assert not espn_live.playable(stashed, 3, 3) and not espn_live.playable(stashed, 9, 3)


def test_kickoff_ramp_bleeds_variance_off_through_the_afternoon():
    ko = espn_live.kickoffs(PROTEAMS)
    assert ko[(1, 3)] == 1_790_295_300_000
    kick = ko[(1, 3)]
    assert espn_live.played_fraction(kick, kick - 60_000) == 0.0
    assert espn_live.played_fraction(kick, kick + espn_live.GAME_MS // 2) == pytest.approx(0.5)
    assert espn_live.played_fraction(kick, kick + espn_live.GAME_MS * 2) == 1.0
    assert espn_live.played_fraction(None, kick) == 1.0          # bye: nothing pending


def test_week_states_come_off_espn_s_own_verdict():
    raw = {"season": 2026, "schedule": {"schedule": [
        {"matchupPeriodId": 1, "winner": "HOME",
         "home": {"teamId": 1, "totalPoints": 101.0}, "away": {"teamId": 2, "totalPoints": 88.0}},
        {"matchupPeriodId": 2, "winner": "UNDECIDED",
         "home": {"teamId": 1, "totalPoints": 40.0}, "away": {"teamId": 2, "totalPoints": 35.0}},
        {"matchupPeriodId": 3, "winner": "UNDECIDED",
         "home": {"teamId": 1, "totalPoints": 0.0}, "away": {"teamId": 2, "totalPoints": 0.0}},
    ]}}
    states = {w["week"]: w["state"] for w in espn_live.weeks(raw, 14)}
    assert states == {1: "final", 2: "live", 3: "future"}


# --------------------------------------------------------------------------
# the lineup optimizer
# --------------------------------------------------------------------------
def player(name, pos, rate, bye=0, status="ACTIVE", on_ir=False, weekly=None,
           sleeper_pts=None):
    """`rate` is what he is worth in a week.

    player_week() no longer reads p["rate"] -- that is ESPN's never-revised
    preseason total -- so a fixture has to carry a LIVE per-week number or the
    optimizer correctly values him at zero. Unless a test says otherwise, give
    him a Sleeper line of `rate` in every week he plays, which is what these
    tests always meant by rate."""
    sl = sleeper_pts if sleeper_pts is not None else \
        {w: rate for w in range(1, 19) if w != bye}
    return {"name": name, "pos": pos, "rate": rate, "bye": bye, "status": status,
            "on_ir": on_ir, "weekly": weekly or {}, "sleeper": sl, "starting": True,
            "pro_team": 1, "slot": 2, "player_id": abs(hash(name)) % 10000,
            "proj_season": rate * 16, "act_season": 0.0}


def squad():
    return [
        player("QB1", "QB", 20), player("QB2", "QB", 12),
        player("RB1", "RB", 15), player("RB2", "RB", 12), player("RB3", "RB", 9),
        player("WR1", "WR", 14), player("WR2", "WR", 11), player("WR3", "WR", 10),
        player("TE1", "TE", 8), player("K1", "K", 8), player("DST1", "DST", 7),
    ]


REPLACEMENT = {"QB": 10.0, "RB": 6.0, "WR": 5.0, "TE": 4.0, "K": 6.0, "DST": 5.0}


def test_the_optimizer_fields_the_best_legal_lineup():
    total, picked = roster_strength.lineup(squad(), 4, 3, REPLACEMENT)
    names = [n for _, n in picked]
    assert names.count("QB2") == 0, "one QB starts, and it is the better one"
    assert set(names) == {"QB1", "RB1", "RB2", "WR1", "WR2", "TE1", "K1", "DST1", "WR3"}
    # the flex takes the best leftover RB/WR — WR3 (10) beats RB3 (9)
    assert ("FLEX", "WR3") in picked
    assert total == pytest.approx(20 + 15 + 12 + 14 + 11 + 8 + 8 + 7 + 10)


def test_a_bye_pulls_a_starter_and_the_next_man_steps_in():
    roster = squad()
    for p in roster:
        if p["name"] in ("RB1", "RB2"):
            p["bye"] = 9
    full = roster_strength.lineup(roster, 4, 3, REPLACEMENT)[0]
    bye = roster_strength.lineup(roster, 9, 3, REPLACEMENT)[0]
    assert bye < full
    names = [n for _, n in roster_strength.lineup(roster, 9, 3, REPLACEMENT)[1]]
    assert "RB1" not in names and "RB3" in names


def test_a_hole_is_priced_at_replacement_not_at_zero():
    thin = [p for p in squad() if p["pos"] != "TE"]
    total, picked = roster_strength.lineup(thin, 4, 3, REPLACEMENT)
    assert ("TE", "(streamed TE)") in picked
    assert total == pytest.approx(
        roster_strength.lineup(squad(), 4, 3, REPLACEMENT)[0] - 8 + REPLACEMENT["TE"])


def test_ruling_a_star_out_drops_the_team_this_week_only():
    roster = squad()
    for p in roster:
        if p["name"] == "RB1":
            p["status"] = "OUT"
    this_week = roster_strength.lineup(roster, 3, 3, REPLACEMENT)[0]
    next_week = roster_strength.lineup(roster, 4, 3, REPLACEMENT)[0]
    assert this_week < next_week


def test_rate_scale_measures_the_espn_gap_but_no_longer_moves_a_lineup():
    """rate_scale is now a DIAGNOSTIC, not an input.

    It still reports how far ESPN's published week projection sits above the
    season total spread over the weeks left -- ~1.13, because the week number is
    conditional on playing while the season number discounts expected absences.
    But player_week() never reads p["rate"], so the scale it returns cannot
    change what a lineup is worth. That is the whole point: the season total is
    a preseason figure, and no multiple of a stale number makes it fresh."""
    roster = [player("A", "RB", 10, weekly={3: 11.3}), player("B", "WR", 8, weekly={3: 9.04}),
              player("C", "QB", 2, weekly={3: 40.0})]   # rate <= 3: ignored as noise
    assert roster_strength.rate_scale(roster, 3) == pytest.approx(1.13, abs=0.001)
    assert roster_strength.rate_scale(roster, 9) == 1.0      # no weekly projections

    p = roster[0]
    # Week 3 has both a published ESPN line (11.3) and a Sleeper line (10.0), so
    # the configured source decides -- the default blend splits them.
    assert roster_strength.player_week(p, 3, 1.13) == pytest.approx(10.65)
    # Week 9: ESPN publishes nothing, so Sleeper's live line stands on its own --
    # at 10.0, NOT the rate lifted to 11.3 the way the old model would have had it.
    assert roster_strength.player_week(p, 9, 1.13) == pytest.approx(10.0)
    # And the scale is inert: passing it, or not, gives the same answer.
    assert roster_strength.player_week(p, 9, 1.0) == \
        roster_strength.player_week(p, 9, 1.13)
    assert roster_strength.player_week(p, 3, 1.0) == \
        roster_strength.player_week(p, 3, 1.13)


def test_calibration_is_shrunk_toward_one_early():
    priors = {1: {1: 100.0, 2: 100.0}}
    hot = [(1, 1, 120.0), (1, 2, 120.0)]                    # +20% over two weeks
    scale, basis = roster_strength.calibration(priors, hot)
    assert 1.0 < scale < 1.2, "two weeks cannot move the level 20%"
    assert "shrunk" in basis
    many = [(1, w, 120.0) for w in (1, 2)] * 1
    assert roster_strength.calibration(priors, many)[0] == pytest.approx(scale)


# --------------------------------------------------------------------------
# the tape
# --------------------------------------------------------------------------
def fake_result(ev):
    return {"teams": [{"team_id": tid, "ev_usd": v, "p_champ": 0.1, "p_shame": 0.1,
                       "exp_wins": 7.0} for tid, v in ev.items()]}


FAKE_PARAMS = {"teams": [{"team_id": 1, "prior_playoff": 95.0},
                         {"team_id": 2, "prior_playoff": 90.0}]}


def test_the_tape_only_records_when_something_moved():
    hist = ev_history.empty(2026)
    tick = ev_history.snapshot(fake_result({1: 100.0, 2: -100.0}), FAKE_PARAMS)
    assert ev_history.moved(hist, tick, 1000)
    ev_history.append(hist, tick, 1000, 3)

    same = ev_history.snapshot(fake_result({1: 100.5, 2: -100.5}), FAKE_PARAMS)
    assert not ev_history.moved(hist, same, 1100), "half a dollar is not news"
    assert ev_history.moved(hist, same, 1000 + ev_history.HEARTBEAT_SECONDS), "but silence is"

    real = ev_history.snapshot(fake_result({1: 140.0, 2: -140.0}), FAKE_PARAMS)
    assert ev_history.moved(hist, real, 1100)


def test_the_tape_keeps_its_columns_aligned():
    hist = ev_history.empty(2026)
    for i, ev in enumerate([{1: 10.0, 2: -10.0}, {1: 30.0, 2: -30.0}, {1: 50.0, 2: -50.0}]):
        ev_history.append(hist, ev_history.snapshot(fake_result(ev), FAKE_PARAMS), 1000 + i, 3)
    for name in ev_history.SERIES:
        for arr in hist["series"][name].values():
            assert len(arr) == len(hist["at"]) == 3
    assert hist["series"]["ev"]["1"] == [10.0, 30.0, 50.0]
    assert ev_history.delta(hist, 1, since=1000) == pytest.approx(40.0)


def test_old_ticks_thin_to_one_a_day_and_recent_ones_do_not():
    hist = ev_history.empty(2026)
    now = 100 * ev_history.DAY
    for i in range(40):                       # four a day for ten days, long ago
        at = now - 40 * ev_history.DAY + (i // 4) * ev_history.DAY + (i % 4) * 3600
        ev_history.append(hist, ev_history.snapshot(fake_result({1: float(i), 2: -float(i)}),
                                                    FAKE_PARAMS), at, 3)
    for i in range(6):                        # and six from today
        ev_history.append(hist, ev_history.snapshot(fake_result({1: 99.0, 2: -99.0}),
                                                    FAKE_PARAMS), now - i * 600, 5)
    before = len(hist["at"])
    ev_history.compact(hist, now)
    assert len(hist["at"]) < before
    assert sum(1 for at in hist["at"] if at >= now - ev_history.KEEP_DENSE_DAYS * ev_history.DAY) == 6
    for name in ev_history.SERIES:
        for arr in hist["series"][name].values():
            assert len(arr) == len(hist["at"])


def test_a_backfill_lands_in_time_order():
    hist = ev_history.empty(2026)
    ev_history.append(hist, ev_history.snapshot(fake_result({1: 5.0, 2: -5.0}), FAKE_PARAMS), 9000, 3)
    ev_history.append(hist, ev_history.snapshot(fake_result({1: 1.0, 2: -1.0}), FAKE_PARAMS),
                      1000, 1, recon=True)
    ev_history.sort_by_time(hist)
    assert hist["at"] == [1000, 9000]
    assert hist["recon"] == [1, 0]
    assert hist["series"]["ev"]["1"] == [1.0, 5.0]


def test_a_new_season_starts_a_new_tape(tmp_path):
    path = tmp_path / "hist.json"
    hist = ev_history.empty(2025)
    ev_history.append(hist, ev_history.snapshot(fake_result({1: 5.0, 2: -5.0}), FAKE_PARAMS), 1, 1)
    ev_history.save(hist, path)
    assert len(ev_history.load(path, 2025)["at"]) == 1
    assert ev_history.load(path, 2026)["at"] == []


# --------------------------------------------------------------------------
# the published page
# --------------------------------------------------------------------------
def test_the_published_page_carries_the_league(tmp_path):
    """The page is open by design — no gate — so the build must actually ship the
    data and the engine rather than a shell waiting on something."""
    params = REPO / "data" / "params.json"
    if not params.exists():
        pytest.skip("params not exported")
    out = tmp_path / "index.html"
    subprocess.run([sys.executable, str(REPO / "scripts/build_app.py"), "--out", str(out)],
                   cwd=REPO, check=True, capture_output=True)
    html = out.read_text()
    for marker in ('"prior_weekly":', '"teams":', "EVSim", "simulate"):
        assert marker in html, f"{marker} missing from the built page"
    for gone in ("PBKDF2", "passphrase", "ev_season.json.enc"):
        assert gone not in html, f"{gone} is a leftover from the removed lock"


# --------------------------------------------------------------------------
# the browser reads ESPN the same way Python does
# --------------------------------------------------------------------------
ESPN_SCHEDULE = {"seasonId": 2026, "schedule": [
    {"matchupPeriodId": 1, "winner": "HOME",
     "home": {"teamId": 1, "totalPoints": 101.0}, "away": {"teamId": 2, "totalPoints": 88.0}},
    {"matchupPeriodId": 2, "winner": "UNDECIDED",
     "home": {"teamId": 1, "totalPoints": 44.0, "totalPointsLive": 44.0,
              "totalProjectedPointsLive": 97.0},
     "away": {"teamId": 2, "totalPoints": 51.0, "totalPointsLive": 51.0,
              "totalProjectedPointsLive": 94.0}},
    {"matchupPeriodId": 3, "winner": "UNDECIDED",
     "home": {"teamId": 1, "totalPoints": 0.0}, "away": {"teamId": 2, "totalPoints": 0.0}},
]}

KICK = 1_790_295_300_000


def js(expr: str):
    """Run a snippet against the browser engine and hand back its JSON."""
    code = (f"const EV=require({str(REPO / 'scripts' / 'ev_sim.js')!r});"
            f"console.log(JSON.stringify(({expr})));")
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True,
                         cwd=REPO, check=True)
    return json.loads(out.stdout)


def test_js_reads_week_states_exactly_as_python_does():
    py = {w["week"]: w["state"]
          for w in espn_live.weeks({"season": 2026, "schedule": ESPN_SCHEDULE}, 14)}
    weeks = js(f"EV.buildWeeks({json.dumps(ESPN_SCHEDULE)}, 14)")
    assert {w["week"]: w["state"] for w in weeks} == py == {1: "final", 2: "live", 3: "future"}


@pytest.mark.parametrize("offset,expected", [
    (-3600_000, 1.0),                               # before kickoff: all still to come
    (espn_live.GAME_MS // 2, 0.5),                  # halfway through the only game
    (espn_live.GAME_MS * 2, 0.0),                   # long over
])
def test_js_and_python_agree_on_how_much_is_left_to_play(offset, expected):
    now = KICK + offset
    proteams = {"settings": {"proTeams": [
        {"id": 1, "abbrev": "ATL", "byeWeek": 7,
         "proGamesByScoringPeriod": {"2": [{"date": KICK}]}}]}}
    roster = [{"starting": True, "pro_team": 1, "weekly": {2: 20.0}, "rate": 20.0,
               "name": "A", "pos": "RB", "bye": 7, "status": "ACTIVE", "on_ir": False}]

    py = espn_live.live_week({"schedule": ESPN_SCHEDULE["schedule"], "seasonId": 2026},
                             {1: roster}, espn_live.kickoffs(proteams), 2, now)
    assert py[1]["frac"] == pytest.approx(expected)

    params = {"teams": [{"team_id": 1, "lineup_now": [{"pro_team": 1, "proj": 20.0}]},
                        {"team_id": 2, "lineup_now": []}]}
    weeks = js(f"(function(){{var w=EV.buildWeeks({json.dumps(ESPN_SCHEDULE)},14);"
               f"return EV.overlayLive({json.dumps(params)}, w, "
               f"{json.dumps(ESPN_SCHEDULE)}, {json.dumps(proteams)}, {now});}})()")
    live = next(w for w in weeks if w["week"] == 2)
    assert live["matchups"][0]["home_frac"] == pytest.approx(expected)
    assert live["matchups"][0]["home_proj"] == pytest.approx(97.0)   # ESPN's live number
    assert py[1]["proj"] == pytest.approx(97.0)
