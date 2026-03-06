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


def _seq_sim(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _word_overlap(a: str, b: str) -> float:
    wa, wb = set(_tokens(a)), set(_tokens(b))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


# ── Event matching ─────────────────────────────────────────────────────────────

def match_score(question: str, home_team: str, away_team: str) -> float:
    """
    Return a 0–1 confidence score for how well (home_team, away_team) matches
    the Polymarket question text.
    """
    q_tokens = set(_tokens(question))
    if not q_tokens:
        return 0.0

    combined = home_team + " " + away_team

    # 1. How many PM tokens appear in the event team names?
    ev_tokens = set(_tokens(combined))
    token_hit = len(q_tokens & ev_tokens) / len(q_tokens)

    # 2. Sequence similarity between full strings
    seq = _seq_sim(question, home_team + " vs " + away_team)

    # 3. Word-set Jaccard
    jaccard = _word_overlap(question, combined)

    # Weighted combination
    score = token_hit * 0.50 + seq * 0.25 + jaccard * 0.25
    return min(score, 1.0)


def guess_yes_is_home(question: str, home_team: str, away_team: str) -> bool:
    """
    Heuristic: does PM YES correspond to the home team winning?
    Returns True if PM question appears to reference the home team first/more.
    This is always overrideable via the manual match UI.
    """
    q_tok = _tokens(question)
    home_tok = set(_tokens(home_team))
    away_tok = set(_tokens(away_team))

    home_hits = sum(1 for t in q_tok if t in home_tok)
    away_hits = sum(1 for t in q_tok if t in away_tok)

    if home_hits != away_hits:
        return home_hits > away_hits

    # Fall back to positional: whichever team appears first in the question
    q_norm = _norm(question)
    home_pos = min(
        (q_norm.find(t) for t in home_tok if t in q_norm),
        default=9999,
    )
    away_pos = min(
        (q_norm.find(t) for t in away_tok if t in q_norm),
        default=9999,
    )
    return home_pos <= away_pos


def find_best_match(
    question: str,
    events: list[dict],
    threshold: float = 0.38,
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
