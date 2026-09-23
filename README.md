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

## Privacy — read this before deploying

GitHub Pages on the free tier requires a **public** repository, so this one is
public and the code is readable by anyone. **The data is not.** Everything real
ships as **AES-256-GCM** ciphertext under a key derived from a passphrase
(PBKDF2-SHA256, 300k rounds) and is decrypted in the browser. What that means in
practice:

| Public | Private (encrypted or never committed) |
|---|---|
| the model's source | every team, owner and roster |
| the payout structure | all projections, EV and odds |
| that this exists | the tape, and even the league id |

Four guards, each covered by a test:

1. the workflow **refuses to run** without `EV_PASSPHRASE`
2. the published page is built `--locked`: no teams, no tape, not even the league
   id — only a lock screen and the engine
3. a deploy gate greps `_site` and **fails** on anything data-shaped in the clear
4. the plaintext payload is gitignored, and the tape is committed encrypted

Actions logs are public here too, so every script prints counts and never a team,
an owner or a dollar figure.

**Use a passphrase you have not used anywhere league-facing.** It is the whole
gate. Anyone with write access to this repository can also read the secret, so
keep the collaborator list to yourself.

## Setup

1. **Settings → Pages → Source: GitHub Actions**
2. **Settings → Secrets and variables → Actions → Secrets:**
   - `EV_PASSPHRASE` — unlocks the page (8+ characters)
   - `ESPN_LEAGUE_ID` — the league id
   - `EV_OWNER` — the surname whose card sits at the top
3. **→ Variables:** `ESPN_SEASON` (e.g. `2026`)
4. Run **Actions → Publish EV → Run workflow** once to seed the tape and deploy.

The workflow then runs every 15 minutes through the game windows (Sunday
afternoon and night, Monday night, Thursday night) and every three hours
otherwise — roughly 600 Actions minutes a month.

## Running it locally

```bash
pip install -r requirements.txt
export ESPN_LEAGUE_ID=... ESPN_SEASON=2026 EV_OWNER=... EV_PASSPHRASE=...

python scripts/export_params.py              # pull ESPN -> data/params.json
python scripts/record_snapshot.py --no-export --backfill   # seed the tape
python scripts/build_app.py --local --out ev.html          # plaintext, for your disk
python -m pytest -q
```

`--local` bakes the data in so the file works straight off your disk. **Never
publish that build** — the default (`--locked`) is the one that may touch a URL.

## Layout

| Path | What |
|---|---|
| `evmodel/season_sim.py` | the simulator: posterior, 20k seasons, the bracket, the money |
| `evmodel/espn_live.py` | ESPN's league endpoints — projections, injuries, byes, kickoffs |
| `evmodel/roster_strength.py` | the lineup optimizer that turns a roster into a weekly mean |
| `evmodel/ev_history.py` | the tape (columnar, thinned, append-when-moved) |
| `scripts/ev_sim.js` | the engine in the browser — a port of `season_sim.py`, tested against it |
| `scripts/crypt.py` | lock / unlock the payload |
| `scripts/build_app.py` | build the page (`--locked` by default) |
| `.github/workflows/publish.yml` | the whole loop, on a schedule |

The JavaScript engine is a faithful port of the Python one; the suite runs both
on the same fixtures and fails if they drift.
