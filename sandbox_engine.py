"""
sandbox_engine.py
─────────────────
Multi-market-type detection and matching for the Sandbox tab.

Detects these PM question types and matches them to bookmaker odds:
  spread        — point spread / handicap cover
  totals        — over/under total score
  totals_sets   — tennis: total sets O/U
  totals_games  — tennis: total games O/U
  draw          — will the match end in a draw (soccer)
  btts          — both teams to score (soccer)
  h2h           — full-game winner (handled by main pipeline, skipped here)

No auto-betting. Analysis only.
"""

import re

from arb_engine import _team_sim, extract_h2h_teams


# ── Market-type detection ──────────────────────────────────────────────────────

_BTTS_RE = re.compile(
    r"\bboth\s+teams?\s+(?:to\s+)?score\b"
    r"|\bBTTS\b"
    r"|\bboth\s+sides?\s+(?:to\s+)?score\b"
    r"|\beach\s+team\s+(?:to\s+)?score\b",
    re.IGNORECASE,
)

_DRAW_RE = re.compile(
    r"\bend\s+in\s+(?:a\s+)?(?:draw|tie)\b"
    r"|\bfinish\s+(?:in\s+a\s+)?(?:draw|tie)\b"
    r"|\bdraw\s+(?:yes|no|\?)\b"
    r"|\bwill\s+(?:it\s+be\s+a\s+)?draw\b"
    r"|\bdraw\s+result\b"
    r"|\btie\s+(?:yes|no|\?)\b",
    re.IGNORECASE,
)

_SPREAD_RE = re.compile(
    r"\bcover[s]?\b"
    r"|\bspread\b"
    r"|\bhandicap\b"
    r"|\b[+-]\d+(?:\.\d+)?\s*(?:points?|goals?|runs?|games?)?\b"   # explicit ±N line
    r"|\bby\s+(?:more\s+than|at\s+least|over)\s+\d"                # by more than N
    r"|\bby\s+\d+\+?\b"                                             # by 2+ or by 3
    r"|\bwin[s]?\s+by\b"                                            # wins by
    r"|\bmargin\b",                                                  # margin of victory
    re.IGNORECASE,
)

_OU_RE = re.compile(
    r"\b(?:over|under)\b"
    r"|\bO/U\b"
    r"|\btotal[s]?\b"
    r"|\bmore\s+than\s+\d"
    r"|\bless\s+than\s+\d"
    r"|\bat\s+least\s+\d+\s*(?:goals?|points?|runs?|sets?|games?)\b"
    r"|\b\d+\+\s*(?:goals?|points?|runs?|corners?|sets?|games?)\b"  # "2+ goals"
    r"|\bhow\s+many\b",
    re.IGNORECASE,
)

_SETS_RE  = re.compile(r"\bsets?\b",  re.IGNORECASE)
_GAMES_RE = re.compile(r"\bgames?\b", re.IGNORECASE)

# Numeric line extraction
_SIGNED_RE = re.compile(r"([+-]\d+(?:\.\d+)?)")
_NUMBER_RE  = re.compile(r"\b(\d{1,3}(?:\.\d+)?)\b")


