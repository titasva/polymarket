"""
fetcher.py
──────────
Fetches Polymarket markets + bookmaker odds, runs matching & edge calculation,
optionally triggers auto-bets, and writes results to SQLite.

Run standalone:  python fetcher.py
"""

import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timezone

import requests
import schedule

from arb_engine import calc_edge, find_best_match
from config import (
    DB_PATH, DEFAULT_BOOKMAKERS, DEFAULT_FETCH_MIN,
    MATCH_THRESHOLD, MIN_VOLUME, ODDS_API_BASE,
    PM_MARKET_LIMIT, TRACKED_SPORTS,
)
from init_db import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "polymarket-edge-tracker/1.0"}


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _conn():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def get_setting(key: str, default: str = "") -> str:
    c = _conn()
    row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    c.close()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    c = _conn()
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    c.commit()
    c.close()


# ── HTTP helper ────────────────────────────────────────────────────────────────

def _get(url, params=None):
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r, r.json()
    except requests.exceptions.RequestException as exc:
        log.warning("GET %s  →  %s", url, exc)
        return None, None


# ── Polymarket ─────────────────────────────────────────────────────────────────

def fetch_pm_markets() -> list[dict]:
    log.info("Fetching Polymarket markets (sports only) …")
    out, offset, batch = [], 0, 100
    while len(out) < PM_MARKET_LIMIT:
        _, data = _get(
            "https://gamma-api.polymarket.com/markets",
            params={
                "active": "true", "closed": "false",
                "limit": batch, "offset": offset,
                "order": "volume", "ascending": "false",
                "tag_slug": "sports",
            },
        )
        if not data:
            break
        out.extend(data)
        if len(data) < batch:
            break
        offset += batch
    log.info("  → %d raw PM markets", len(out))
    return out


