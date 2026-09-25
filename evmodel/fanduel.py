"""FanDuel Research (numberFire) rest-of-season projections: a third opinion.

Its GraphQL endpoint is undocumented but open -- no key, no auth -- and
introspection is enabled, which is how the input shape below was established
rather than guessed. Three calls cover the league: QB/RB/WR/TE all share the
NFL_SKILL type, with kickers and defences in their own.

What makes it worth having is independence. ESPN and Sleeper both lean on
overlapping industry consensus; numberFire is its own model, and a blend of two
correlated sources is worth less than it looks. What makes it awkward is the
shape: FanDuel gives ONE number per player for the rest of the season, with no
weeks in it. It can never say a receiver returns in week 7 or that a quarterback
is on bye, and those are the two things our weekly grid is best at.

So this module never touches shape. It produces a single multiplier per player
-- `fd_scale` -- that says only "you are pricing this man too high or too low
overall". Multiplying preserves zeros, so byes and injury absences come through
the other side untouched.

Four things had to be measured before the multiplier could be trusted:

  * Horizon. FanDuel's total runs a week or two past our championship. Sweeping
    the end week put the league median ratio closest to 1.000 at week 18, but
    Josh Allen alone reads 17, and the per-player view barely moves between them
    (correlation 0.998). So the constant is divided out via the league median
    and the horizon never has to be settled.

  * Availability. A season total forecasts what a player WILL accumulate,
    including games he is expected to miss; a sum of weekly projections assumes
    he plays every week he is not on bye. The gap is real -- we measured 13% of
    it on ESPN, where it lives in the params as `rate_scale`. FanDuel's is about
    3%. The median removes the league-average share of it.

  * Injuries. The worst disagreements are all injured players -- a back ESPN has
    on IR all season that FanDuel still projects, a quarterback at 0.09 of our
    number. A dateless total cannot express "out until week 7", which is exactly
    where the weekly grid is strongest. Those players are skipped outright.

  * Clamping bias. Truncating the bullish tail without touching the bearish one
    dragged the league total down 4.87 points a week in testing. So the scales
    are renormalised AFTER clamping, the same way the management overlay is.
"""

from __future__ import annotations

import json
import re
import statistics
import urllib.request

from . import config

URL = "https://www.fanduel.com/research/api/graphql"
TIMEOUT = 45
# The page requests a subset of these. The schema carries sacks, fumbles lost and
# two-point conversions too, which is the difference between approximating this
# league's scoring and matching it -- Josh Allen's sacks alone are 33 points.
SKILL = ("player { name position } team { abbreviation } passingYards passingTouchdowns "
         "interceptionsThrown sacks rushingYards rushingTouchdowns receivingYards "
         "receivingTouchdowns fumLost tpcP tpcR tpcV")
KICKER = ("player { name position } team { abbreviation } extraPointsMade "
          "fieldGoalsMade0To19 fieldGoalsMade20To29 fieldGoalsMade30To39 "
          "fieldGoalsMade40To49 fieldGoalsMade50Plus")
DST = ("player { name position } team { abbreviation } pointsAllowed sacks "
       "interceptions fumblesRecovered touchdowns")
FRAGMENTS = {"NFL_SKILL": ("NflSkill", SKILL),
             "NFL_KICKER": ("NflKicker", KICKER),
             "NFL_D_ST": ("NflDefenseSt", DST)}

_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\.?$", re.I)


def norm(name: str) -> str:
    """Match key. FanDuel writes "Aaron Jones", ESPN "Aaron Jones Sr." -- dropping
    the generational suffix is most of the difference between 87% and 98%."""
    return "".join(c for c in _SUFFIX.sub("", str(name).strip()).lower() if c.isalnum())