def detect_market_type(question: str) -> str:
    """
    Classify a PM question into one of the supported sandbox market types.
    Returns: 'btts' | 'draw' | 'spread' | 'totals' | 'totals_sets' |
             'totals_games' | 'h2h'
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
    return "h2h"   # default — handled by main pipeline


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


def extract_ou_direction(question: str) -> str:
    """
    Returns 'over' or 'under' for a totals question.
    Defaults to 'over' when ambiguous — most PM totals questions frame YES as the
    exciting outcome (over) or are phrased "will total exceed X?".
    """
    q = question.lower()
    if re.search(r"\bunder\b|\bless\s+than\b", q):
        return "under"
    # 'over', 'more than', 'at least', 'N+ goals', or ambiguous → default over
    return "over"


# ── Team extraction ────────────────────────────────────────────────────────────

# Patterns that indicate the END of a team name in longer question strings
_TRAILING_RE = re.compile(
    r"\s+(?:"
    r"end[s]?\s+in"
    r"|finish(?:es)?\s+(?:in|as)"
    r"|win[s]?\s+(?:the|their|a)"
    r"|beat[s]?\s"
    r"|score[s]?\b(?!\s+\d)"     # "score" but not "score N goals"
    r"|keep[s]?\s+a"
    r"|draw[s]?\s+(?:with|in|no)"
    r"|advance[s]?"
    r"|qualify|qualified"
    r"|to\s+(?:win|score|beat|advance|qualify)"
    r"|by\s+\d"                   # "by 2 goals" → stop before "by"
    r")\b.*$",
    re.IGNORECASE,
)

# Question-word prefixes to strip from the start of an extracted team name
_LEAD_RE = re.compile(
    r"^(?:will|can|the|both|total|over|under|does|do|is|are|was|were|would|should|could|who|what|when|which|a|an)\s+",
    re.IGNORECASE,
)


def _clean_team(raw: str) -> str:
    """
    Scrub leading question words and trailing clause fragments from a raw team
    string produced by the regex extractor.

    Examples:
      "Will Arsenal"            → "Arsenal"
      "Chelsea end in a draw"   → "Chelsea"
      "both teams score in Ajax" → "Ajax"  (after multi-pass leading strip)
    """
    s = raw.strip()
    # Strip leading question words — iterate until stable
    for _ in range(5):
        s2 = _LEAD_RE.sub("", s)
        if s2 == s:
            break
        s = s2
    # Strip trailing clauses
    s = _TRAILING_RE.sub("", s).strip().rstrip("?.!,").strip()
    return s


_LOOSE_PATS = [
    # Standard "A vs B" — stop at common separator
    r"(.+?)\s+vs?\.?\s+(.+?)(?:\s*[-–:|?]|\s*$)",
    # "A or B"
    r"(.+?)\s+or\s+(.+?)(?:\?|$)",
]


def _extract_teams_any(question: str):
    """
    Extract a (team1, team2) pair from any style of PM question.

    Strategy:
    1. Try the strict h2h extractor from arb_engine (best for clean "A vs B" questions)
    2. Fall back to loose patterns with full team-name cleanup applied to both sides
    """
    # Try the standard extractor first
    teams = extract_h2h_teams(question)
    if teams:
        t1, t2 = teams
        # arb_engine may leave trailing clause words in t2 — clean them
        t2c = _clean_team(t2)
        if t2c and len(t2c) > 2:
            return t1, t2c
        # If cleanup wiped out t2, fall through to loose patterns

    # Loose patterns with full cleanup on both sides
    for pat in _LOOSE_PATS:
        m = re.search(pat, question, re.IGNORECASE)
        if m:
            t1 = _clean_team(m.group(1).strip())
            t2 = _clean_team(m.group(2).strip().rstrip("?!.").strip())
            if t1 and t2 and len(t1) > 2 and len(t2) > 2:
                return t1, t2
    return None


# ── Event + market matching ───────────────────────────────────────────────────

_MIN_TEAM_SIM = 0.55   # both teams must individually exceed this (mirrors main pipeline)


def find_sandbox_event(
    question: str,
    base_events: list[dict],
    threshold: float = 0.45,
) -> tuple[dict | None, float]:
    """
    Match a PM question (any type) to a sportsbook base event using team names.
    `base_events` is a deduplicated list: [{raw_event_id, home_team, away_team, sport}].
    Returns (best_event, score).

    Both teams must individually exceed _MIN_TEAM_SIM so that one strong
    partial match cannot drag up the score for a completely wrong opponent.
    """
    teams = _extract_teams_any(question)
    if not teams:
        return None, 0.0
    t1, t2 = teams
    best_ev, best_sc = None, threshold
    for ev in base_events:
        home = ev.get("home_team", "")
        away = ev.get("away_team", "")

        fwd_h = _team_sim(t1, home)
        fwd_a = _team_sim(t2, away)
        rev_h = _team_sim(t2, home)
        rev_a = _team_sim(t1, away)

        # Both teams must individually match — one strong match can't carry the other
        fwd_ok = fwd_h >= _MIN_TEAM_SIM and fwd_a >= _MIN_TEAM_SIM
        rev_ok = rev_h >= _MIN_TEAM_SIM and rev_a >= _MIN_TEAM_SIM
        if not fwd_ok and not rev_ok:
            continue

        fwd = (fwd_h + fwd_a) / 2 if fwd_ok else 0.0
        rev = (rev_h + rev_a) / 2 if rev_ok else 0.0
        sc  = max(fwd, rev)
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
    Given a pm market type and matched event id, find the best sandbox odds market.

    For lines (spreads / totals), selects the bookmaker line closest to the
    numeric value mentioned in the PM question (e.g. O/U 2.5 → find 2.5 line).
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
        return None

    matches = [m for m in candidates if m.get("market_type") == api_type]
    if not matches:
        return None

    # For lines, require an exact match (within 0.5 pts) — any deviation
    # means a different question and produces a meaningless edge calculation.
    pm_line = extract_line(question)
    if pm_line is not None and api_type in ("spreads", "totals"):
        exact = [m for m in matches if abs((m.get("point") or 0) - pm_line) <= 0.5]
        if not exact:
            return None
        return min(exact, key=lambda m: abs((m.get("point") or 0) - pm_line))

    # For draw/btts: no line to match — pick first available
    return matches[0]


def find_sandbox_market_relaxed(
    pm_type: str,
    question: str,
    sandbox_markets: list[dict],
    raw_event_id: str,
) -> tuple[dict | None, float | None]:
    """
    Like find_sandbox_market but for the betting path only.
    Returns (closest_market, pm_line) for totals without enforcing a line
    distance limit — the caller is responsible for checking line favorability.
    Returns (None, None) if no totals market exists for the event.
    """
    api_type_map = {
        "totals":       "totals",
        "totals_sets":  "totals",
        "totals_games": "totals",
    }
    api_type = api_type_map.get(pm_type)
    if api_type is None:
        return None, None

    pm_line = extract_line(question)
    if pm_line is None:
        return None, None

    candidates = [m for m in sandbox_markets
                  if m.get("raw_event_id") == raw_event_id
                  and m.get("market_type") == api_type]
    if not candidates:
        return None, None

    best = min(candidates, key=lambda m: abs((m.get("point") or 0) - pm_line))
    return best, pm_line


def is_favorable_totals_line(pm_line: float, book_line: float, bet_direction: str) -> bool:
    """
    Returns True if the line relationship is valid for a bet:
      - Exact match (within 0.5): always OK
      - PM line > book line AND bet is UNDER: OK — extra cushion on the under
      - PM line < book line AND bet is OVER:  OK — extra cushion on the over
    Any other mismatch is unfavorable (we'd be getting a worse number than the
    book, so the book's implied prob underestimates our true probability of winning).
    """
    if abs(pm_line - book_line) <= 0.5:
        return True
    if pm_line > book_line and bet_direction == "under":
        return True
    if pm_line < book_line and bet_direction == "over":
        return True
    return False


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
}


def calc_sandbox_edge(
    pm_yes: float,
    pm_no: float,
    pm_type: str,
    question: str,
    odds_market: dict,
) -> dict:
    """
    Compare PM YES/NO prices against a sandbox bookmaker market.

    Mapping rules:
      totals/sets/games : YES → Over  (unless question says "under")
      spread            : YES → home cover (if negative spread in question)
                                away cover  (if positive / unsigned)
      draw              : YES → Draw
      btts              : YES → Both score (outcome_a)
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

    if pm_type in ("totals", "totals_sets", "totals_games"):
        # Odds API: outcome_a = Over, outcome_b = Under
        if extract_ou_direction(question) == "under":
            yes_impl, no_impl = bi, ai
            result["yes_maps_to"] = "under"
        else:
            yes_impl, no_impl = ai, bi
            result["yes_maps_to"] = "over"

    elif pm_type == "spread":
        # outcome_a = home cover (favourite, negative spread)
        # outcome_b = away cover (underdog, positive spread)
        m = _SIGNED_RE.search(question)
        if m and float(m.group(1)) < 0:
            yes_impl, no_impl = ai, bi
            result["yes_maps_to"] = "home_cover"
        else:
            yes_impl, no_impl = bi, ai
            result["yes_maps_to"] = "away_cover"

    else:
        # draw / btts: outcome_a = affirmative (draw / both score)
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
