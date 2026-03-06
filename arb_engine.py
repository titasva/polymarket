"""
arb_engine.py
─────────────
Pure-function module for:
  1. Fuzzy matching Polymarket questions to sportsbook events
  2. Calculating edge and true-arbitrage between PM and a bookmaker
"""

import re
from difflib import SequenceMatcher


# ── Text helpers ───────────────────────────────────────────────────────────────

_STOPWORDS = {
    "the", "a", "an", "to", "vs", "v", "will", "who", "wins", "win",
    "beat", "beats", "defeat", "defeats", "in", "at", "on", "of", "for",
    "and", "or", "be", "is", "are", "their", "nfl", "nba", "mlb", "nhl",
    "ufc", "epl", "mls", "2024", "2025", "2026", "super", "bowl",
    "championship", "cup", "league", "series", "game", "match", "season",
    "week", "round", "2023", "2022", "playoffs", "playoff", "final",
}


def _norm(s: str) -> str:
    s = re.sub(r"[^\w\s]", " ", s.lower())
    return " ".join(s.split())


def _tokens(s: str) -> list[str]:
    return [t for t in _norm(s).split() if t not in _STOPWORDS and len(t) > 2]


# ── H2H detection & team extraction ──────────────────────────────────────────

# Patterns that indicate a head-to-head matchup and capture two sides
_H2H_PATTERNS = [
    # "X vs Y" / "X v Y" / "X v. Y"
    r'(.+?)\s+vs?\.?\s+(.+?)(?:\s*[-–|?:\.]|\s*$)',
    # "Will X beat/defeat Y?"
    r'(?:will|can)\s+(.+?)\s+(?:beat|defeat|win (?:against|over|vs?\.?))\s+(.+?)(?:\?|$)',
    # "X to beat/defeat Y"
    r'(.+?)\s+to\s+(?:beat|defeat|win (?:against|over|vs?\.?))\s+(.+?)(?:\?|$)',
    # "X or Y" in "who/which will win" context
    r'(?:who|which).+?:\s*(.+?)\s+or\s+(.+?)(?:\?|$)',
]


def extract_h2h_teams(question: str) -> tuple[str, str] | None:
    """
    If the question describes a head-to-head matchup, extract the two sides.
    Returns None for futures, props, and non-h2h markets.

    Examples that MATCH:
      "Lakers vs Celtics" → ("Lakers", "Celtics")
      "Will Arsenal beat Chelsea on March 10?" → ("Arsenal", "Chelsea")

    Examples that DON'T match (returns None):
      "Will the Kings make the NHL Playoffs?"
      "Who will win the Super Bowl?"
      "Will Ohtani hit 50 HRs?"
    """
    q = question.strip()
    for pat in _H2H_PATTERNS:
        m = re.search(pat, q, re.IGNORECASE)
        if m:
            t1 = m.group(1).strip()
            t2 = m.group(2).strip().rstrip("?").strip()
            # Strip leading "the"
            for prefix in ("will ", "the ", "can "):
                if t1.lower().startswith(prefix):
                    t1 = t1[len(prefix):]
                if t2.lower().startswith(prefix):
                    t2 = t2[len(prefix):]
            t1, t2 = t1.strip(), t2.strip()
            if t1 and t2:
                return t1, t2
    return None


# ── Team name similarity ─────────────────────────────────────────────────────

def _last_word(s: str) -> str:
    """Get the distinguishing team name (last word of multi-word names)."""
    words = _norm(s).split()
    return words[-1] if words else ""


def _team_sim(extracted: str, official: str) -> float:
    """
    Compare an extracted team name from a PM question against an official
    sportsbook team name.

    Heavily weights the *distinguishing* part of the name:
      "Los Angeles Kings" vs "Los Angeles FC" → low score (Kings ≠ FC)
      "Arsenal" vs "Arsenal" → 1.0
    """
    ext = _norm(extracted)
    off = _norm(official)

    if ext == off:
        return 1.0

    ext_words = ext.split()
    off_words = off.split()
    if not ext_words or not off_words:
        return 0.0

    # 1. Last-word similarity (the actual team name: "Kings", "Lakers", "Arsenal")
    last_sim = SequenceMatcher(None, _last_word(ext), _last_word(off)).ratio()

    # 2. Full sequence similarity
    full_sim = SequenceMatcher(None, ext, off).ratio()

    # 3. Word set overlap
    ext_set, off_set = set(ext_words), set(off_words)
    union = ext_set | off_set
    overlap = len(ext_set & off_set) / len(union) if union else 0.0

    # Last word is the most discriminative signal
    return last_sim * 0.50 + full_sim * 0.30 + overlap * 0.20


# ── Event matching ─────────────────────────────────────────────────────────────

def match_score(question: str, home_team: str, away_team: str) -> float:
    """
    Return a 0–1 confidence score for how well (home_team, away_team) matches
    the Polymarket question text.

    Returns 0.0 immediately if the PM question is not a head-to-head market.
    Requires BOTH extracted teams to individually match (not just one).
    """
    teams = extract_h2h_teams(question)
    if teams is None:
        return 0.0   # futures, props, non-h2h → never match an h2h event

    pm_t1, pm_t2 = teams

    # Try both orientations: pm_t1=home or pm_t1=away
    fwd_home = _team_sim(pm_t1, home_team)
    fwd_away = _team_sim(pm_t2, away_team)
    fwd = (fwd_home + fwd_away) / 2.0

    rev_home = _team_sim(pm_t2, home_team)
    rev_away = _team_sim(pm_t1, away_team)
    rev = (rev_home + rev_away) / 2.0

    if fwd >= rev:
        best, s1, s2 = fwd, fwd_home, fwd_away
    else:
        best, s1, s2 = rev, rev_home, rev_away

    # BOTH teams must match reasonably — one good match isn't enough
    if min(s1, s2) < 0.35:
        return 0.0

    return best


