"""
fetcher.py – runs continuously, fetching Polymarket trades every minute.
Run alongside the Flask server:  python fetcher.py
"""

import sqlite3
import logging
import time
from datetime import datetime

import requests
import schedule

DB_PATH = "polymarket.db"
GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "polymarket-tracker/1.0"}


# ─── DB helpers ───────────────────────────────────────────────────────────────

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


# ─── API helpers ──────────────────────────────────────────────────────────────

def _get(url, params=None):
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        log.warning("GET %s failed: %s", url, exc)
        return None


def fetch_profile(address: str) -> dict | None:
    """Fetch trader profile from Gamma API."""
    data = _get(f"{GAMMA_API}/profiles", params={"address": address})
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict):
        return data
    return None


def fetch_activity(address: str, limit: int = 500) -> list:
    """Fetch trade activity from Data API."""
    data = _get(f"{DATA_API}/activity", params={"user": address, "limit": limit})
    if isinstance(data, list):
        return data
    return []


# ─── Profile field normalisation ──────────────────────────────────────────────

def _str(profile: dict, *keys) -> str:
    for k in keys:
        v = profile.get(k)
        if v:
            return str(v)
    return ""


def extract_profile(profile: dict) -> dict:
    return {
        "username": _str(profile, "name", "username", "displayName"),
        "bio": _str(profile, "bio", "description"),
        "profile_image": _str(profile, "profileImage", "profileImageUrl", "avatar", "image"),
        "website": _str(profile, "website", "websiteUrl"),
        "twitter": _str(profile, "twitterHandle", "twitter", "twitterUsername"),
    }


# ─── Trade field normalisation ────────────────────────────────────────────────

def _float(d: dict, *keys) -> float:
    for k in keys:
        v = d.get(k)
        if v is not None:
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
    return 0.0


def make_trade_id(trade: dict) -> str:
    tx = trade.get("transactionHash", "")
    ts = trade.get("timestamp", "")
    tid = trade.get("id", "")
    if tid:
        return str(tid)
    return f"{tx}_{ts}"


def extract_trade(trade: dict, address: str) -> dict | None:
    trade_type = trade.get("type", "TRADE")
    if trade_type not in ("TRADE", "trade", ""):
        return None  # skip non-trade events (splits, merges, etc.)

    side = trade.get("side", trade.get("type", "BUY")).upper()
    if side not in ("BUY", "SELL"):
        side = "BUY"

    return {
        "id": make_trade_id(trade),
        "trader_address": address,
        "market_title": _str(trade, "title", "marketTitle", "market"),
        "market_icon": _str(trade, "icon", "marketImage", "image"),
        "outcome": _str(trade, "outcome", "outcomeName"),
        "side": side,
        "size": _float(trade, "size", "shares"),
        "price": _float(trade, "price"),
        "usdc_size": _float(trade, "usdcSize", "amount", "value"),
        "condition_id": _str(trade, "conditionId", "condition_id"),
        "transaction_hash": _str(trade, "transactionHash", "txHash"),
        "timestamp": int(trade.get("timestamp", 0) or 0),
    }


# ─── Core update logic ────────────────────────────────────────────────────────

def update_trader(address: str):
    log.info("Updating trader %s …", address)

    profile_raw = fetch_profile(address)
    activity = fetch_activity(address)

    conn = get_conn()
    try:
        # Update profile fields if we got data
        if profile_raw:
            p = extract_profile(profile_raw)
            conn.execute(
                """UPDATE traders SET
                       username      = ?,
                       bio           = ?,
                       profile_image = ?,
                       website       = ?,
                       twitter       = ?,
                       last_updated  = ?
                   WHERE address = ?""",
                (
                    p["username"],
                    p["bio"],
                    p["profile_image"],
                    p["website"],
                    p["twitter"],
                    datetime.utcnow().isoformat(),
                    address,
                ),
            )

        # Insert new trades
        new_count = 0
        for raw in activity:
            t = extract_trade(raw, address)
            if t is None or not t["id"]:
                continue
            existing = conn.execute(
                "SELECT 1 FROM trades WHERE id = ?", (t["id"],)
            ).fetchone()
            if existing:
                continue
            conn.execute(
                """INSERT OR IGNORE INTO trades
                   (id, trader_address, market_title, market_icon, outcome,
                    side, size, price, usdc_size, condition_id, transaction_hash, timestamp)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    t["id"], t["trader_address"], t["market_title"],
                    t["market_icon"], t["outcome"], t["side"],
                    t["size"], t["price"], t["usdc_size"],
                    t["condition_id"], t["transaction_hash"], t["timestamp"],
                ),
            )
            new_count += 1

        conn.commit()
        log.info("  → %d new trades stored for %s", new_count, address)
    finally:
        conn.close()


def fetch_all():
    conn = get_conn()
    traders = conn.execute("SELECT address FROM traders").fetchall()
    conn.close()

    if not traders:
        log.info("No traders tracked yet.")
        return

    for row in traders:
        try:
            update_trader(row["address"])
        except Exception as exc:
            log.error("Error updating %s: %s", row["address"], exc)


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("Polymarket fetcher starting …")
    fetch_all()  # immediate first run

    schedule.every(1).minutes.do(fetch_all)

    while True:
        schedule.run_pending()
        time.sleep(10)