def _parse_json_field(raw):
    """Parse a field that may be a JSON-encoded string or already a list."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return []
    return raw or []


def _parse_pm(m: dict) -> dict | None:
    # outcomePrices is often a JSON-encoded string from the Gamma API
    raw_prices = _parse_json_field(m.get("outcomePrices"))
    try:
        yes = float(raw_prices[0])
        no  = float(raw_prices[1]) if len(raw_prices) > 1 else round(1 - yes, 6)
    except (ValueError, TypeError, IndexError):
        yes, no = 0.5, 0.5

    # Extract outcome token IDs (needed for placing bets)
    # Gamma API returns clobTokenIds as JSON string ["yes_id", "no_id"]
    yes_token_id = no_token_id = ""
    clob_ids = _parse_json_field(m.get("clobTokenIds"))
    if len(clob_ids) >= 2:
        yes_token_id = str(clob_ids[0])
        no_token_id  = str(clob_ids[1])
    else:
        # Fallback: tokens array
        raw_tokens = _parse_json_field(m.get("tokens"))
        for tok in raw_tokens:
            if isinstance(tok, dict):
                outcome = tok.get("outcome", "").lower()
                tid     = tok.get("token_id", "") or tok.get("tokenId", "")
                if outcome in ("yes", "1"):
                    yes_token_id = tid
                elif outcome in ("no", "0"):
                    no_token_id = tid

    tags = m.get("tags") or []
    cat  = ""
    if tags:
        t0  = tags[0]
        cat = (t0.get("label") or t0.get("slug") or "") if isinstance(t0, dict) else str(t0)

    mid = m.get("id") or m.get("conditionId", "")
    if not mid:
        return None

    end_date = m.get("endDate", "")
    if end_date:
        try:
            ed = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            if ed <= datetime.now(timezone.utc):
                return None
        except ValueError:
            pass

    return {
        "id":           mid,
        "question":     m.get("question", ""),
        "yes_price":    yes,
        "no_price":     no,
        "yes_token_id": yes_token_id,
        "no_token_id":  no_token_id,
        "volume":       float(m.get("volume") or 0),
        "end_date":     end_date,
        "slug":         m.get("slug", ""),
        "category":     cat,
    }


# ── The Odds API ───────────────────────────────────────────────────────────────

def fetch_active_sports(api_key: str) -> list[str]:
    """
    Query /v4/sports to get all sports currently in season that have h2h markets.
    Costs 1 API request. Falls back to TRACKED_SPORTS if the call fails.
    """
    _, data = _get(f"{ODDS_API_BASE}/sports", params={"apiKey": api_key, "all": "false"})
    if not data:
        log.warning("Could not fetch sports list — using config defaults")
        return list(TRACKED_SPORTS)

    # Keep only sports with h2h odds (group_id indicates sport family)
    # Filter out outrights/futures (title contains "Winner", "Champion", etc.)
    _OUTRIGHT_RE = re.compile(r"\bwinner|champion|outright|season\b", re.IGNORECASE)
    keys = [
        s["key"] for s in data
        if s.get("active") and s.get("has_outrights") is not True
        and not _OUTRIGHT_RE.search(s.get("title", ""))
    ]
    log.info("Active sports from Odds API: %d", len(keys))
    return keys


def fetch_odds_events(api_key: str, bookmakers: str = "pinnacle") -> list[dict]:
    if not api_key:
        log.warning("No Odds API key — skipping odds fetch")
        return []

    # Use user-configured list, or auto-discover active sports
    sports_raw = get_setting("tracked_sports", "").strip()
    if sports_raw:
        sport_list = [s.strip() for s in sports_raw.split(",") if s.strip()]
        log.info("Using configured sports list (%d sports)", len(sport_list))
    else:
        sport_list = fetch_active_sports(api_key)

    out, remaining = [], None

    for sport in sport_list:
        r, data = _get(
            f"{ODDS_API_BASE}/sports/{sport}/odds",
            params={
                "apiKey":     api_key,
                "regions":    "us,eu",
                "markets":    "h2h",
                "bookmakers": bookmakers,
                "oddsFormat": "decimal",
            },
        )
        if data is None:
            continue
        if r is not None:
            remaining = r.headers.get("x-requests-remaining", remaining)

        for ev in data:
            out.extend(_parse_odds_event(ev, sport))

        log.info("  → %s: %d events", sport, len(data))
        time.sleep(0.4)

    if remaining is not None:
        set_setting("odds_api_remaining", str(remaining))
        log.info("Odds API requests remaining: %s", remaining)

    log.info("Total odds events: %d", len(out))
    return out


def _parse_odds_event(ev: dict, sport: str) -> list[dict]:
    # Drop events that have already started
    commence = ev.get("commence_time", "")
    if commence:
        try:
            ct = datetime.fromisoformat(commence.replace("Z", "+00:00"))
            if ct <= datetime.now(timezone.utc):
                return []
        except ValueError:
            pass

    results = []
    home = ev.get("home_team", "")
    away = ev.get("away_team", "")

    for bk in ev.get("bookmakers", []):
        for mkt in bk.get("markets", []):
            if mkt.get("key") != "h2h":
                continue
            outcomes = mkt.get("outcomes", [])
            if len(outcomes) < 2:
                continue

            home_dec = away_dec = draw_dec = None
            for o in outcomes:
                name  = o.get("name", "")
                price = float(o.get("price", 0) or 0)
                if name == home:
                    home_dec = price
                elif name == away:
                    away_dec = price
                elif name.lower() in ("draw", "tie"):
                    draw_dec = price

            if not home_dec or not away_dec:
                prices = [float(o.get("price", 0)) for o in outcomes]
                if len(prices) >= 2:
                    home_dec, away_dec = prices[0], prices[1]
                    if len(prices) >= 3:
                        draw_dec = prices[2]
                if not home_dec or not away_dec:
                    continue

            home_impl = 1.0 / home_dec if home_dec else 0.0
            away_impl = 1.0 / away_dec if away_dec else 0.0
            overround = home_impl + away_impl + (1.0 / draw_dec if draw_dec else 0.0)

            bk_key = bk.get("key", "")
            results.append({
                "id":            ev.get("id", "") + "_" + bk_key,
                "raw_event_id":  ev.get("id", ""),
                "sport":         sport,
                "competition":   ev.get("sport_title", sport),
                "home_team":     home,
                "away_team":     away,
                "commence_time": commence,
                "bookmaker":     bk.get("title", bk_key),
                "bookmaker_key": bk_key,
                "home_decimal":  home_dec,
                "away_decimal":  away_dec,
                "draw_decimal":  draw_dec,
                "home_implied":  home_impl,
                "away_implied":  away_impl,
                "overround":     overround,
            })
    return results


# ── DB writes ──────────────────────────────────────────────────────────────────

def _save_pm(markets: list[dict]) -> None:
    c = _conn()
    now = datetime.utcnow().isoformat()
    for m in markets:
        c.execute(
            """INSERT OR REPLACE INTO pm_markets
               (id, question, yes_price, no_price, yes_token_id, no_token_id,
                volume, end_date, slug, category, last_updated)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (m["id"], m["question"], m["yes_price"], m["no_price"],
             m["yes_token_id"], m["no_token_id"],
             m["volume"], m["end_date"], m["slug"], m["category"], now),
        )
    c.commit()
    c.close()


