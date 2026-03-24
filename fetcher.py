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
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
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

def _get(url, params=None, _retries=4):
    delay = 2
    for attempt in range(_retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=15)
            r.raise_for_status()
            return r, r.json()
        except requests.exceptions.HTTPError as exc:
            log.warning("GET %s  →  %s", url, exc)
            return exc.response, None   # HTTP error — don't retry
        except requests.exceptions.RequestException as exc:
            if attempt < _retries - 1:
                log.warning("GET %s  →  %s (retry %d/%d in %ds)",
                            url, exc, attempt + 1, _retries - 1, delay)
                time.sleep(delay)
                delay *= 2
            else:
                log.warning("GET %s  →  %s (all %d retries exhausted)", url, exc, _retries - 1)
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

_OUTRIGHT_RE = re.compile(r"\bwinner|champion|outright|season\b", re.IGNORECASE)
_SPORTS_CACHE_TTL_HOURS = 8  # re-fetch active sports at most 3x per day


def fetch_active_sports(api_key: str) -> list[str]:
    """
    Query /v4/sports to get all sports currently in season that have h2h markets.
    Costs 1 API request. Result is cached in the DB for _SPORTS_CACHE_TTL_HOURS hours.
    Falls back to cached list (or TRACKED_SPORTS) if the call fails.
    """
    # Return cached list if fresh enough
    cached_sports = get_setting("cached_sports", "")
    cached_at_str = get_setting("cached_sports_at", "")
    if cached_sports and cached_at_str:
        try:
            cached_at = datetime.fromisoformat(cached_at_str)
            age_hours = (datetime.now(timezone.utc) - cached_at).total_seconds() / 3600
            if age_hours < _SPORTS_CACHE_TTL_HOURS:
                keys = json.loads(cached_sports)
                log.info("Active sports (cached, %.1fh old): %d", age_hours, len(keys))
                return keys
        except Exception:
            pass

    _, data = _get(f"{ODDS_API_BASE}/sports", params={"apiKey": api_key, "all": "false"})
    if not data:
        log.warning("Could not fetch sports list — using cached/config defaults")
        if cached_sports:
            return json.loads(cached_sports)
        return list(TRACKED_SPORTS)

    keys = [
        s["key"] for s in data
        if s.get("active")
        and not _OUTRIGHT_RE.search(s.get("title", ""))
    ]
    log.info("Active sports from Odds API: %d", len(keys))

    set_setting("cached_sports", json.dumps(keys))
    set_setting("cached_sports_at", datetime.now(timezone.utc).isoformat())

    return keys


