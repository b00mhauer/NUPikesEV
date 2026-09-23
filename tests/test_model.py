"""The model: the money adds up, the plaque is a regular-season prize, live games
move the number, and the browser engine agrees with Python."""

import json
import math
import subprocess
import tempfile
from pathlib import Path

import pytest

from evmodel import config, season_sim

REPO = Path(__file__).resolve().parents[1]
EV_SIM_JS = REPO / "scripts" / "ev_sim.js"
PARAMS = REPO / "data" / "params.json"
N = 20000


# --------------------------------------------------------------------------
# a synthetic league — no network, no board, every week state represented
# --------------------------------------------------------------------------
def round_robin(team_ids: list[int], weeks: int) -> list[list[tuple[int, int]]]:
    """Circle-method pairings, wrapped to fill the season."""
    ring = team_ids[1:]
    rounds = []
    for r in range(len(team_ids) - 1):
        order = [team_ids[0]] + ring[r:] + ring[:r]
        half = len(order) // 2
        rounds.append(list(zip(order[:half], reversed(order[half:]))))
    return [rounds[w % len(rounds)] for w in range(weeks)]


def make_params(n_teams=12, weeks=14, finals=2, live_week=3, spread=1.0):
    ids = list(range(1, n_teams + 1))
    teams = [{
        "team_id": i,
        "abbrev": f"T{i:02d}",
        "owner": f"Owner {i}",
        "name": f"Team {i}",
        "is_us": i == 1,
        "prior_weekly": {str(w): 95.0 + spread * (i - (n_teams + 1) / 2)
                         for w in range(1, weeks + 1)},
        "prior_playoff": 95.0 + spread * (i - (n_teams + 1) / 2),
    } for i in ids]

    sched = round_robin(ids, weeks)
    out_weeks = []
    for w, pairs in enumerate(sched, start=1):
        state = "final" if w <= finals else ("live" if w == live_week else "future")
        matchups = []
        for k, (home, away) in enumerate(pairs):
            m = {"home": home, "away": away, "home_points": 0.0, "away_points": 0.0}
            if state == "final":
                m["home_points"] = 90.0 + 3 * k + w
                m["away_points"] = 100.0 - 2 * k
            elif state == "live":
                m.update(home_points=40.0 + k, away_points=35.0,
                         home_proj=98.0 + k, away_proj=94.0,
                         home_minutes=200.0, away_minutes=240.0)
            matchups.append(m)
        out_weeks.append({"week": w, "state": state, "matchups": matchups})

    return {
        "season": 2026, "reg_season_weeks": weeks, "current_week": live_week,
        "sigma": season_sim.SIGMA_WEEK, "tau_prior": season_sim.TAU_PRIOR,
        "share_usd": config.SHARE_USD,
        "payout_usd": {str(p): config.payout_usd(p) for p in range(1, n_teams + 1)},
        "teams": teams, "weeks": out_weeks,
    }


@pytest.fixture(scope="module")
def params():
    return make_params()


@pytest.fixture(scope="module")
def result(params):
    return season_sim.simulate(params, N, seed=7)


# --------------------------------------------------------------------------
# the money
# --------------------------------------------------------------------------
def test_payout_vector_is_the_league_s(params):
    # 8/3/1 shares out, one share in: +1400 / +400 / break-even / -200.
    assert config.payout_usd(1) == 1400
    assert config.payout_usd(2) == 400
    assert config.payout_usd(3) == 0
    assert config.payout_usd(12) == -config.SHARE_USD
    # a closed pot: what the league pays out is exactly what it put in
    assert sum(config.payout_usd(p) for p in range(1, 13)) == 0


def test_ev_is_zero_sum(result):
    assert sum(t["ev_usd"] for t in result["teams"]) == pytest.approx(0, abs=1e-6)


def test_probabilities_are_distributions(result):
    assert sum(t["p_champ"] for t in result["teams"]) == pytest.approx(1, abs=1e-9)
    assert sum(t["p_shame"] for t in result["teams"]) == pytest.approx(1, abs=1e-9)
    for t in result["teams"]:
        assert sum(t["p_place"]) == pytest.approx(1, abs=1e-9)
        assert t["p_playoffs"] == pytest.approx(sum(t["p_place"][:4]), abs=1e-9)


def test_stronger_rosters_are_worth_more(result):
    ev = {t["abbrev"]: t["ev_usd"] for t in result["teams"]}
    assert ev["T12"] > ev["T01"]          # T12 carries the best prior
    shame = {t["abbrev"]: t["p_shame"] for t in result["teams"]}
    assert shame["T01"] > shame["T12"]


