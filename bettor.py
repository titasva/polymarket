"""
bettor.py
─────────
Places bets on Polymarket via the CLOB API when an edge opportunity is found.

Requires:
  - pip install py-clob-client
  - A Polygon wallet private key stored in settings (pm_private_key)
  - That wallet must have USDC on Polygon and have traded on Polymarket at
    least once (to register proxy wallet & approve CLOB contract)
"""

import logging
import sqlite3
from datetime import datetime, timezone

from config import DB_PATH

log = logging.getLogger(__name__)

CLOB_HOST = "https://clob.polymarket.com"
POLYGON_CHAIN_ID = 137


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _conn():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _get_setting(key: str, default: str = "") -> str:
    c = _conn()
    row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    c.close()
    return row["value"] if row else default


def _record_bet(
    pm_id: str, question: str, event_name: str, bookmaker: str,
    side: str, pm_price: float, book_implied: float, edge_pct: float,
    size_usdc: float, token_id: str, order_id: str,
    status: str, error: str | None,
) -> None:
    c = _conn()
    c.execute(
        """INSERT INTO bets
           (pm_id, question, event_name, bookmaker, side, pm_price,
            book_implied, edge_pct, size_usdc, token_id, order_id,
            status, error, placed_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (pm_id, question, event_name, bookmaker,
         side, pm_price, book_implied, edge_pct,
         size_usdc, token_id or "", order_id or "",
         status, error or "",
         datetime.now(timezone.utc).isoformat()),
    )
    c.commit()
    c.close()


# ── CLOB client ────────────────────────────────────────────────────────────────

def _get_client(private_key: str):
    try:
        from py_clob_client.client import ClobClient
    except ImportError:
        raise ImportError(
            "py-clob-client not installed. Run: pip install py-clob-client"
        )
    client = ClobClient(CLOB_HOST, key=private_key, chain_id=POLYGON_CHAIN_ID)
    client.set_api_creds(client.create_or_derive_api_creds())
    return client


def _check_balance(client, size_usdc: float) -> bool:
    """
    Returns True if the wallet has enough USDC allowance/balance for the bet.
    Logs the actual balance so the user knows what's available.
    """
    try:
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
        resp = client.get_balance_allowance(
            params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        # Response keys: "balance", "allowance" (both as decimal strings)
        balance = float(resp.get("balance", 0))
        allowance = float(resp.get("allowance", 0))
        log.info("  Wallet USDC — balance: $%.2f | allowance: $%.2f", balance, allowance)
        usable = min(balance, allowance)
        if usable < size_usdc:
            log.error(
                "  Insufficient funds: need $%.2f but usable USDC is $%.2f "
                "(balance=$%.2f, allowance=$%.2f). "
                "Deposit USDC on Polymarket (polymarket.com → Profile → Deposit) "
                "or approve the exchange contract.",
                size_usdc, usable, balance, allowance,
            )
            return False
        return True
    except Exception as exc:
        log.warning("  Could not check balance (will try order anyway): %s", exc)
        return True  # don't block the order if the check itself fails


# ── Core bet placement ─────────────────────────────────────────────────────────

def already_bet(pm_id: str) -> bool:
    """Return True if we ever placed a successful bet on this market."""
    c = _conn()
    row = c.execute(
        "SELECT id FROM bets WHERE pm_id = ? AND status = 'PLACED'",
        (pm_id,),
    ).fetchone()
    c.close()
    return row is not None


def bets_today() -> int:
    c = _conn()
    n = c.execute(
        "SELECT COUNT(*) n FROM bets WHERE status='PLACED' AND date(placed_at)=date('now')"
    ).fetchone()["n"]
    c.close()
    return n


def maybe_place_bet(
    pm_id: str,
    question: str,
    event_name: str,
    bookmaker: str,
    side: str,           # "YES" or "NO"
    pm_price: float,
    book_implied: float,
    edge_pct: float,
    token_id: str,
) -> None:
    """
    Called by the fetcher after calculating edge.
    Checks all gates, then places a limit order on Polymarket.
    """
    # Gate 1: auto-bet enabled?
    if _get_setting("auto_bet_enabled", "false").lower() != "true":
        return

    # Gate 2: edge above threshold?
    min_edge = float(_get_setting("min_edge_pct", "2.5"))
    if edge_pct < min_edge:
        return

    # Gate 3: private key configured?
    private_key = _get_setting("pm_private_key", "")
    if not private_key:
        log.warning("Auto-bet triggered but no private key set (market: %s)", pm_id)
        return

    # Gate 4: token ID available?
    if not token_id:
        log.warning("No token_id for %s side on %s — cannot bet (check clobTokenIds in API response)", side, pm_id)
        return
    log.debug("Token ID for %s %s: %s", side, pm_id[:12], token_id[:16] + "…")

    # Gate 5: already bet on this market (ever)?
    if already_bet(pm_id):
        log.debug("Already placed a bet on %s — skipping", pm_id)
        return

    # Gate 6: daily cap
    max_daily = int(_get_setting("max_bets_day", "10"))
    if bets_today() >= max_daily:
        log.warning("Daily bet cap (%d) reached — skipping", max_daily)
        return

    size_usdc = float(_get_setting("bet_size", "10"))

    log.info(
        "Auto-bet: %s %s | price=%.3f | edge=%.1f%% | $%.2f | %s",
        side, pm_id[:12], pm_price, edge_pct, size_usdc, event_name,
    )

    try:
        from py_clob_client.clob_types import OrderArgs
        from py_clob_client.order_builder.constants import BUY

        client = _get_client(private_key)

        if not _check_balance(client, size_usdc):
            _record_bet(pm_id, question, event_name, bookmaker,
                        side, pm_price, book_implied, edge_pct,
                        size_usdc, token_id, "", "FAILED",
                        "Insufficient USDC balance/allowance")
            return

        # Place a limit order slightly above market price for better fill odds
        limit_price = round(min(pm_price + 0.02, 0.97), 4)

        order_args = OrderArgs(
            token_id=token_id,
            price=limit_price,
            size=size_usdc,
            side=BUY,
        )
        resp = client.create_and_post_order(order_args)
        order_id = (resp or {}).get("orderID", "")
        if order_id:
            log.info("  Order placed: %s", order_id)
            _record_bet(pm_id, question, event_name, bookmaker,
                        side, pm_price, book_implied, edge_pct,
                        size_usdc, token_id, order_id, "PLACED", None)
        else:
            _record_bet(pm_id, question, event_name, bookmaker,
                        side, pm_price, book_implied, edge_pct,
                        size_usdc, token_id, "", "FAILED",
                        f"No orderID in response: {resp}")

    except Exception as exc:
        err_str = str(exc)
        if "balance" in err_str.lower() or "allowance" in err_str.lower():
            log.error(
                "Bet failed for %s — insufficient USDC balance or allowance. "
                "Fund your wallet with USDC on Polygon and ensure the CLOB "
                "exchange is approved. Error: %s",
                pm_id, exc,
            )
        else:
            log.error("Bet failed for %s: %s", pm_id, exc)
        _record_bet(pm_id, question, event_name, bookmaker,
                    side, pm_price, book_implied, edge_pct,
                    size_usdc, token_id, "", "FAILED", err_str)
