"""
settler.py
──────────
Checks Polymarket Gamma API for resolution of unsettled PLACED bets and
updates the bets table with outcome (WIN/LOSS), pnl_usdc, and settled_at.
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone

import requests

from config import DB_PATH

log = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"


def _parse_json_field(raw):
    if isinstance(raw, (list, dict)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return []
    return raw or []


def settle_bets() -> int:
    """
    Check Polymarket for resolution of all PLACED bets that have no outcome yet.
    Updates outcome (WIN/LOSS), pnl_usdc, and settled_at for each resolved market.
    Returns the number of bets newly settled.
    """
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT id, pm_id, side, pm_price, size_usdc FROM bets "
        "WHERE status='PLACED' AND outcome IS NULL"
    ).fetchall()

    if not rows:
        conn.close()
        return 0

    settled = 0
    for bet in rows:
        try:
            resp = requests.get(
                f"{GAMMA_API}/markets",
                params={"id": bet["pm_id"]},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            market = data[0] if isinstance(data, list) else data
            if not market:
                continue

            # Market must be closed (resolved)
            closed = market.get("closed", False)
            active = market.get("active", True)
            if not closed and active:
                continue

            prices = _parse_json_field(market.get("outcomePrices"))
            if not prices or len(prices) < 2:
                continue

            try:
                yes_final = float(prices[0])
                no_final  = float(prices[1])
            except (ValueError, TypeError):
                continue

            # A resolved market has one side at 1.0 and the other at 0.0
            if yes_final >= 0.99:
                winning_side = "YES"
            elif no_final >= 0.99:
                winning_side = "NO"
            else:
                continue  # not clearly resolved yet (e.g. N/A or partial)

            side      = bet["side"]
            pm_price  = bet["pm_price"] or 0.5
            size_usdc = bet["size_usdc"] or 0.0

            if side == winning_side:
                outcome  = "WIN"
                # Bought (size_usdc / pm_price) tokens, each redeems at $1
                pnl_usdc = round(size_usdc / pm_price - size_usdc, 4)
            else:
                outcome  = "LOSS"
                pnl_usdc = round(-size_usdc, 4)

            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE bets SET outcome=?, pnl_usdc=?, settled_at=? WHERE id=?",
                (outcome, pnl_usdc, now, bet["id"]),
            )
            conn.commit()
            settled += 1
            log.info("Settled bet %d on %s: %s → %s  P&L %+.2f USDC",
                     bet["id"], bet["pm_id"], side, outcome, pnl_usdc)

        except Exception as e:
            log.warning("Error settling bet %d (market %s): %s",
                        bet["id"], bet["pm_id"], e)

    conn.close()
    return settled