def test_even_league_splits_evenly():
    """Twelve identical rosters and no games played: everyone is 1/12 to win it
    and 1/12 for the plaque, and nobody has an edge in dollars."""
    flat = make_params(spread=0.0, finals=0, live_week=0)
    r = season_sim.simulate(flat, N, seed=11)
    for t in r["teams"]:
        assert t["p_champ"] == pytest.approx(1 / 12, abs=0.012)
        assert t["p_shame"] == pytest.approx(1 / 12, abs=0.012)
        assert t["ev_usd"] == pytest.approx(0, abs=40)


def test_is_deterministic(params):
    a = season_sim.simulate(params, 4000, seed=3)
    b = season_sim.simulate(params, 4000, seed=3)
    assert a == b


# --------------------------------------------------------------------------
# the plaque is a regular-season prize — the bracket never touches it
# --------------------------------------------------------------------------
def test_plaque_follows_the_worst_record_not_the_bracket():
    """A team that has already lost every week and is projected last takes the
    plaque; a playoff team never can, because the top four by record are by
    definition not the worst by record."""
    p = make_params(finals=2, live_week=0, spread=2.0)
    r = season_sim.simulate(p, N, seed=5)
    by = {t["abbrev"]: t for t in r["teams"]}
    assert by["T01"]["p_shame"] > by["T12"]["p_shame"]
    for t in r["teams"]:
        assert t["p_shame"] + t["p_playoffs"] <= 1.0 + 1e-9


def test_a_hopeless_roster_owns_the_plaque():
    p = make_params(finals=0, live_week=0)
    for t in p["teams"]:
        if t["abbrev"] == "T01":
            t["prior_weekly"] = {w: 40.0 for w in t["prior_weekly"]}
            t["prior_playoff"] = 40.0
    r = season_sim.simulate(p, 6000, seed=9)
    by = {t["abbrev"]: t for t in r["teams"]}
    assert by["T01"]["p_shame"] > 0.95
    assert by["T01"]["p_playoffs"] < 0.01
    assert by["T01"]["ev_usd"] == pytest.approx(-config.SHARE_USD, abs=5)


# --------------------------------------------------------------------------
# results shrink toward the prior at the rate the noise justifies
# --------------------------------------------------------------------------
def test_posterior_weight_matches_the_closed_form(params):
    post = season_sim.posterior(params)
    sigma2, tau2 = params["sigma"] ** 2, params["tau_prior"] ** 2
    for t in params["teams"]:
        p = post[t["team_id"]]
        assert p["games"] == 2                      # two finished weeks
        assert p["weight"] == pytest.approx(2 / (2 + sigma2 / tau2))
        assert abs(p["shift"]) < params["sigma"]    # two games cannot swing it far


def test_more_games_earn_more_weight():
    light = season_sim.posterior(make_params(finals=2, live_week=0))
    heavy = season_sim.posterior(make_params(finals=10, live_week=0))
    assert heavy[1]["weight"] > light[1]["weight"]
    assert heavy[1]["var"] < light[1]["var"]


# --------------------------------------------------------------------------
# the live week — what makes this thing move on a Sunday
# --------------------------------------------------------------------------
def test_freezing_the_live_week_matches_never_playing_it(params):
    """`freeze_live` is the baseline the app measures today's swing against, so
    it must reproduce the model with that week still ahead of us."""
    frozen = season_sim.simulate(params, N, seed=21, freeze_live=True)
    future = dict(params, weeks=[dict(w, state="future") if w["state"] == "live" else w
                                 for w in params["weeks"]])
    ahead = season_sim.simulate(future, N, seed=21)
    for a, b in zip(frozen["teams"], ahead["teams"]):
        assert a["ev_usd"] == pytest.approx(b["ev_usd"], abs=1e-9)


def test_a_blowout_in_progress_moves_the_money(params):
    """Put one team 60 up with the clock dead and its EV must rise against the
    same week unplayed — that is the number moving during games."""
    live = json.loads(json.dumps(params))
    for wk in live["weeks"]:
        if wk["state"] != "live":
            continue
        for m in wk["matchups"]:
            if m["home"] != 1:
                continue
            m.update(home_points=160.0, home_proj=160.0, home_minutes=0.0,
                     away_points=100.0, away_proj=100.0, away_minutes=0.0)
    hot = season_sim.simulate(live, N, seed=33)
    base = season_sim.simulate(live, N, seed=33, freeze_live=True)
    us = next(t for t in hot["teams"] if t["team_id"] == 1)
    was = next(t for t in base["teams"] if t["team_id"] == 1)
    assert us["ev_usd"] > was["ev_usd"] + 20
    assert us["exp_wins"] > was["exp_wins"] + 0.3