def _save_events(events: list[dict]) -> None:
    c = _conn()
    now = datetime.utcnow().isoformat()
    for e in events:
        c.execute(
            """INSERT OR REPLACE INTO odds_events
               (id, raw_event_id, sport, competition, home_team, away_team,
                commence_time, bookmaker, bookmaker_key,
                home_decimal, away_decimal, draw_decimal,
                home_implied, away_implied, overround, last_updated)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (e["id"], e["raw_event_id"], e["sport"], e["competition"],
             e["home_team"], e["away_team"], e["commence_time"],
             e["bookmaker"], e["bookmaker_key"],
             e["home_decimal"], e["away_decimal"], e.get("draw_decimal"),
             e["home_implied"], e["away_implied"], e["overround"], now),
        )
    c.commit()
    c.close()


def _save_match_edge(pm: dict, ev: dict, score: float, yih: bool, edge: dict) -> None:
    c = _conn()
    now = datetime.utcnow().isoformat()

    # Skip if this market has a manual override pointing to a different event
    row = c.execute(
        "SELECT is_manual, event_id FROM market_matches WHERE pm_id=?", (pm["id"],)
    ).fetchone()
    if row and row["is_manual"] and row["event_id"] != ev["id"]:
        c.close()
        return

    c.execute(
        """INSERT OR REPLACE INTO market_matches
           (pm_id, event_id, pm_yes_is_home, match_score, is_manual, created_at)
           VALUES (?,?,?,?,0,?)""",
        (pm["id"], ev["id"], int(yih), round(score, 4), now),
    )
    c.execute(
        """INSERT INTO edge_log
           (pm_id, event_id, pm_yes_price, pm_no_price,
            book_yes_decimal, book_no_decimal,
            book_yes_implied, book_no_implied,
            yes_edge, no_edge, max_edge, best_side, best_edge, detected_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (pm["id"], ev["id"],
         edge["pm_yes"], edge["pm_no"],
         edge["book_yes_decimal"], edge["book_no_decimal"],
         edge["book_yes_implied"], edge["book_no_implied"],
         edge["yes_edge"], edge["no_edge"], edge["max_edge"],
         edge["best_side"], edge["best_edge"], now),
    )
    c.commit()
    c.close()


# ── Main cycle ─────────────────────────────────────────────────────────────────

def fetch_all() -> None:
    log.info("═══ Fetch cycle start ═══")

    api_key    = get_setting("odds_api_key")
    bookmakers = get_setting("bookmakers", DEFAULT_BOOKMAKERS)
    threshold  = float(get_setting("match_threshold", str(MATCH_THRESHOLD)))
    min_vol    = float(get_setting("min_volume", str(MIN_VOLUME)))

    # 1. Polymarket
    raw      = fetch_pm_markets()
    markets  = [m for m in (_parse_pm(r) for r in raw) if m and m["volume"] >= min_vol]
    _save_pm(markets)
    log.info("Stored %d PM markets (vol ≥ $%s)", len(markets), min_vol)

    # 2. Odds
    events = fetch_odds_events(api_key, bookmakers)
    if events:
        _save_events(events)

    # 3. Match, calculate edge, optionally auto-bet
    from bettor import maybe_place_bet
    from arb_engine import extract_h2h_teams as _h2h
    h2h_count = sum(1 for m in markets if _h2h(m["question"]) is not None)
    log.info("H2H-matchable PM markets: %d / %d (others are futures/props/crypto)", h2h_count, len(markets))
    matched = edges = 0

    for pm in markets:
        ev, score, yih = find_best_match(pm["question"], events, threshold)
        if ev is None:
            continue

        edge = calc_edge(
            pm["yes_price"], pm["no_price"],
            ev["home_decimal"], ev["away_decimal"], yih,
        )
        _save_match_edge(pm, ev, score, yih, edge)
        matched += 1

        if edge["best_side"] and edge["best_edge"] > 0:
            edges += 1
            token_id = (
                pm["yes_token_id"] if edge["best_side"] == "YES"
                else pm["no_token_id"]
            )
            maybe_place_bet(
                pm_id=pm["id"],
                question=pm["question"],
                event_name=f'{ev["home_team"]} vs {ev["away_team"]}',
                bookmaker=ev["bookmaker"],
                side=edge["best_side"],
                pm_price=edge["bet_price"],
                book_implied=edge["book_impl"],
                edge_pct=edge["best_edge"] * 100,
                token_id=token_id,
            )

    set_setting("last_fetch", datetime.utcnow().isoformat())
    log.info("Matched %d/%d  |  %d with positive edge", matched, len(markets), edges)

    # Diagnostic: when nothing matched, show each h2h question with its best score
    if matched == 0 and h2h_count > 0 and events:
        from arb_engine import match_score as _ms
        log.info("── Diagnostic: h2h questions vs best sportsbook match ──")
        for pm in markets:
            if _h2h(pm["question"]) is None:
                continue
            best_sc, best_ev = 0.0, None
            for ev in events:
                sc = _ms(pm["question"], ev.get("home_team", ""), ev.get("away_team", ""))
                if sc > best_sc:
                    best_sc, best_ev = sc, ev
            log.info("  %.3f  PM: %s  →  book: %s vs %s",
                     best_sc, pm["question"][:70],
                     best_ev["home_team"] if best_ev else "?",
                     best_ev["away_team"] if best_ev else "?")
        log.info("── End diagnostic (threshold=%.2f) ──", threshold)

    log.info("═══ Fetch cycle done  ═══")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    fetch_all()

    interval = int(get_setting("fetch_interval", str(DEFAULT_FETCH_MIN)))
    schedule.every(interval).minutes.do(fetch_all)
    log.info("Fetcher scheduled every %d min — Ctrl-C to stop", interval)

    while True:
        schedule.run_pending()
        time.sleep(15)