def fetch_odds_events(api_key: str, bookmakers: str = "pinnacle") -> list[dict]:
    if not api_key:
        log.warning("No Odds API key — skipping odds fetch")
        return []

    # Priority: user-configured list > full active scan (API-cached for 8h)
    sports_raw = get_setting("tracked_sports", "").strip()
    if sports_raw:
        sport_list = [s.strip() for s in sports_raw.split(",") if s.strip()]
        log.info("Using configured sports list (%d sports)", len(sport_list))
    else:
        sport_list = fetch_active_sports(api_key)
        log.info("Full sports scan (%d sports)", len(sport_list))

    _BASELINE = "h2h,spreads,totals"

    def _extended_markets(sport: str) -> str:
        """Best-effort extended market string for a sport category."""
        if sport.startswith("soccer_"):
            return "h2h,spreads,totals,alternate_spreads,alternate_totals,btts"
        if any(sport.startswith(p) for p in (
            "basketball_nba", "americanfootball_nfl",
            "icehockey_nhl", "baseball_mlb",
        )):
            return "h2h,spreads,totals,alternate_spreads,alternate_totals"
        return _BASELINE

    # Per-sport market-support cache: maps sport key → "baseline" | "extended"
    # Lets us skip the double-request for sports that 422'd in a previous cycle.
    # Cached for 24 h so any API changes are picked up daily.
    _SPORT_MKT_TTL = 24
    try:
        sport_mkt_cache: dict = json.loads(get_setting("sport_mkt_cache", "{}") or "{}")
        sport_mkt_cache_at = datetime.fromisoformat(
            get_setting("sport_mkt_cache_at", datetime.now(timezone.utc).isoformat())
        )
        if (datetime.now(timezone.utc) - sport_mkt_cache_at).total_seconds() / 3600 > _SPORT_MKT_TTL:
            sport_mkt_cache = {}
    except Exception:
        sport_mkt_cache = {}

    out, remaining = [], None
    productive = []
    sandbox_mkts: list[dict] = []

    for sport in sport_list:
        extended = _extended_markets(sport)
        needs_baseline = sport_mkt_cache.get(sport) == "baseline"
        markets = _BASELINE if (needs_baseline or extended == _BASELINE) else extended

        r, data = _get(
            f"{ODDS_API_BASE}/sports/{sport}/odds",
            params={
                "apiKey":     api_key,
                "regions":    "us,eu",
                "markets":    markets,
                "bookmakers": bookmakers,
                "oddsFormat": "decimal",
            },
        )

        # 422 means these extended markets aren't supported → fall back & remember
        if data is None and r is not None and getattr(r, "status_code", 0) == 422:
            log.info("%s: extended markets unsupported — retrying with baseline", sport)
            sport_mkt_cache[sport] = "baseline"
            r, data = _get(
                f"{ODDS_API_BASE}/sports/{sport}/odds",
                params={
                    "apiKey":     api_key,
                    "regions":    "us,eu",
                    "markets":    _BASELINE,
                    "bookmakers": bookmakers,
                    "oddsFormat": "decimal",
                },
            )

        if data is None:
            continue
        if r is not None:
            remaining = r.headers.get("x-requests-remaining", remaining)

        parsed = []
        for ev in data:
            parsed.extend(_parse_odds_event(ev, sport))
            sandbox_mkts.extend(_parse_sandbox_from_event(ev, sport))
        if parsed:
            productive.append(sport)
            out.extend(parsed)
            log.info("  → %s: %d events", sport, len(data))

    # Persist per-sport market support cache
    set_setting("sport_mkt_cache", json.dumps(sport_mkt_cache))
    set_setting("sport_mkt_cache_at", datetime.now(timezone.utc).isoformat())

    if remaining is not None:
        set_setting("odds_api_remaining", str(remaining))
        log.info("Odds API requests remaining: %s", remaining)

    if sandbox_mkts:
        _save_sandbox_odds(sandbox_mkts)
        log.info("Sandbox odds markets stored: %d", len(sandbox_mkts))

    log.info("Total odds events: %d (from %d/%d sports)",
             len(out), len(productive), len(sport_list))
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


# ── Sandbox market parsing ──────────────────────────────────────────────────────