def test_a_finished_game_carries_no_variance(params):
    """Minutes at zero means the week is decided: two different seeds must give
    that matchup the same result, every time."""
    live = json.loads(json.dumps(params))
    for wk in live["weeks"]:
        if wk["state"] == "live":
            for m in wk["matchups"]:
                m.update(home_minutes=0.0, away_minutes=0.0,
                         home_proj=m["home_points"], away_proj=m["away_points"])
    a = season_sim.simulate(live, 3000, seed=1)["teams"][0]["exp_wins"]
    b = season_sim.simulate(live, 3000, seed=2)["teams"][0]["exp_wins"]
    # week 1-3 are settled; only weeks 4-14 still differ between seeds
    assert abs(a - b) < 0.25


def test_minutes_remaining_scale_the_uncertainty(params):
    """The clock IS the uncertainty. A team trailing its projection is dead once
    the minutes hit zero, but still live with a full slate left to play."""
    live = next(w for w in params["weeks"] if w["state"] == "live")
    underdog = live["matchups"][0]["away"]      # every away side trails on projection

    def wins(minutes):
        p = json.loads(json.dumps(params))
        for wk in p["weeks"]:
            if wk["state"] == "live":
                for m in wk["matchups"]:
                    m["home_minutes"] = m["away_minutes"] = minutes
        r = season_sim.simulate(p, 8000, seed=4)
        return next(t for t in r["teams"] if t["team_id"] == underdog)["exp_wins"]

    assert wins(540.0) > wins(0.0) + 0.2


def test_bad_params_fail_loudly(params):
    season_sim.check_params(params)
    broken = json.loads(json.dumps(params))
    broken["weeks"][0]["state"] = "kinda-final"
    with pytest.raises(ValueError):
        season_sim.check_params(broken)
    missing = json.loads(json.dumps(params))
    del missing["teams"][0]["prior_weekly"]["9"]
    with pytest.raises(ValueError):
        season_sim.check_params(missing)


# --------------------------------------------------------------------------
# the browser engine is the same model
# --------------------------------------------------------------------------
def run_js(params, n_sims, seed, freeze=False):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(params, fh)
        path = fh.name
    code = (f"const EV=require({str(EV_SIM_JS)!r});"
            f"const p=require({path!r});"
            f"const r=EV.simulate(p,{n_sims},{seed},{str(freeze).lower()});"
            "console.log(JSON.stringify(r.teams));")
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True,
                         cwd=REPO, check=True)
    return json.loads(out.stdout)


def test_js_engine_matches_python(params):
    py = season_sim.simulate(params, N, seed=101)["teams"]
    js = run_js(params, N, 101)
    assert [t["team_id"] for t in js] == [t["team_id"] for t in py]
    for a, b in zip(py, js):
        # independent RNGs, so this is a Monte-Carlo tolerance, not an identity
        assert a["p_champ"] == pytest.approx(b["p_champ"], abs=0.02)
        assert a["p_shame"] == pytest.approx(b["p_shame"], abs=0.02)
        assert a["p_playoffs"] == pytest.approx(b["p_playoffs"], abs=0.03)
        assert a["ev_usd"] == pytest.approx(b["ev_usd"], abs=45)
        assert a["exp_wins"] == pytest.approx(b["exp_wins"], abs=0.15)
        assert a["post_shift"] == pytest.approx(b["post_shift"], abs=1e-9)
        assert a["post_weight"] == pytest.approx(b["post_weight"], abs=1e-9)
    assert sum(t["ev_usd"] for t in js) == pytest.approx(0, abs=1e-6)


def test_js_reads_the_live_week_like_python(params):
    """The live overlay is the part that only exists on a Sunday — pin it."""
    py = season_sim.simulate(params, N, seed=55, freeze_live=True)["teams"]
    js = run_js(params, N, 55, freeze=True)
    for a, b in zip(py, js):
        assert a["ev_usd"] == pytest.approx(b["ev_usd"], abs=45)


# --------------------------------------------------------------------------
# the committed params file (skipped until it is exported)
# --------------------------------------------------------------------------
def test_exported_params_are_valid():
    path = PARAMS
    if not path.exists():
        pytest.skip("params not exported")
    p = season_sim.load_params(path)
    season_sim.check_params(p)
    assert len(p["teams"]) == 12
    assert p["share_usd"] == config.SHARE_USD
    assert p["payout_usd"]["1"] == config.payout_usd(1)
    r = season_sim.simulate(p, 4000, seed=1)
    assert sum(t["ev_usd"] for t in r["teams"]) == pytest.approx(0, abs=1e-6)
    assert sum(1 for t in r["teams"] if t["is_us"]) == 1
    for t in p["teams"]:
        assert all(60 < v < 160 for v in t["prior_weekly"].values()), t["abbrev"]