def fetch(position: str, timeout: int = TIMEOUT) -> list[dict]:
    """One position group's rest-of-season projections."""
    type_name, fields = FRAGMENTS[position]
    body = json.dumps({
        "operationName": "GetProjections",
        "variables": {"input": {"type": "REMAINING", "sport": "NFL",
                                "position": position, "scope": "TOTAL"}},
        "query": ("query GetProjections($input: ProjectionsInput!) "
                  "{ getProjections(input: $input) { ... on %s { %s } } }" % (type_name, fields)),
    }).encode()
    req = urllib.request.Request(URL, body, {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.fanduel.com/research/nfl/fantasy/rest-of-season-projections/te"})
    payload = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    if payload.get("errors"):
        raise RuntimeError(f"fanduel graphql: {json.dumps(payload['errors'])[:160]}")
    return payload["data"]["getProjections"] or []


def _n(row: dict, key: str) -> float:
    return float(row.get(key) or 0.0)


# FanDuel's column -> the scoring map's column. Keyed by NAME rather than by
# ESPN's stat ids, because FanDuel ships names and both repos carry the named
# map; going through the id map would work in one and not the other.
SKILL_COLUMNS = {
    "passingYards": "pass_yd",          "passingTouchdowns": "pass_td",
    "interceptionsThrown": "pass_int",  "sacks": "pass_sack",
    "rushingYards": "rush_yd",          "rushingTouchdowns": "rush_td",
    "receivingYards": "rec_yd",         "receivingTouchdowns": "rec_td",
    "fumLost": "fum_lost",
    "tpcP": "pass_2pt",                 "tpcR": "rush_2pt",  "tpcV": "rec_2pt",
}


def score_skill(row: dict) -> float:
    """A skill player's line under THIS league's rules, not FanDuel's."""
    pts = config.SLEEPER_STAT_POINTS
    return sum(_n(row, col) * pts[stat] for col, stat in SKILL_COLUMNS.items()
               if stat in pts)


def score_kicker(row: dict) -> float:
    """Field goals by distance, which is the whole reason this is worth doing:
    this league pays 3/4/5/6 by range and Sleeper only ever gave us a total.
    FanDuel's top bucket is 50+, so 60-yarders score as 50-59 -- a fraction of a
    kick a season, against a banding that was previously not applied at all."""
    k = config.KICKER_POINTS
    short = (_n(row, "fieldGoalsMade0To19") + _n(row, "fieldGoalsMade20To29")
             + _n(row, "fieldGoalsMade30To39"))
    return (short * k["fg_0_39"] + _n(row, "fieldGoalsMade40To49") * k["fg_40_49"]
            + _n(row, "fieldGoalsMade50Plus") * k["fg_50_59"]
            + _n(row, "extraPointsMade") * k["pat_made"])


def score_dst(row: dict, games: int) -> float:
    """A defence's line, with the points-allowed band applied PER GAME.

    The band is a per-game rule and FanDuel reports a season total, so the total
    is spread across the games it covers and the band read off the average. That
    understates a defence that alternates shutouts with blowouts, which is the
    honest cost of scoring a season total under a weekly rule."""
    d = config.DST_POINTS
    base = (_n(row, "sacks") * d["sack"] + _n(row, "interceptions") * d["interception"]
            + _n(row, "fumblesRecovered") * d["fumble_recovered"]
            + _n(row, "touchdowns") * d["int_return_td"])
    per_game = _n(row, "pointsAllowed") / games if games else 0.0
    return base + config.dst_points_allowed(per_game) * games


def totals(games: int = 15, timeout: int = TIMEOUT) -> dict[str, float]:
    """name-key -> rest-of-season points in our scoring, every position."""
    out: dict[str, float] = {}
    for row in fetch("NFL_SKILL", timeout):
        out[norm(row["player"]["name"])] = score_skill(row)
    for row in fetch("NFL_KICKER", timeout):
        out[norm(row["player"]["name"])] = score_kicker(row)
    for row in fetch("NFL_D_ST", timeout):
        # defences join on team abbreviation: "Texans D/ST" here, "Houston D/ST"
        # there, and the abbreviation is the same on both sides.
        out["DST:" + str((row.get("team") or {}).get("abbreviation") or "")] = \
            score_dst(row, games)
    return out


# --- turning a total into a multiplier ----------------------------------------
CLAMP = (0.75, 1.30)
MIN_LEVEL = 40.0        # below this a ratio is noise, not an opinion
SKIP_STATUS = {"OUT", "INJURY_RESERVE"}


def key(player: dict) -> str:
    """How a rostered player joins to FanDuel: defences by team, everyone by name."""
    if player.get("pos") == "DST":
        return "DST:" + str(player.get("pro_team_abbrev") or "")
    return norm(player["name"])


def scales(players: list[dict], ours: dict[int, float], fd: dict[str, float],
           weight: float, clamp: tuple[float, float] = CLAMP) -> dict[int, float]:
    """player_id -> multiplier. Identity for anyone we should not touch.

    `ours` is each player's own rest-of-season total under the current blend,
    summed over the same weeks FanDuel is being compared across.
    """
    if weight <= 0:
        return {}

    raw: dict[int, float] = {}
    for p in players:
        if p.get("on_ir") or p.get("status") in SKIP_STATUS:
            continue                      # ESPN's weekly timing beats a dateless total
        theirs = fd.get(key(p))
        mine = ours.get(p["player_id"], 0.0)
        if theirs is None or mine < MIN_LEVEL:
            continue
        raw[p["player_id"]] = theirs / mine
    if not raw:
        return {}

    # The median is the horizon and the league-average availability haircut. What
    # survives dividing by it is FanDuel's opinion about the PLAYER.
    mid = statistics.median(raw.values())
    if mid <= 0:
        return {}
    lo, hi = clamp
    out = {pid: min(hi, max(lo, 1.0 + weight * (r / mid - 1.0)))
           for pid, r in raw.items()}

    # Clamping is one-sided in practice -- the bullish tail is longer -- so it
    # walks the league level downwards if left alone. Re-centre on the scaled
    # players' own weight so the league total is where it started.
    #
    # Note the order: clamp, then re-centre. Re-centring can carry a clamped
    # value a fraction past its bound (0.75 becomes 0.741 when the correction is
    # 1.2%), and that is the right trade -- the clamp is a guard against one bad
    # join wrecking a lineup, while the level is an invariant the calibration
    # scale depends on. Re-clamping afterwards would restore the bound and break
    # the invariant.
    total = sum(ours[pid] for pid in out)
    moved = sum(ours[pid] * f for pid, f in out.items())
    if total > 0 and moved > 0:
        k = total / moved
        out = {pid: f * k for pid, f in out.items()}
    return out
