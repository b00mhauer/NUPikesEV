# EV model — what a fantasy season is worth, live

Prices every team's season in dollars and keeps repricing it while the games are
on. It answers two questions a standings page cannot:

- **What is my season worth right now?** Expected winnings, in dollars, against
  the league's real payout structure.
- **Who is on the hook for last place?** Odds on the wooden spoon — here, the
  **Shame Plaque** — decided on regular-season record, because the playoffs
  decide the money and nothing else.

Twelve owners ante one share each; the pot pays **8 / 3 / 1** shares to 1st / 2nd
/ 3rd. You never win your own share back, so what lands in a pocket is **7 / 2 /
0** — and every other finish is **−1**. Third place is a push, and the whole
table sums to zero, which is the model's cheapest self-check.

## How it works

**1. The prior is the roster, not the record.** Strength comes from ESPN's own
projections, re-cut continuously: every player's rest-of-season number, his
week-specific projection, his injury designation, his bye. A bye- and
injury-aware lineup optimizer turns that into one scoring mean per team per
remaining week. A starter ruled out is not in the lineup — the next man is, at
his projection — which is why an inactive report moves the money.

Two calibrations, both measured, both shown on the page:

- ESPN's *week* projection is conditional on playing; its *season* projection
  already discounts games it expects a player to miss. Across a live league the
  gap is a flat **1.13** at every position, so rest-of-season rates are lifted
  onto the weekly scale. Without it the same roster reads 100 in week 3 and 81 in
  week 5.
- The league's scoring level is then nudged to what it actually scores, shrunk
  toward 1 by how little has been played.

**Matchup shape comes from a second source.** ESPN publishes a week-specific
projection for exactly one week — the current one — so every week after it would
otherwise be the same flat number for a player. Sleeper projects every remaining
week, differentiated by opponent. We take **only the shape**: `factor =
sleeper_week / that player's average week`, applied to ESPN's rate. The level
stays ESPN's, because ESPN scores against the league's actual rules and Sleeper
against its own — a ratio cancels that and leaves the matchup. Anything Sleeper
cannot match gets a factor of 1.0, which is the flat rate, so the dependency can
fail without the model degrading.

Measured rather than assumed: the two sources are statistically indistinguishable
on accuracy (Sleeper MAE 4.91 / r 0.580, ESPN 4.97 / 0.607, n=159), so this is
about availability. Defences match on team code, not name — every unmatched
player in the live league was a D/ST — which puts coverage at 99%. Net effect: a
team-week moves a median of 1.5 points, up to 7.8, against 19.6 of weekly noise.

**2. Results shrink toward that prior — they never replace it.** Weekly scores
swing about **19.6 points** (within-team sd, five seasons), far more than a
roster projection still leaves uncertain (about **4**). So a team's own results
earn weight *n / (n + σ²/τ²)*: about **8% after two games**, roughly 40% by week
14. A hot start is mostly schedule, and the model says so instead of crowning
anybody in September.

**3. Twenty thousand seasons**, on the real remaining schedule. Each week is
**final** (fixed), **live** (mean = ESPN's live projection, variance scaled by
the share of each lineup that has not kicked off yet, from real kickoff times),
or **future** (drawn from the posterior predictive). Then the bracket: top four
by record, points-for breaking ties, two-week semifinals, the final and the
third-place game. Places 5–12 fall out by record; the last of those takes the
plaque. Seeded and reproducible.

**The tape.** Every run appends a point — EV, title odds, plaque odds, roster
strength, projected wins — so each team reads like a stock. Points are recorded
only when something moved, and thinned to daily after two weeks. Anything
reconstructed after the fact is drawn dashed and labelled; a reconstruction is
not a measurement.

## What's on the page

- **Your team at the top.** Tap any row to claim it and the page remembers; until
  someone picks, the card cycles the whole league.
- **What this week is worth.** Not "you are 38% to make the playoffs" but "win
  Sunday and it is 61%, lose and it is 19% — a $134 swing". It comes from
  partitioning the same simulated seasons by who won the week, so both sides are
  consistent with each other and with the headline by construction.
- **A book.** Spread, total and moneylines in American odds for every matchup,
  plus season prices — title, playoffs, plaque, win total — on each team. These
  are **fair odds**: no juice, no hold, so the two sides of a game add to 100%.
- **The tape.** A sparkline per team and the full line on tap.
- **A crawl** along the bottom, every team quoted like a stock, with plaque watch
  and the biggest swing of the week.

## It is an open page

There is no password. Anyone with the link opens it, and because GitHub Pages on
the free tier requires a public repository, the code and the data are public too.
That is a decision: the audience is the whole league, and a gate everybody has to
be handed was not worth the friction.

What that means concretely — on the page and in this repo, readable by anyone who
finds them: team names, owner names as ESPN reports them, rosters, records,
projections, and every EV and odds number here. What is *not* here: any
credential. The model reads ESPN's public league endpoints; nothing it touches
needs a login, and nothing secret is stored.

`robots.txt` asks search engines to stay out. That is a request, not a wall.

If that ever stops being the right trade, the options in rough order of effort
are: publish at an unguessable path instead of the root, put Cloudflare Access in
front of it, or go back to encrypting the payload behind a passphrase (it was
built that way first — see the history of `scripts/crypt.py`).

## Setup

1. **Settings → Pages → Source: GitHub Actions**
2. **Settings → Secrets and variables → Actions → Variables:**
   - `ESPN_LEAGUE_ID` — the league id
   - `ESPN_SEASON` — e.g. `2026`
3. Run **Actions → Publish EV → Run workflow** once to seed the tape and deploy.

The workflow then runs every 15 minutes through the game windows (Sunday
afternoon and night, Monday night, Thursday night) and every three hours
otherwise — roughly 600 Actions minutes a month.

## Running it locally

```bash
pip install -r requirements.txt
export ESPN_LEAGUE_ID=... ESPN_SEASON=2026

python scripts/export_params.py                            # pull ESPN -> data/params.json
python scripts/record_snapshot.py --no-export --backfill   # seed the tape
python scripts/build_app.py --out ev.html                  # the page
python -m pytest -q
```

The build bakes the data in, so `ev.html` works straight off your disk with no
server and no network.

## Layout

| Path | What |
|---|---|
| `evmodel/season_sim.py` | the simulator: posterior, 20k seasons, the bracket, the money |
| `evmodel/espn_live.py` | ESPN's league endpoints — projections, injuries, byes, kickoffs |
| `evmodel/roster_strength.py` | the lineup optimizer that turns a roster into a weekly mean |
| `evmodel/ev_history.py` | the tape (columnar, thinned, append-when-moved) |
| `evmodel/sleeper.py` | matchup shape — the week-to-week ratios ESPN does not publish |
| `scripts/ev_sim.js` | the engine in the browser — a port of `season_sim.py`, tested against it |
| `scripts/build_app.py` | build the page |
| `.github/workflows/publish.yml` | the whole loop, on a schedule |

The JavaScript engine is a faithful port of the Python one; the suite runs both
on the same fixtures and fails if they drift.
