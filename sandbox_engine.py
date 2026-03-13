"""
sandbox_engine.py
─────────────────
Multi-market-type detection and matching for the Sandbox tab.

Detects these PM question types and matches them to bookmaker odds:
  h2h          — full-game winner (moneyline)
  spread        — point spread / handicap cover
  totals        — over/under total score
  totals_sets   — tennis: total sets O/U
  totals_games  — tennis: total games O/U
  draw          — will the match end in a draw (soccer)
  btts          — both teams to score (soccer)

No auto-betting. Analysis only.
"""

import re

from arb_engine import _team_sim, extract_h2h_teams


# ── Market-type detection ──────────────────────────────────────────────────────

_BTTS_RE = re.compile(
    r"\bboth\s+teams?\s+(?:to\s+)?score\b|\bBTTS\b",
    re.IGNORECASE,
)

_DRAW_RE = re.compile(
    r"\bdraw\b|\bend\s+in\s+(?:a\s+)?(?:draw|tie)\b",
    re.IGNORECASE,
)

_SPREAD_RE = re.compile(
    r"\bcover\b|\bspread\b|\bhandicap\b"
    r"|\b[+-]\d+(?:\.\d+)?\s*(?:points?|goals?|runs?)?\b"
    r"|\bby\s+(?:more\s+than|at\s+least|over)\s+\d",
    re.IGNORECASE,
)

_OU_RE = re.compile(
    r"\b(?:over|under)\b|\bO/U\b|\btotal[s]?\b"
    r"|\bmore\s+than\s+\d|\bless\s+than\s+\d",
    re.IGNORECASE,
)

_SETS_RE  = re.compile(r"\bsets?\b",  re.IGNORECASE)
_GAMES_RE = re.compile(r"\bgames?\b", re.IGNORECASE)

# Extract numeric lines
_SIGNED_RE = re.compile(r"([+-]\d+(?:\.\d+)?)")
_NUMBER_RE = re.compile(r"\b(\d{1,3}(?:\.\d)?)\b")


def detect_market_type(question: str) -> str:
    """
    Classify a Polymarket question into one of the supported market types.
    Returns: 'btts' | 'draw' | 'spread' | 'totals' | 'totals_sets' |
             'totals_games' | 'h2h' | 'unknown'
    """
    if _BTTS_RE.search(question):
        return "btts"
    if _DRAW_RE.search(question):
        return "draw"
    if _SPREAD_RE.search(question):
        return "spread"
    if _OU_RE.search(question):
        if _SETS_RE.search(question):
            return "totals_sets"
        if _GAMES_RE.search(question):
            return "totals_games"
        return "totals"
    # Default: treat as h2h (extract_h2h_teams may still return None for some)
    return "h2h"


def extract_line(question: str) -> float | None:
    """Extract the numeric spread/total line from a question string."""
    m = _SIGNED_RE.search(question)
    if m:
        return abs(float(m.group(1)))
    for m in _NUMBER_RE.finditer(question):
        v = float(m.group(1))
        if 0.5 < v < 300:   # plausible spread / total range
            return v
    return None


def extract_ou_direction(question: str) -> str | None:
    """Returns 'over' or 'under' for a totals question, or None if ambiguous."""
    q = question.lower()
    if re.search(r"\bover\b|\bmore\s+than\b", q):
        return "over"
    if re.search(r"\bunder\b|\bless\s+than\b", q):
        return "under"
    return None


# ── Team extraction (loose, for non-h2h questions) ────────────────────────────

_LOOSE_PATS = [
    r"(.+?)\s+vs?\.?\s+(.+?)(?:\s*[-–:|?]|\s*$)",
    r"(.+?)\s+or\s+(.+?)(?:\?|$)",
]
_STRIP_PREFIX = re.compile(
    r"^(?:will|can|the|both|total|over|under|does|do|is)\s+",
    re.IGNORECASE,
)


def _extract_teams_any(question: str):
    """Extract team pair from any question style."""
    teams = extract_h2h_teams(question)
    if teams:
        return teams
    for pat in _LOOSE_PATS:
        m = re.search(pat, question, re.IGNORECASE)
        if m:
            t1 = _STRIP_PREFIX.sub("", m.group(1).strip())
            t2 = m.group(2).strip().rstrip("?").strip()
            if t1 and t2 and len(t1) > 2 and len(t2) > 2:
                return t1, t2
    return None


# ── Event + market matching ───────────────────────────────────────────────────

