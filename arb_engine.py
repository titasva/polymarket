"""
arb_engine.py
─────────────
Pure-function module for:
  1. Fuzzy matching Polymarket questions to sportsbook h2h events
  2. Calculating the edge between PM implied probability and a bookmaker
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

# Generic club suffixes shared across many teams — not distinctive
_GENERIC_SUFFIXES = {"fc", "sc", "ac", "cf", "afc", "rfc", "utd", "united", "city"}


def _norm(s: str) -> str:
    s = re.sub(r"[^\w\s]", " ", s.lower())
    return " ".join(s.split())


def _tokens(s: str) -> list[str]:
    return [t for t in _norm(s).split() if t not in _STOPWORDS and len(t) > 2]


# ── Market-type detection ──────────────────────────────────────────────────────

_TOTALS_RE = re.compile(
    r"\bO/U\b"
    r"|\bover[-/\s]under\b"
    r"|\btotal[s]?\s*(?:goals?|points?|runs?|yards?)?\s*\d",
    re.IGNORECASE,
)


def _is_h2h_winner_question(question: str) -> bool:
    return not _TOTALS_RE.search(question)


# ── H2H team extraction ────────────────────────────────────────────────────────

_H2H_PATTERNS = [
    r"(.+?)\s+vs?\.?\s+(.+?)(?:\s*[-–|?:\.]|\s*$)",
    r"(?:will|can)\s+(.+?)\s+(?:beat|defeat|win\s+(?:against|over|vs?\.?))\s+(.+?)(?:\?|$)",
    r"(.+?)\s+to\s+(?:beat|defeat|win\s+(?:against|over|vs?\.?))\s+(.+?)(?:\?|$)",
    r"(?:who|which).+?:\s*(.+?)\s+or\s+(.+?)(?:\?|$)",
]


def extract_h2h_teams(question: str) -> tuple[str, str] | None:
    """
    Extract both teams from an h2h winner question.
    Returns None for O/U totals, futures, props, and non-h2h questions.
    """
    if not _is_h2h_winner_question(question):
        return None
    q = question.strip()
    for pat in _H2H_PATTERNS:
        m = re.search(pat, q, re.IGNORECASE)
        if m:
            t1 = m.group(1).strip()
            t2 = m.group(2).strip().rstrip("?").strip()
            for prefix in ("will ", "the ", "can "):
                if t1.lower().startswith(prefix):
                    t1 = t1[len(prefix):]
                if t2.lower().startswith(prefix):
                    t2 = t2[len(prefix):]
            t1, t2 = t1.strip(), t2.strip()
            if t1 and t2:
                return t1, t2
    return None


# ── Team name similarity ───────────────────────────────────────────────────────

def _key_word(s: str) -> str:
    """Most distinctive word of a team name, skipping generic suffixes."""
    words = _norm(s).split()
    for i in range(len(words) - 1, -1, -1):
        if words[i] not in _GENERIC_SUFFIXES:
            return words[i]
    return words[-1] if words else ""


def _team_sim(extracted: str, official: str) -> float:
    ext = _norm(extracted)
    off = _norm(official)
    if ext == off:
        return 1.0
    ext_words, off_words = ext.split(), off.split()
    if not ext_words or not off_words:
        return 0.0
    kw_sim   = SequenceMatcher(None, _key_word(ext), _key_word(off)).ratio()
    full_sim = SequenceMatcher(None, ext, off).ratio()
    ext_set, off_set = set(ext_words), set(off_words)
    union    = ext_set | off_set
    overlap  = len(ext_set & off_set) / len(union) if union else 0.0
    return kw_sim * 0.50 + full_sim * 0.30 + overlap * 0.20


# ── Event matching ─────────────────────────────────────────────────────────────

def match_score(question: str, home_team: str, away_team: str) -> float:
    teams = extract_h2h_teams(question)
    if teams is None:
        return 0.0
    pm_t1, pm_t2 = teams
    fwd_h, fwd_a = _team_sim(pm_t1, home_team), _team_sim(pm_t2, away_team)
    rev_h, rev_a = _team_sim(pm_t2, home_team), _team_sim(pm_t1, away_team)
    fwd, rev = (fwd_h + fwd_a) / 2, (rev_h + rev_a) / 2
    if fwd >= rev:
        best, s1, s2 = fwd, fwd_h, fwd_a
    else:
        best, s1, s2 = rev, rev_h, rev_a
    if min(s1, s2) < 0.40:
        return 0.0
    return best


def guess_yes_is_home(question: str, home_team: str, away_team: str) -> bool:
    teams = extract_h2h_teams(question)
    if teams is None:
        return True
    pm_t1, _ = teams
    return _team_sim(pm_t1, home_team) >= _team_sim(pm_t1, away_team)


def find_best_match(
    question: str,
    events: list[dict],
    threshold: float = 0.50,
) -> tuple[dict | None, float, bool]:
    best_event, best_score, best_yih = None, threshold, True
    for ev in events:
        sc = match_score(question, ev.get("home_team", ""), ev.get("away_team", ""))
        if sc > best_score:
            best_score = sc
            best_event = ev
            best_yih   = guess_yes_is_home(question, ev["home_team"], ev["away_team"])
    return best_event, best_score, best_yih


# ── Edge calculation ───────────────────────────────────────────────────────────

def calc_edge(
    pm_yes: float,
    pm_no: float,
    home_dec: float,
    away_dec: float,
    yes_is_home: bool,
) -> dict:
    """
    Compare Polymarket YES/NO prices against a bookmaker's decimal odds and
    return edge metrics.

    Edge definition:
      yes_edge = book_yes_implied − pm_yes
        +ve → book thinks YES more likely than PM does → PM YES is cheap
        -ve → PM is overpricing YES relative to book
      no_edge  = book_no_implied − pm_no  (same logic for NO)

    best_side: which PM outcome is underpriced ("YES", "NO", or None)
    best_edge: the positive edge value on that side (0 if no positive edge)
    bet_price: the PM price to pay for best_side
    """
    if yes_is_home:
        book_yes_dec, book_no_dec = home_dec, away_dec
    else:
        book_yes_dec, book_no_dec = away_dec, home_dec

    book_yes_impl = (1.0 / book_yes_dec) if book_yes_dec else 0.0
    book_no_impl  = (1.0 / book_no_dec)  if book_no_dec  else 0.0

    yes_edge = book_yes_impl - pm_yes   # +ve = PM cheap for YES
    no_edge  = book_no_impl  - pm_no    # +ve = PM cheap for NO
    max_edge = max(abs(yes_edge), abs(no_edge))

    # Determine which side (if any) has a positive edge on PM
    if yes_edge > 0 and yes_edge >= no_edge:
        best_side  = "YES"
        best_edge  = yes_edge
        bet_price  = pm_yes
        book_impl  = book_yes_impl
    elif no_edge > 0:
        best_side  = "NO"
        best_edge  = no_edge
        bet_price  = pm_no
        book_impl  = book_no_impl
    else:
        best_side  = None
        best_edge  = 0.0
        bet_price  = None
        book_impl  = 0.0

    return {
        "pm_yes":           pm_yes,
        "pm_no":            pm_no,
        "book_yes_decimal": book_yes_dec,
        "book_no_decimal":  book_no_dec,
        "book_yes_implied": book_yes_impl,
        "book_no_implied":  book_no_impl,
        "yes_edge":         yes_edge,
        "no_edge":          no_edge,
        "max_edge":         max_edge,
        "best_side":        best_side,
        "best_edge":        best_edge,
        "bet_price":        bet_price,
        "book_impl":        book_impl,
    }