def guess_yes_is_home(question: str, home_team: str, away_team: str) -> bool:
    """
    Heuristic: does PM YES correspond to the home team winning?
    Returns True if the PM question's first-mentioned team maps to home.
    Always overrideable via the manual match UI.
    """
    teams = extract_h2h_teams(question)
    if teams is None:
        return True

    pm_t1, pm_t2 = teams

    # Does team1 match home or away better?
    sim_t1_home = _team_sim(pm_t1, home_team)
    sim_t1_away = _team_sim(pm_t1, away_team)

    # First-mentioned team in PM question = the YES outcome team
    # If that team is the home team → YES is home
    return sim_t1_home >= sim_t1_away


def find_best_match(
    question: str,
    events: list[dict],
    threshold: float = 0.50,
) -> tuple[dict | None, float, bool]:
    """
    Search `events` for the best match for a PM question.

    Returns (best_event, score, yes_is_home).
    best_event is None if no event exceeds `threshold`.
    """
    best_event, best_score, best_yih = None, threshold, True

    for ev in events:
        sc = match_score(question, ev.get("home_team", ""), ev.get("away_team", ""))
        if sc > best_score:
            best_score = sc
            best_event = ev
            best_yih = guess_yes_is_home(
                question, ev["home_team"], ev["away_team"]
            )

    return best_event, best_score, best_yih


# ── Arbitrage calculation ──────────────────────────────────────────────────────

def calc_arb(
    pm_yes: float,
    pm_no: float,
    home_dec: float,
    away_dec: float,
    yes_is_home: bool,
) -> dict:
    """
    Compare Polymarket YES/NO prices against a bookmaker's home/away decimal
    odds for the same event.

    pm_yes      : PM price for YES (0–1, equals the implied probability)
    pm_no       : PM price for NO  (0–1, usually ≈ 1 − pm_yes)
    home_dec    : bookmaker decimal odds for home team (e.g. 2.10)
    away_dec    : bookmaker decimal odds for away team
    yes_is_home : True  → PM YES = home team wins
                  False → PM YES = away team wins

    Returns a dict with:
      yes_edge, no_edge, max_edge : differences in implied probabilities
                                    (+ve = PM is cheaper than book → bet PM)
      is_arb          : True if guaranteed profit exists
      arb_return_pct  : guaranteed return % (0 if not arb)
      best_strategy   : string describing optimal strategy
      pm_stake_pct    : fraction of total bankroll to stake on PM side
      book_stake_pct  : fraction of total bankroll to stake on book side
    """
    # Map home/away to YES/NO according to the match orientation
    if yes_is_home:
        book_yes_dec, book_no_dec = home_dec, away_dec
    else:
        book_yes_dec, book_no_dec = away_dec, home_dec

    book_yes_impl = (1.0 / book_yes_dec) if book_yes_dec else 0.0
    book_no_impl  = (1.0 / book_no_dec)  if book_no_dec  else 0.0

    # Edge: positive means PM is underpricing relative to book (PM is the value bet)
    yes_edge = book_yes_impl - pm_yes
    no_edge  = book_no_impl  - pm_no
    max_edge = max(abs(yes_edge), abs(no_edge))

    # ── True arb: cover both outcomes across PM and book ──────────────────────
    # Strategy A: buy PM YES + bet book's NO outcome
    cost_a = pm_yes + book_no_impl
    is_a   = cost_a < 1.0
    ret_a  = (1.0 / cost_a - 1.0) * 100 if cost_a > 0 else 0.0

    # Strategy B: buy PM NO + bet book's YES outcome
    cost_b = pm_no + book_yes_impl
    is_b   = cost_b < 1.0
    ret_b  = (1.0 / cost_b - 1.0) * 100 if cost_b > 0 else 0.0

    is_arb = is_a or is_b

    if is_a and ret_a >= ret_b:
        strategy    = "BUY PM YES + BET BOOK NO"
        arb_return  = ret_a
        total_impl  = cost_a
        pm_stake    = book_no_impl / cost_a
        book_stake  = pm_yes / cost_a
    elif is_b:
        strategy    = "BUY PM NO + BET BOOK YES"
        arb_return  = ret_b
        total_impl  = cost_b
        pm_stake    = book_yes_impl / cost_b
        book_stake  = pm_no / cost_b
    else:
        # No guaranteed arb — show the best pure edge
        if abs(yes_edge) >= abs(no_edge):
            strategy = "EDGE: BUY PM YES" if yes_edge > 0 else "EDGE: BET BOOK YES"
        else:
            strategy = "EDGE: BUY PM NO"  if no_edge  > 0 else "EDGE: BET BOOK NO"
        arb_return = 0.0
        total_impl = min(cost_a, cost_b)
        pm_stake   = 1.0
        book_stake = 0.0

    return {
        "pm_yes":          pm_yes,
        "pm_no":           pm_no,
        "book_yes_decimal": book_yes_dec,
        "book_no_decimal":  book_no_dec,
        "book_yes_implied": book_yes_impl,
        "book_no_implied":  book_no_impl,
        "yes_edge":        yes_edge,
        "no_edge":         no_edge,
        "max_edge":        max_edge,
        "is_arb":          is_arb,
        "arb_return_pct":  arb_return,
        "best_strategy":   strategy,
        "total_implied":   total_impl,
        "pm_stake_pct":    pm_stake,
        "book_stake_pct":  book_stake,
    }