def _parse_sandbox_from_event(ev: dict, sport: str) -> list[dict]:
    """
    Extract spreads, totals, and draw odds from a raw Odds API event.
    Returns a list of sandbox_odds_markets rows.
    """
    commence = ev.get("commence_time", "")
    if commence:
        try:
            ct = datetime.fromisoformat(commence.replace("Z", "+00:00"))
            if ct <= datetime.now(timezone.utc):
                return []
        except ValueError:
            pass

    raw_id   = ev.get("id", "")
    home     = ev.get("home_team", "")
    away     = ev.get("away_team", "")
    comp     = ev.get("sport_title", sport)
    now      = datetime.utcnow().isoformat()
    results  = []

    for bk in ev.get("bookmakers", []):
        bk_key  = bk.get("key", "")
        bk_name = bk.get("title", bk_key)

        for mkt in bk.get("markets", []):
            mkt_key  = mkt.get("key", "")
            outcomes = mkt.get("outcomes", [])

            # ── Spreads ─────────────────────────────────────────────────────
            if mkt_key == "spreads" and len(outcomes) >= 2:
                for ou in outcomes:
                    name  = ou.get("name", "")
                    point = float(ou.get("point") or 0)
                    price = float(ou.get("price") or 0)
                    if not price:
                        continue
                    # Pair each side with its counterpart
                    other = next(
                        (x for x in outcomes if x.get("name") != name), None
                    )
                    if not other or not float(other.get("price") or 0):
                        continue
                    # Only store once per line (keyed by name_a < name_b)
                    if name < other.get("name", ""):
                        uid = f"{raw_id}_{bk_key}_spreads_{name}_{point}"
                        results.append({
                            "id":             uid,
                            "raw_event_id":   raw_id,
                            "sport":          sport,
                            "competition":    comp,
                            "home_team":      home,
                            "away_team":      away,
                            "commence_time":  commence,
                            "bookmaker":      bk_name,
                            "bookmaker_key":  bk_key,
                            "market_type":    "spreads",
                            "point":          point,
                            "outcome_a_name": name,
                            "outcome_b_name": other.get("name"),
                            "outcome_a_dec":  price,
                            "outcome_b_dec":  float(other.get("price") or 0),
                            "last_updated":   now,
                        })

            # ── Totals (O/U) ────────────────────────────────────────────────
            elif mkt_key == "totals" and len(outcomes) >= 2:
                over  = next((o for o in outcomes if o.get("name", "").lower() == "over"),  None)
                under = next((o for o in outcomes if o.get("name", "").lower() == "under"), None)
                if over and under:
                    o_dec = float(over.get("price")  or 0)
                    u_dec = float(under.get("price") or 0)
                    point = float(over.get("point")  or under.get("point") or 0)
                    if o_dec and u_dec:
                        uid = f"{raw_id}_{bk_key}_totals_{point}"
                        results.append({
                            "id":             uid,
                            "raw_event_id":   raw_id,
                            "sport":          sport,
                            "competition":    comp,
                            "home_team":      home,
                            "away_team":      away,
                            "commence_time":  commence,
                            "bookmaker":      bk_name,
                            "bookmaker_key":  bk_key,
                            "market_type":    "totals",
                            "point":          point,
                            "outcome_a_name": "Over",
                            "outcome_b_name": "Under",
                            "outcome_a_dec":  o_dec,
                            "outcome_b_dec":  u_dec,
                            "last_updated":   now,
                        })

            # ── Spreads (alternate lines) ────────────────────────────────────
            elif mkt_key == "alternate_spreads" and len(outcomes) >= 2:
                for ou in outcomes:
                    name  = ou.get("name", "")
                    point = float(ou.get("point") or 0)
                    price = float(ou.get("price") or 0)
                    if not price:
                        continue
                    other = next((x for x in outcomes if x.get("name") != name), None)
                    if not other or not float(other.get("price") or 0):
                        continue
                    if name < other.get("name", ""):
                        uid = f"{raw_id}_{bk_key}_alt_spreads_{name}_{point}"
                        results.append({
                            "id":             uid,
                            "raw_event_id":   raw_id,
                            "sport":          sport,
                            "competition":    comp,
                            "home_team":      home,
                            "away_team":      away,
                            "commence_time":  commence,
                            "bookmaker":      bk_name,
                            "bookmaker_key":  bk_key,
                            "market_type":    "spreads",
                            "point":          point,
                            "outcome_a_name": name,
                            "outcome_b_name": other.get("name"),
                            "outcome_a_dec":  price,
                            "outcome_b_dec":  float(other.get("price") or 0),
                            "last_updated":   now,
                        })

            # ── Alternate Totals (many O/U lines at different points) ─────────
            elif mkt_key == "alternate_totals":
                pt_map: dict[float, dict] = {}
                for ou in outcomes:
                    pt    = float(ou.get("point") or 0)
                    name  = ou.get("name", "").lower()
                    price = float(ou.get("price") or 0)
                    if price:
                        pt_map.setdefault(pt, {})[name] = price
                for pt, sides in pt_map.items():
                    o_dec = sides.get("over")
                    u_dec = sides.get("under")
                    if o_dec and u_dec:
                        uid = f"{raw_id}_{bk_key}_alt_totals_{pt}"
                        results.append({
                            "id":             uid,
                            "raw_event_id":   raw_id,
                            "sport":          sport,
                            "competition":    comp,
                            "home_team":      home,
                            "away_team":      away,
                            "commence_time":  commence,
                            "bookmaker":      bk_name,
                            "bookmaker_key":  bk_key,
                            "market_type":    "totals",
                            "point":          pt,
                            "outcome_a_name": "Over",
                            "outcome_b_name": "Under",
                            "outcome_a_dec":  o_dec,
                            "outcome_b_dec":  u_dec,
                            "last_updated":   now,
                        })

            # ── BTTS (Both Teams To Score) ───────────────────────────────────
            elif mkt_key == "btts" and len(outcomes) >= 2:
                yes_out = next((o for o in outcomes if o.get("name", "").lower() == "yes"), None)
                no_out  = next((o for o in outcomes if o.get("name", "").lower() == "no"),  None)
                if yes_out and no_out:
                    y_dec = float(yes_out.get("price") or 0)
                    n_dec = float(no_out.get("price")  or 0)
                    if y_dec and n_dec:
                        uid = f"{raw_id}_{bk_key}_btts"
                        results.append({
                            "id":             uid,
                            "raw_event_id":   raw_id,
                            "sport":          sport,
                            "competition":    comp,
                            "home_team":      home,
                            "away_team":      away,
                            "commence_time":  commence,
                            "bookmaker":      bk_name,
                            "bookmaker_key":  bk_key,
                            "market_type":    "btts",
                            "point":          None,
                            "outcome_a_name": "Yes",
                            "outcome_b_name": "No",
                            "outcome_a_dec":  y_dec,
                            "outcome_b_dec":  n_dec,
                            "last_updated":   now,
                        })

            # ── Draw (from 3-way h2h) ────────────────────────────────────────
            elif mkt_key == "h2h":
                draw_out = next(
                    (o for o in outcomes if o.get("name", "").lower() in ("draw", "tie")),
                    None,
                )
                if draw_out:
                    d_dec = float(draw_out.get("price") or 0)
                    # Proxy "No Draw": combine home+away implied / total
                    h_out = next((o for o in outcomes if o.get("name") == home), None)
                    a_out = next((o for o in outcomes if o.get("name") == away), None)
                    if h_out and a_out and d_dec:
                        # "No Draw" decimal ≈ 1 / (home_impl + away_impl)
                        h_dec = float(h_out.get("price") or 0)
                        a_dec = float(a_out.get("price") or 0)
                        if h_dec and a_dec:
                            no_draw_impl = (1/h_dec) + (1/a_dec)
                            nd_dec = round(1.0 / no_draw_impl, 4) if no_draw_impl else 0
                            uid = f"{raw_id}_{bk_key}_h2h_draw"
                            results.append({
                                "id":             uid,
                                "raw_event_id":   raw_id,
                                "sport":          sport,
                                "competition":    comp,
                                "home_team":      home,
                                "away_team":      away,
                                "commence_time":  commence,
                                "bookmaker":      bk_name,
                                "bookmaker_key":  bk_key,
                                "market_type":    "h2h_draw",
                                "point":          None,
                                "outcome_a_name": "Draw",
                                "outcome_b_name": "No Draw",
                                "outcome_a_dec":  d_dec,
                                "outcome_b_dec":  nd_dec,
                                "last_updated":   now,
                            })

    return results


