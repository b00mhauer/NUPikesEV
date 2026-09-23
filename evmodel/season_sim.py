"""G5 — the in-season season simulator: playoff odds, finish distribution, and the
money (expected winnings in dollars) under the league's 8/3/1 share structure.

WHAT IT ANSWERS: what is each owner's season worth, right now, in dollars — and
who is on the hook for the Shame Plaque. EV = sum over finishing places of
P(place) x net dollars for that place (config.payout_usd): +1400 / +400 / 0 /
-200. The table sums to zero across twelve teams because the pot is closed.

THE MODEL (three layers, deliberately simple where the data is thin):

1. PRIOR — each team's weekly scoring mean comes from its CURRENT roster: our
   board projections, run through a bye-aware lineup optimizer, one mean per
   remaining week (`prior_weekly`). Built by scripts/export_ev_data.py; the
   engine just consumes it. This carries the model early in the season, when
   two games of results say almost nothing.

2. POSTERIOR — results shrink toward that prior, they don't replace it. Weekly
   scores are noisy (sigma ~ 19.6 pts within-team, 2021-25) while true team
   strength spreads only tau ~ 4-5 pts, so a team's own results earn weight
   n / (n + sigma^2/tau^2) — about 12% after two games, 48% after fourteen.
   A 2-0 start is mostly schedule luck and this says so out loud.

3. SIMULATION — 20k seeded seasons over the real remaining schedule. Weeks fall
   into three states: FINAL (fixed points), LIVE (mean = ESPN's live projection,
   sd scaled by the minutes still on the field — this is what moves during
   games), and FUTURE (drawn from the posterior predictive). Then: seed the top
   four by record with points-for as the tiebreak, run the two-week semifinals
   (1v4, 2v3), the final and the third-place game. Places 5-12 are the rest by
   record; the LAST of those takes the plaque.

The JavaScript port in scripts/ev_sim.js is a faithful mirror of this file —
same params, same three week states, same bracket. tests/test_model.py runs both
on one fixture and asserts they agree.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from . import config

REG_SEASON_WEEKS = 14
# A starting lineup is nine players; ESPN reports team minutes remaining against
# roughly that budget, and we use the ratio to scale live-week variance.
TEAM_MINUTES = 9 * 60
# Weekly scoring noise and true-strength spread, both in league points, measured
# off seventeen seasons of this league's box scores (2021-25, weeks 1-14):
# within-team weekly sd 19.6; the observed
# spread of team season means is 7.2, of which sigma^2/14 is sampling noise,
# leaving tau ~ 5. tau_prior is the residual uncertainty AFTER the roster prior,
# so it is set a little tighter than tau. Both are overridable per params file.
SIGMA_WEEK = 19.6
TAU_PRIOR = 4.0


# --------------------------------------------------------------------------
# params contract (shared with scripts/ev_sim.js — keep the two in step)
# --------------------------------------------------------------------------
# {
#   "season": 2026, "current_week": 3, "reg_season_weeks": 14,
#   "sigma": 19.6, "tau_prior": 4.0,
#   "share_usd": 200.0,
#   "teams": [{"team_id": 9, "abbrev": "PAE", "owner": "Mike Parrott",
#              "name": "Paella Partners", "is_us": true,
#              "prior_weekly": {"3": 101.2, ...},   # one mean per week 1..14
#              "prior_playoff": 103.0}],
#   "weeks": [{"week": 1, "state": "final"|"live"|"future",
#              "matchups": [{"home": 9, "away": 4,
#                            "home_points": 93.2, "away_points": 80.7,
#                            "home_proj": ..., "away_proj": ...,      # live only
#                            "home_minutes": ..., "away_minutes": ...}]}]
# }

def check_params(params: dict) -> None:
    """Fail loudly on a malformed params file rather than simulating nonsense."""
    for key in ("teams", "weeks", "sigma", "tau_prior", "share_usd"):
        if key not in params:
            raise ValueError(f"params missing {key!r}")
    ids = {t["team_id"] for t in params["teams"]}
    if len(ids) != len(params["teams"]):
        raise ValueError("duplicate team_id")
    for wk in params["weeks"]:
        if wk["state"] not in ("final", "live", "future"):
            raise ValueError(f"week {wk['week']}: bad state {wk['state']!r}")
        for m in wk["matchups"]:
            if m["home"] not in ids or m["away"] not in ids:
                raise ValueError(f"week {wk['week']}: unknown team in matchup")
    for t in params["teams"]:
        missing = [w["week"] for w in params["weeks"]
                   if w["state"] != "final" and str(w["week"]) not in t["prior_weekly"]]
        if missing:
            raise ValueError(f"team {t['abbrev']}: no prior_weekly for weeks {missing}")


# --------------------------------------------------------------------------
# layer 2 — the posterior shift each team has earned from its own results
# --------------------------------------------------------------------------
def posterior(params: dict, through_week: int | None = None) -> dict[int, dict]:
    """Per team: how far its finished weeks move it off its roster prior.

    Conjugate normal update on the team's OFFSET from the prior, so a team that
    keeps beating its projection drifts up and one that keeps missing drifts
    down — at a rate the weekly noise justifies, and no faster.
    """
    sigma2 = params["sigma"] ** 2
    tau2 = params["tau_prior"] ** 2
    finals = [w for w in params["weeks"] if w["state"] == "final"
              and (through_week is None or w["week"] <= through_week)]

    out = {}
    for t in params["teams"]:
        resid, n = 0.0, 0
        for wk in finals:
            for m in wk["matchups"]:
                for side in ("home", "away"):
                    if m[side] == t["team_id"]:
                        prior = t["prior_weekly"][str(wk["week"])]
                        resid += m[f"{side}_points"] - prior
                        n += 1
        prec = 1.0 / tau2 + n / sigma2
        out[t["team_id"]] = {
            "shift": (resid / sigma2) / prec,   # posterior mean of the offset
            "var": 1.0 / prec,                  # and its uncertainty
            "games": n,
            "weight": n / (n + sigma2 / tau2),  # how much of it is his own doing
        }
    return out


# --------------------------------------------------------------------------
# layer 3 — the simulation
# --------------------------------------------------------------------------
def simulate(params: dict, n_sims: int = 20000, seed: int = 20260101,
             freeze_live: bool = False) -> dict:
    """Run the season n_sims times. Returns per-team probabilities and EV.

    `freeze_live` treats the in-progress week as if it had not kicked off yet —
    the baseline the live numbers are measured against, so the app can show what
    today's games are actually doing to the money.
    """
    check_params(params)
    rng = np.random.default_rng(seed)
    teams = params["teams"]
    T = len(teams)
    idx = {t["team_id"]: i for i, t in enumerate(teams)}
    sigma = params["sigma"]
    post = posterior(params)

    # predictive sd for a FUTURE week: weekly noise + what we still don't know
    # about the team itself.
    pred_sd = np.array([math.sqrt(sigma ** 2 + post[t["team_id"]]["var"])
                        for t in teams])
    shift = np.array([post[t["team_id"]]["shift"] for t in teams])

    wins = np.zeros((n_sims, T))
    pf = np.zeros((n_sims, T))

    for wk in params["weeks"]:
        if wk["week"] > params.get("reg_season_weeks", REG_SEASON_WEEKS):
            continue
        state = wk["state"]
        if freeze_live and state == "live":
            state = "future"
        scores = np.zeros((n_sims, T))
        played = np.zeros(T, dtype=bool)

        for m in wk["matchups"]:
            for side in ("home", "away"):
                i = idx[m[side]]
                played[i] = True
                if state == "final":
                    scores[:, i] = m[f"{side}_points"]
                elif state == "live":
                    # mean = ESPN's live projection; the uncertainty left is the
                    # share of the lineup still on the field. `_frac` comes from
                    # real kickoff times (espn_live); `_minutes` is the hosted
                    # tap's older signal, kept as a fallback.
                    if m.get(f"{side}_frac") is not None:
                        frac = float(m[f"{side}_frac"])
                    else:
                        frac = max(0.0, float(m.get(f"{side}_minutes", 0.0))) / TEAM_MINUTES
                    frac = min(1.0, max(0.0, frac))
                    mean = float(m.get(f"{side}_proj", m[f"{side}_points"]))
                    sd = math.sqrt((sigma ** 2 + post[teams[i]["team_id"]]["var"]) * frac)
                    scores[:, i] = mean + sd * rng.standard_normal(n_sims)
                else:
                    mean = teams[i]["prior_weekly"][str(wk["week"])] + shift[i]
                    scores[:, i] = mean + pred_sd[i] * rng.standard_normal(n_sims)

        pf[:, played] += scores[:, played]
        for m in wk["matchups"]:
            h, a = idx[m["home"]], idx[m["away"]]
            hw = scores[:, h] > scores[:, a]
            tie = scores[:, h] == scores[:, a]
            wins[:, h] += hw + 0.5 * tie
            wins[:, a] += (~hw & ~tie) + 0.5 * tie

    # --- seeding: record first, points-for breaks every tie -----------------
    key = wins * 1e7 + pf                      # pf < 1e7, so record dominates
    order = np.argsort(-key, axis=1, kind="stable")   # best -> worst

    # --- playoffs: 1v4 and 2v3 over two weeks, then final + third-place -----
    playoff_mean = np.array([t["prior_playoff"] for t in teams]) + shift

    def draw(team_idx: np.ndarray, weeks: int = 1) -> np.ndarray:
        mu = playoff_mean[team_idx]
        sd = pred_sd[team_idx]
        tot = np.zeros(len(team_idx))
        for _ in range(weeks):
            tot += mu + sd * rng.standard_normal(len(team_idx))
        return tot

    s1, s2, s3, s4 = (order[:, k] for k in range(4))
    a_win = draw(s1, 2) >= draw(s4, 2)
    b_win = draw(s2, 2) >= draw(s3, 2)
    fin_a = np.where(a_win, s1, s4)
    fin_b = np.where(b_win, s2, s3)
    lose_a = np.where(a_win, s4, s1)
    lose_b = np.where(b_win, s3, s2)

    champ_is_a = draw(fin_a) >= draw(fin_b)
    first = np.where(champ_is_a, fin_a, fin_b)
    second = np.where(champ_is_a, fin_b, fin_a)
    third_is_a = draw(lose_a) >= draw(lose_b)
    third = np.where(third_is_a, lose_a, lose_b)
    fourth = np.where(third_is_a, lose_b, lose_a)

    # --- finish table: 1-4 from the bracket, 5-12 by regular-season record ---
    place = np.zeros((n_sims, T), dtype=np.int64)
    rows = np.arange(n_sims)
    for p, who in enumerate((first, second, third, fourth), start=1):
        place[rows, who] = p
    for p in range(5, T + 1):
        place[rows, order[:, p - 1]] = p

    p_place = np.zeros((T, T))
    for p in range(1, T + 1):
        p_place[:, p - 1] = (place == p).mean(axis=0)

    # The plaque is the worst REGULAR-SEASON record — the bracket never touches it.
    shame = np.zeros(T)
    last = order[:, -1]
    for i in range(T):
        shame[i] = (last == i).mean()

    # the params file carries the payout vector so Python and the browser price
    # a finish off one source; config is the fallback that built it.
    table = params.get("payout_usd") or {}
    usd = np.array([float(table.get(str(p), config.payout_usd(p)))
                    for p in range(1, T + 1)])
    ev = p_place @ usd

    return {
        "n_sims": n_sims,
        "seed": seed,
        "teams": [
            {
                "team_id": t["team_id"],
                "abbrev": t["abbrev"],
                "owner": t["owner"],
                "name": t.get("name", ""),
                "is_us": bool(t.get("is_us")),
                "ev_usd": float(ev[i]),
                "p_place": [float(x) for x in p_place[i]],
                "p_champ": float(p_place[i, 0]),
                "p_money": float(p_place[i, 0] + p_place[i, 1]),
                "p_playoffs": float(p_place[i, :4].sum()),
                "p_shame": float(shame[i]),
                "exp_wins": float(wins[:, i].mean()),
                "exp_pf": float(pf[:, i].mean()),
                "post_shift": float(shift[i]),
                "post_weight": float(post[t["team_id"]]["weight"]),
                "games": int(post[t["team_id"]]["games"]),
            }
            for i, t in enumerate(teams)
        ],
    }


def load_params(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())
