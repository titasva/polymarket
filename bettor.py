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
    # Log the wallet address so it can be verified against the Polymarket account
    try:
        from eth_account import Account
        addr = Account.from_key(private_key).address
        log.info("  CLOB wallet address: %s", addr)
    except Exception:
        pass
    return client


# Polymarket on Polygon — contract addresses
_POLYGON_RPCS = [
    "https://polygon.llamarpc.com",
    "https://rpc.ankr.com/polygon",
    "https://polygon-mainnet.public.blastapi.io",
    "https://rpc-mainnet.maticvigil.com",
]
_USDC_ADDRESS       = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"   # USDC.e
_CTF_EXCHANGE       = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"   # CLOB exchange
_NEG_RISK_ADAPTER   = "0xd91E80cF2eA9d73cC994E963fA2B1d26BfC78b39"   # NegRisk adapter
_MAX_UINT256        = 2**256 - 1

_ERC20_APPROVE_ABI = [
    {
        "name": "approve",
        "type": "function",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount",  "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
    },
    {
        "name": "allowance",
        "type": "function",
        "inputs": [
            {"name": "owner",   "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    },
    {
        "name": "balanceOf",
        "type": "function",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    },
]


def _on_chain_approve(private_key: str) -> None:
    """
    Send an on-chain ERC-20 approve() transaction so the CTF Exchange (and
    NegRisk adapter) can spend the wallet's USDC.  Needs a tiny POL for gas.
    """
    try:
        from web3 import Web3
        from eth_account import Account
    except ImportError:
        log.warning("  web3 not installed — run: pip install web3")
        return

    w3 = None
    for rpc in _POLYGON_RPCS:
        candidate = Web3(Web3.HTTPProvider(rpc))
        try:
            candidate.eth.block_number  # quick connectivity test
            w3 = candidate
            log.info("  Connected to Polygon via %s", rpc)
            break
        except Exception:
            log.debug("  RPC %s unreachable, trying next …", rpc)
    if w3 is None:
        log.error("  All Polygon RPCs failed — cannot send approval tx")
        return

    acct = Account.from_key(private_key)
    addr = acct.address

    usdc = w3.eth.contract(
        address=Web3.to_checksum_address(_USDC_ADDRESS),
        abi=_ERC20_APPROVE_ABI,
    )

    raw_balance = usdc.functions.balanceOf(addr).call()
    log.info("  On-chain USDC balance: $%.2f", raw_balance / 1e6)

    for spender_label, spender in [
        ("CTF Exchange", _CTF_EXCHANGE),
        ("NegRisk Adapter", _NEG_RISK_ADAPTER),
    ]:
        spender_cs = Web3.to_checksum_address(spender)
        current = usdc.functions.allowance(addr, spender_cs).call()
        if current > 0:
            log.info("  %s already approved (allowance=%d)", spender_label, current)
            continue

        log.info("  Approving %s …", spender_label)
        nonce = w3.eth.get_transaction_count(addr)
        gas_price = w3.eth.gas_price

        tx = usdc.functions.approve(spender_cs, _MAX_UINT256).build_transaction({
            "from":     addr,
            "nonce":    nonce,
            "gas":      100_000,
            "gasPrice": gas_price,
            "chainId":  137,
        })
        signed = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        log.info("  Approval tx sent: 0x%s — waiting for confirmation …", tx_hash.hex())
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt.status == 1:
            log.info("  %s approved successfully (block %d)", spender_label, receipt.blockNumber)
        else:
            log.error("  Approval tx reverted for %s", spender_label)


def _ensure_allowance(client, private_key: str = "") -> None:
    """
    Check CLOB-reported allowance; if zero, send a real on-chain approve()
    instead of relying on the CLOB API endpoint (which is only informational).
    """
    try:
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
        resp = client.get_balance_allowance(
            params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        allowance = float(resp.get("allowance", 0))
        if allowance == 0:
            log.info("  Allowance is 0 — sending on-chain approve() …")
            if private_key:
                _on_chain_approve(private_key)
            else:
                log.warning("  No private key available for on-chain approval")
    except Exception as exc:
        log.warning("  Could not set allowance (will attempt order anyway): %s", exc)


def _check_balance(client, size_usdc: float, private_key: str = "") -> bool:
    """
    Returns True if the wallet has enough USDC allowance/balance for the bet.
    Automatically approves the exchange contract if allowance is zero.
    """
    try:
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

        # Auto-approve if needed before checking
        _ensure_allowance(client, private_key)

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
                "Send USDC to your CLOB wallet on Polygon and ensure POL "
                "is available for gas.",
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

        if not _check_balance(client, size_usdc, private_key):
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