def find_sandbox_event(
    question: str,
    base_events: list[dict],
    threshold: float = 0.35,
) -> tuple[dict | None, float]:
    """
    Match a PM question (any type) to a sportsbook base event using team names.
    `base_events` is a deduplicated list: [{raw_event_id, home_team, away_team, sport}].
    Returns (best_event, score).
    """
    teams = _extract_teams_any(question)
    if not teams:
        return None, 0.0
    t1, t2 = teams
    best_ev, best_sc = None, threshold
    for ev in base_events:
        home = ev.get("home_team", "")
        away = ev.get("away_team", "")
        fwd = (_team_sim(t1, home) + _team_sim(t2, away)) / 2
        rev = (_team_sim(t2, home) + _team_sim(t1, away)) / 2
        sc = max(fwd, rev)
        if sc > best_sc:
            best_sc = sc
            best_ev = ev
    return best_ev, best_sc


def find_sandbox_market(
    pm_type: str,
    question: str,
    sandbox_markets: list[dict],
    raw_event_id: str,
) -> dict | None:
    """
    Given a pm market type and matched event, find the best sandbox odds market.
    """
    candidates = [m for m in sandbox_markets if m.get("raw_event_id") == raw_event_id]

    api_type_map = {
        "totals":       "totals",
        "totals_sets":  "totals",
        "totals_games": "totals",
        "spread":       "spreads",
        "draw":         "h2h_draw",
        "btts":         "btts",
    }
    api_type = api_type_map.get(pm_type)
    if api_type is None:
        return None   # h2h and unknown don't use sandbox markets

    matches = [m for m in candidates if m.get("market_type") == api_type]
    if not matches:
        return None

    # For lines (spreads/totals), prefer the line closest to the one in the question
    pm_line = extract_line(question)
    if pm_line and api_type in ("spreads", "totals"):
        return min(matches, key=lambda m: abs((m.get("point") or 0) - pm_line))

    return matches[0]


# ── Edge calculation ──────────────────────────────────────────────────────────

# Human-readable labels for each market type
MARKET_LABELS = {
    "h2h":          "H2H",
    "spread":       "Spread",
    "totals":       "Totals O/U",
    "totals_sets":  "Sets O/U",
    "totals_games": "Games O/U",
    "draw":         "Draw",
    "btts":         "BTTS",
    "unknown":      "Unknown",
}


def calc_sandbox_edge(
    pm_yes: float,
    pm_no: float,
    pm_type: str,
    question: str,
    odds_market: dict,
) -> dict:
    """
    Compare PM YES/NO prices against a sandbox odds market (spread, totals, draw, btts).

    odds_market keys expected:
        market_type, point,
        outcome_a_name, outcome_a_dec,
        outcome_b_name, outcome_b_dec

    Returns a dict with edge metrics.
    """
    a_dec = odds_market.get("outcome_a_dec") or 0.0
    b_dec = odds_market.get("outcome_b_dec") or 0.0

    result: dict = {
        "book_a_name":    odds_market.get("outcome_a_name", ""),
        "book_b_name":    odds_market.get("outcome_b_name", ""),
        "book_a_decimal": a_dec,
        "book_b_decimal": b_dec,
        "book_a_implied": (1.0 / a_dec) if a_dec else 0.0,
        "book_b_implied": (1.0 / b_dec) if b_dec else 0.0,
        "yes_maps_to":    None,
        "yes_edge":       0.0,
        "no_edge":        0.0,
        "best_side":      None,
        "best_edge":      0.0,
        "point":          odds_market.get("point"),
    }

    if not a_dec or not b_dec:
        return result

    ai = result["book_a_implied"]
    bi = result["book_b_implied"]

    # ── Map PM YES to outcome_a or outcome_b ──
    if pm_type in ("totals", "totals_sets", "totals_games"):
        # Odds API convention: outcome_a = Over, outcome_b = Under
        direction = extract_ou_direction(question)
        if direction == "under":
            yes_impl, no_impl = bi, ai
            result["yes_maps_to"] = "under"
        else:
            yes_impl, no_impl = ai, bi
            result["yes_maps_to"] = "over"

    elif pm_type == "spread":
        # outcome_a = home cover (negative spread/favourite)
        # outcome_b = away cover (positive spread/underdog)
        m = _SIGNED_RE.search(question)
        if m and float(m.group(1)) < 0:
            yes_impl, no_impl = ai, bi   # favourite cover
            result["yes_maps_to"] = "home_cover"
        else:
            yes_impl, no_impl = bi, ai   # underdog / away cover
            result["yes_maps_to"] = "away_cover"

    else:
        # draw / btts: outcome_a = YES (draw happens / both score)
        yes_impl, no_impl = ai, bi
        result["yes_maps_to"] = "yes"

    yes_edge = yes_impl - pm_yes
    no_edge  = no_impl  - pm_no

    if yes_edge > 0 and yes_edge >= no_edge:
        best_side, best_edge = "YES", yes_edge
    elif no_edge > 0:
        best_side, best_edge = "NO", no_edge
    else:
        best_side, best_edge = None, 0.0

    result.update({
        "yes_edge":  yes_edge,
        "no_edge":   no_edge,
        "best_side": best_side,
        "best_edge": best_edge,
    })
    return result