# ── Sandbox DB write ────────────────────────────────────────────────────────────

def _save_sandbox_odds(markets: list[dict]) -> None:
    c = _conn()
    for m in markets:
        c.execute(
            """INSERT OR REPLACE INTO sandbox_odds_markets
               (id, raw_event_id, sport, competition, home_team, away_team,
                commence_time, bookmaker, bookmaker_key, market_type, point,
                outcome_a_name, outcome_b_name, outcome_a_dec, outcome_b_dec,
                last_updated)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (m["id"], m["raw_event_id"], m["sport"], m["competition"],
             m["home_team"], m["away_team"], m["commence_time"],
             m["bookmaker"], m["bookmaker_key"], m["market_type"], m.get("point"),
             m["outcome_a_name"], m["outcome_b_name"],
             m["outcome_a_dec"], m["outcome_b_dec"], m["last_updated"]),
        )
    c.commit()
    c.close()


# ── Sandbox matching ────────────────────────────────────────────────────────────

def run_sandbox_matching(markets: list[dict]) -> None:
    """
    For each PM market, detect its type and try to match it to a sandbox
    odds market (spread/totals/draw/btts). Results go to sandbox_matches.
    """
    from sandbox_engine import (
        detect_market_type, find_sandbox_event,
        find_sandbox_market, find_sandbox_market_relaxed,
        is_favorable_totals_line, calc_sandbox_edge,
    )
    from bettor import maybe_place_bet

    c = _conn()
    # Load sandbox odds and base events
    sandbox_rows = c.execute(
        "SELECT * FROM sandbox_odds_markets"
    ).fetchall()
    sandbox_mkts = [dict(r) for r in sandbox_rows]

    # Deduplicated base event list (one row per raw event id)
    seen = set()
    base_events = []
    for r in sandbox_rows:
        rid = r["raw_event_id"]
        if rid not in seen:
            seen.add(rid)
            base_events.append({
                "raw_event_id": rid,
                "home_team":    r["home_team"],
                "away_team":    r["away_team"],
                "sport":        r["sport"],
            })
    c.close()

    if not base_events:
        log.info("Sandbox matching: no sandbox odds in DB yet")
        return

    saved = 0
    now = datetime.utcnow().isoformat()

    c = _conn()
    for pm in markets:
        pm_type = detect_market_type(pm["question"])
        if pm_type in ("h2h", "unknown"):
            continue   # h2h is handled by the main pipeline

        ev, score = find_sandbox_event(pm["question"], base_events)
        if ev is None:
            continue

        # ── Analysis match (strict: exact line only) → sandbox display ──────
        odds_mkt = find_sandbox_market(
            pm_type, pm["question"], sandbox_mkts, ev["raw_event_id"]
        )
        if odds_mkt is not None:
            edge = calc_sandbox_edge(
                pm["yes_price"], pm["no_price"],
                pm_type, pm["question"], odds_mkt,
            )
            c.execute(
                """INSERT OR REPLACE INTO sandbox_matches
                   (pm_id, odds_market_id, pm_type, match_score,
                    yes_maps_to, yes_edge, no_edge, best_edge, best_side, detected_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (pm["id"], odds_mkt["id"], pm_type, round(score, 4),
                 edge.get("yes_maps_to"),
                 edge["yes_edge"], edge["no_edge"],
                 edge["best_edge"], edge.get("best_side"), now),
            )
            saved += 1

        # ── Betting path (totals only): also allows favorable line mismatches ─
        # Rule: exact match always OK; also OK if:
        #   PM line > book line and bet direction is UNDER (extra cushion)
        #   PM line < book line and bet direction is OVER  (extra cushion)
        if pm_type in ("totals", "totals_sets", "totals_games"):
            bet_mkt, pm_line = find_sandbox_market_relaxed(
                pm_type, pm["question"], sandbox_mkts, ev["raw_event_id"]
            )
            if bet_mkt is not None and pm_line is not None:
                bet_edge = calc_sandbox_edge(
                    pm["yes_price"], pm["no_price"],
                    pm_type, pm["question"], bet_mkt,
                )
                if bet_edge["best_side"] and bet_edge["best_edge"] > 0:
                    # Resolve actual over/under direction we'd be betting
                    if bet_edge["best_side"] == "YES":
                        bet_dir = bet_edge["yes_maps_to"]  # "over" or "under"
                    else:
                        bet_dir = "under" if bet_edge["yes_maps_to"] == "over" else "over"

                    book_line = bet_mkt.get("point") or 0.0
                    if is_favorable_totals_line(pm_line, book_line, bet_dir):
                        pm_price = (pm["yes_price"] if bet_edge["best_side"] == "YES"
                                    else pm["no_price"])
                        # Recover bookmaker implied prob: edge = book_impl - pm_price
                        book_impl = (bet_edge["yes_edge"] + pm["yes_price"]
                                     if bet_edge["best_side"] == "YES"
                                     else bet_edge["no_edge"] + pm["no_price"])
                        token_id = (pm.get("yes_token_id") if bet_edge["best_side"] == "YES"
                                    else pm.get("no_token_id"))
                        maybe_place_bet(
                            pm_id=pm["id"],
                            question=pm["question"],
                            event_name=f'{ev["home_team"]} vs {ev["away_team"]}',
                            bookmaker=bet_mkt.get("bookmaker", ""),
                            side=bet_edge["best_side"],
                            pm_price=pm_price,
                            book_implied=book_impl,
                            edge_pct=bet_edge["best_edge"] * 100,
                            token_id=token_id,
                        )

    c.commit()
    c.close()
    log.info("Sandbox matching: %d PM markets matched to non-h2h odds", saved)


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

    api_key        = get_setting("odds_api_key")
    bookmakers     = get_setting("bookmakers", DEFAULT_BOOKMAKERS)
    threshold      = float(get_setting("match_threshold", str(MATCH_THRESHOLD)))
    bet_min_conf   = float(get_setting("bet_min_confidence", "0.75"))
    min_vol        = float(get_setting("min_volume", str(MIN_VOLUME)))

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
            if score < bet_min_conf:
                log.info("  Skip bet (conf %.0f%% < %.0f%% required): %s",
                         score * 100, bet_min_conf * 100, pm["question"])
                continue
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

    # 4. Sandbox matching (spread / totals / draw / btts)
    run_sandbox_matching(markets)

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
