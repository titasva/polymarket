"""
settler.py
──────────
1. Checks Polymarket Gamma API for resolution of PLACED bets (WIN/LOSS).
2. For each WIN, calls CTF.redeemPositions() on Polygon to convert the
   winning conditional tokens back to USDC.e automatically.
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone

import requests

from config import DB_PATH

log = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"

# ── Polygon / contract constants ───────────────────────────────────────────────

_POLYGON_RPCS = [
    "https://polygon.llamarpc.com",
    "https://rpc.ankr.com/polygon",
    "https://polygon-mainnet.public.blastapi.io",
    "https://1rpc.io/matic",
    "https://polygon.drpc.org",
]
_USDC_E_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
_CTF_ADDRESS    = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

_CTF_ABI = [
    {
        "name": "redeemPositions",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "collateralToken",    "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId",        "type": "bytes32"},
            {"name": "indexSets",          "type": "uint256[]"},
        ],
        "outputs": [],
    }
]


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _get_setting(key: str, default: str = "") -> str:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def _parse_json_field(raw):
    if isinstance(raw, (list, dict)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return []
    return raw or []


# ── Polygon redemption ─────────────────────────────────────────────────────────

def _connect_polygon():
    """Return a connected Web3 instance for Polygon, or None."""
    try:
        from web3 import Web3
        from web3.middleware import ExtraDataToPOAMiddleware
    except ImportError:
        log.warning("web3 not installed — cannot auto-redeem (run: pip install web3)")
        return None

    for rpc in _POLYGON_RPCS:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
            _ = w3.eth.block_number      # test connection
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            log.debug("Polygon RPC: %s", rpc)
            return w3
        except Exception:
            continue

    log.error("All Polygon RPCs failed — cannot redeem")
    return None


def _redeem_win(condition_id: str, side: str, private_key: str) -> str | None:
    """
    Call CTF.redeemPositions to turn winning conditional tokens into USDC.e.

    condition_id  — bytes32 hex from the Polymarket Gamma API (conditionId field)
    side          — "YES" or "NO" (the side that won, i.e. what we bet on)
    private_key   — Polygon wallet private key

    Returns the 0x-prefixed tx hash on success, None on failure.
    """
    from web3 import Web3
    from eth_account import Account

    w3 = _connect_polygon()
    if w3 is None:
        return None

    try:
        acct = Account.from_key(private_key)
        addr = acct.address

        # index_set bitmask: YES = outcome 0 → 2^0 = 1 | NO = outcome 1 → 2^1 = 2
        index_set = 1 if side == "YES" else 2

        # Normalise conditionId to exactly 32 bytes
        cid_hex = condition_id.removeprefix("0x").zfill(64)
        if len(cid_hex) != 64:
            log.error("conditionId has unexpected length: %s", condition_id)
            return None
        cid_bytes  = bytes.fromhex(cid_hex)
        parent_id  = b"\x00" * 32       # parentCollectionId = bytes32(0)

        ctf = w3.eth.contract(
            address=Web3.to_checksum_address(_CTF_ADDRESS),
            abi=_CTF_ABI,
        )

        fn = ctf.functions.redeemPositions(
            Web3.to_checksum_address(_USDC_E_ADDRESS),
            parent_id,
            cid_bytes,
            [index_set],
        )

        nonce = w3.eth.get_transaction_count(addr, "latest")
        tx    = fn.build_transaction({
            "from":                 addr,
            "nonce":                nonce,
            "gas":                  180_000,
            "maxFeePerGas":         w3.to_wei(200, "gwei"),
            "maxPriorityFeePerGas": w3.to_wei(50,  "gwei"),
            "chainId":              137,
            "type":                 2,
        })
        signed  = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        log.info("  Redemption tx submitted: 0x%s", tx_hash.hex())

        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120, poll_latency=10)
        if receipt.status == 1:
            log.info("  Redemption confirmed in block %d", receipt.blockNumber)
            return "0x" + tx_hash.hex()
        else:
            log.error("  Redemption tx REVERTED (block %d)", receipt.blockNumber)
            return None

    except Exception as e:
        log.error("  Redemption error: %s", e)
        return None


# ── Main settlement + redemption entry point ───────────────────────────────────

def settle_bets() -> dict:
    """
    1. Detect newly resolved markets → record WIN/LOSS + P&L.
    2. Redeem any WIN bets that haven't been redeemed yet (including prior wins).
    Returns {"settled": N, "redeemed": M}.
    """
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    # ── Phase 1: settle unresolved bets ───────────────────────────────────────

    unsettled = conn.execute(
        "SELECT id, pm_id, side, pm_price, size_usdc FROM bets "
        "WHERE status='PLACED' AND outcome IS NULL"
    ).fetchall()

    settled_count = 0
    # track conditionIds for bets we just marked WIN so we can redeem them below
    newly_won: list[dict] = []

    for bet in unsettled:
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

            if yes_final >= 0.99:
                winning_side = "YES"
            elif no_final >= 0.99:
                winning_side = "NO"
            else:
                continue  # not clearly resolved yet

            side      = bet["side"]
            pm_price  = bet["pm_price"] or 0.5
            size_usdc = bet["size_usdc"] or 0.0

            if side == winning_side:
                outcome  = "WIN"
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
            settled_count += 1
            log.info("Settled bet %d: %s → %s  P&L %+.2f USDC",
                     bet["id"], side, outcome, pnl_usdc)

            if outcome == "WIN":
                condition_id = market.get("conditionId", "")
                neg_risk     = market.get("negRisk", False)
                newly_won.append({
                    "id":           bet["id"],
                    "side":         side,
                    "condition_id": condition_id,
                    "neg_risk":     neg_risk,
                })

        except Exception as e:
            log.warning("Error settling bet %d (market %s): %s",
                        bet["id"], bet["pm_id"], e)

    # ── Phase 2: redeem all unredeemed WIN bets ────────────────────────────────
    # Includes bets won in this run AND wins from previous runs that weren't redeemed

    private_key = _get_setting("pm_private_key", "")
    redeemed_count = 0

    if not private_key:
        if newly_won:
            log.warning("Won %d bet(s) but no private key configured — cannot auto-redeem",
                        len(newly_won))
        conn.close()
        return {"settled": settled_count, "redeemed": 0}

    # Collect all unredeemed wins (already in DB + just settled above)
    unredeemed_rows = conn.execute(
        "SELECT id, pm_id, side FROM bets "
        "WHERE status='PLACED' AND outcome='WIN' AND (redeemed_tx IS NULL OR redeemed_tx='')"
    ).fetchall()

    if not unredeemed_rows:
        conn.close()
        return {"settled": settled_count, "redeemed": 0}

    # Build a conditionId lookup from bets we just settled
    cid_map = {b["id"]: b for b in newly_won}

    for row in unredeemed_rows:
        bet_id = row["id"]
        side   = row["side"]

        # Get conditionId — use cached value if available, else re-query API
        if bet_id in cid_map:
            condition_id = cid_map[bet_id]["condition_id"]
            neg_risk     = cid_map[bet_id]["neg_risk"]
        else:
            try:
                resp = requests.get(
                    f"{GAMMA_API}/markets",
                    params={"id": row["pm_id"]},
                    timeout=10,
                )
                resp.raise_for_status()
                data   = resp.json()
                market = data[0] if isinstance(data, list) else data
                condition_id = market.get("conditionId", "")
                neg_risk     = market.get("negRisk", False)
            except Exception as e:
                log.warning("Could not fetch conditionId for bet %d: %s", bet_id, e)
                continue

        if not condition_id:
            log.warning("Bet %d has no conditionId — skipping redemption", bet_id)
            continue

        if neg_risk:
            log.warning(
                "Bet %d is a NegRisk market — attempting standard CTF redemption "
                "(may need manual redemption at polymarket.com if this fails)", bet_id
            )

        log.info("Redeeming bet %d: %s side, conditionId %s…", bet_id, side, condition_id[:18] + "…")
        tx_hash = _redeem_win(condition_id, side, private_key)

        if tx_hash:
            conn.execute(
                "UPDATE bets SET redeemed_tx=? WHERE id=?",
                (tx_hash, bet_id),
            )
            conn.commit()
            redeemed_count += 1
        else:
            log.error("Redemption failed for bet %d — will retry on next settle call", bet_id)

    conn.close()
    log.info("Settlement done: %d settled, %d redeemed", settled_count, redeemed_count)
    return {"settled": settled_count, "redeemed": redeemed_count}
