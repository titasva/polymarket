"""
app.py — Flask server for the Polymarket Edge Finder + Auto-Bettor.
Run:  python app.py
"""

import logging
import sqlite3
import threading
from datetime import datetime

from flask import Flask, jsonify, render_template, request

from config import DB_PATH
from init_db import init_db

app = Flask(__name__)
log = logging.getLogger(__name__)


# ── DB ─────────────────────────────────────────────────────────────────────────

def _conn():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _d(row):
    return dict(row) if row else None


# ── Page ───────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── Config ─────────────────────────────────────────────────────────────────────

@app.route("/api/config")
def get_config():
    c = _conn()
    rows = c.execute("SELECT key, value FROM settings").fetchall()
    c.close()
    cfg = {r["key"]: r["value"] for r in rows}
    # Mask private key
    pk = cfg.get("pm_private_key", "")
    cfg["pm_private_key_set"] = bool(pk)
    cfg["pm_private_key"] = ""
    if pk:
        cfg["pm_private_key_masked"] = pk[:6] + "…" + pk[-4:]
    # Mask odds API key
    ok = cfg.get("odds_api_key", "")
    cfg["odds_api_key_set"] = bool(ok)
    cfg["odds_api_key"] = ""
    if ok:
        cfg["odds_api_key_masked"] = ok[:4] + "…" + ok[-4:]
    return jsonify(cfg)


@app.route("/api/config", methods=["POST"])
def set_config():
    body    = request.get_json(force=True) or {}
    allowed = {
        "odds_api_key", "bookmakers", "fetch_interval",
        "match_threshold", "min_volume", "tracked_sports",
        "auto_bet_enabled", "bet_size", "min_edge_pct", "max_bets_day",
        "pm_private_key",
    }
    # Keys that are allowed to be saved as empty string (clears the setting)
    _clearable = {"tracked_sports"}
    c = _conn()
    for k, v in body.items():
        if k not in allowed or v is None:
            continue
        sv = str(v).strip()
        if not sv and k not in _clearable:
            continue  # skip empty non-clearable values
        if sv:
            c.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
                (k, sv),
            )
        else:
            # Blank → delete the setting so the default (auto-discover) kicks in
            c.execute("DELETE FROM settings WHERE key=?", (k,))
    c.commit()
    c.close()
    return jsonify({"status": "ok"})


# ── Stats ──────────────────────────────────────────────────────────────────────

@app.route("/api/stats")
def get_stats():
    c = _conn()
    def _n(table, where=""):
        return c.execute(f"SELECT COUNT(*) n FROM {table} {where}").fetchone()["n"]

    def _sum(col, where=""):
        row = c.execute(f"SELECT COALESCE(SUM({col}),0) v FROM bets {where}").fetchone()
        return row["v"]

    stats = {
        "pm_markets":      _n("pm_markets"),
        "odds_events":     _n("odds_events"),
        "matched_markets": _n("market_matches"),
        "edges_found":     _n("edge_log", "WHERE best_edge > 0"),
        "bets_today":      _n("bets", "WHERE status='PLACED' AND date(placed_at)=date('now')"),
        "bets_total":      _n("bets", "WHERE status='PLACED'"),
        "bets_won":        _n("bets", "WHERE status='PLACED' AND outcome='WIN'"),
        "bets_lost":       _n("bets", "WHERE status='PLACED' AND outcome='LOSS'"),
        "bets_pending":    _n("bets", "WHERE status='PLACED' AND outcome IS NULL"),
        "net_pnl":         round(_sum("pnl_usdc", "WHERE status='PLACED'"), 2),
        "last_fetch":   None,
        "api_remaining": None,
    }
    row = c.execute("SELECT value FROM settings WHERE key='last_fetch'").fetchone()
    if row:
        stats["last_fetch"] = row["value"]
    row = c.execute("SELECT value FROM settings WHERE key='odds_api_remaining'").fetchone()
    if row:
        stats["api_remaining"] = row["value"]
    c.close()
    return jsonify(stats)


# ── Edge opportunities ─────────────────────────────────────────────────────────

@app.route("/api/edges")
def get_edges():
    limit    = min(int(request.args.get("limit", 200)), 500)
    min_edge = float(request.args.get("min_edge", 0)) / 100.0
    sport    = request.args.get("sport", "").strip()
    bookmaker = request.args.get("bookmaker", "").strip()

    conditions = ["el.max_edge >= ?"]
    params     = [min_edge]
    if sport:
        conditions.append("oe.sport LIKE ?")
        params.append(f"%{sport}%")
    if bookmaker:
        conditions.append("oe.bookmaker LIKE ?")
        params.append(f"%{bookmaker}%")

    where = "WHERE " + " AND ".join(conditions)

    c = _conn()
    rows = c.execute(f"""
        SELECT
            el.*,
            pm.question, pm.yes_price, pm.no_price, pm.volume,
            pm.end_date, pm.slug, pm.category,
            pm.yes_token_id, pm.no_token_id,
            oe.sport, oe.competition,
            oe.home_team, oe.away_team, oe.commence_time,
            oe.bookmaker, oe.home_decimal, oe.away_decimal,
            oe.home_implied, oe.away_implied, oe.overround,
            mm.pm_yes_is_home, mm.match_score, mm.is_manual,
            (SELECT status FROM bets
             WHERE bets.pm_id = el.pm_id
               AND date(bets.placed_at) = date('now')
             ORDER BY bets.placed_at DESC LIMIT 1) AS today_bet_status
        FROM edge_log el
        JOIN pm_markets     pm ON el.pm_id    = pm.id
        JOIN odds_events    oe ON el.event_id = oe.id
        JOIN market_matches mm ON mm.pm_id = el.pm_id AND mm.event_id = el.event_id
        INNER JOIN (
            SELECT pm_id, MAX(detected_at) latest
            FROM edge_log GROUP BY pm_id
        ) latest ON el.pm_id = latest.pm_id AND el.detected_at = latest.latest
        {where}
        ORDER BY el.best_edge DESC, el.max_edge DESC
        LIMIT ?
    """, params + [limit]).fetchall()
    c.close()

    result = []
    for r in rows:
        row = _d(r)
        # Convert fractions to percent for the frontend
        for f in ("yes_edge", "no_edge", "max_edge", "best_edge",
                  "book_yes_implied", "book_no_implied"):
            if row.get(f) is not None:
                row[f + "_pct"] = round((row[f] or 0) * 100, 2)
        result.append(row)
    return jsonify(result)


# ── Bets ───────────────────────────────────────────────────────────────────────

@app.route("/api/bets")
def get_bets():
    limit  = min(int(request.args.get("limit", 100)), 500)
    offset = int(request.args.get("offset", 0))
    c = _conn()
    rows = c.execute(
        """SELECT * FROM bets
           ORDER BY placed_at DESC
           LIMIT ? OFFSET ?""",
        (limit, offset),
    ).fetchall()
    c.close()
    return jsonify([_d(r) for r in rows])


# ── Markets ────────────────────────────────────────────────────────────────────

@app.route("/api/markets")
def get_markets():
    limit        = min(int(request.args.get("limit", 100)), 500)
    offset       = int(request.args.get("offset", 0))
    matched_only = request.args.get("matched_only", "false").lower() == "true"
    q            = request.args.get("q", "").strip()

    conditions, params = [], []
    if matched_only:
        conditions.append("mm.pm_id IS NOT NULL")
    if q:
        conditions.append("pm.question LIKE ?")
        params.append(f"%{q}%")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    c = _conn()
    rows = c.execute(f"""
        SELECT pm.*,
               mm.event_id, mm.pm_yes_is_home, mm.match_score, mm.is_manual,
               oe.home_team, oe.away_team, oe.bookmaker,
               oe.commence_time, oe.home_decimal, oe.away_decimal,
               oe.home_implied, oe.away_implied, oe.sport, oe.competition
        FROM pm_markets pm
        LEFT JOIN market_matches mm ON pm.id = mm.pm_id
        LEFT JOIN odds_events    oe ON mm.event_id = oe.id
        {where}
        ORDER BY pm.volume DESC
        LIMIT ? OFFSET ?
    """, params + [limit, offset]).fetchall()
    c.close()
    return jsonify([_d(r) for r in rows])


# ── Manual match overrides ─────────────────────────────────────────────────────

@app.route("/api/markets/<pm_id>/unmatch", methods=["POST"])
def unmatch(pm_id):
    c = _conn()
    c.execute("DELETE FROM market_matches WHERE pm_id=?", (pm_id,))
    c.commit()
    c.close()
    return jsonify({"status": "ok"})


# ── Settlement trigger ─────────────────────────────────────────────────────────

@app.route("/api/settle", methods=["POST"])
def trigger_settle():
    def _run():
        from settler import settle_bets
        result = settle_bets()
        log.info("Settlement result: %s", result)
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started"})


# ── Sandbox ────────────────────────────────────────────────────────────────────

@app.route("/api/sandbox")
def get_sandbox():
    limit    = min(int(request.args.get("limit", 300)), 500)
    pm_type  = request.args.get("pm_type", "").strip()
    min_edge = float(request.args.get("min_edge", 0)) / 100.0

    conditions = ["sm.best_edge >= ?"]
    params     = [min_edge]
    if pm_type:
        conditions.append("sm.pm_type = ?")
        params.append(pm_type)

    where = "WHERE " + " AND ".join(conditions)

    c = _conn()
    rows = c.execute(f"""
        SELECT
            sm.*,
            pm.question, pm.yes_price, pm.no_price, pm.volume,
            pm.end_date, pm.slug, pm.category,
            sob.sport, sob.competition,
            sob.home_team, sob.away_team, sob.commence_time,
            sob.bookmaker, sob.market_type AS odds_market_type,
            sob.point, sob.outcome_a_name, sob.outcome_b_name,
            sob.outcome_a_dec, sob.outcome_b_dec
        FROM sandbox_matches sm
        JOIN pm_markets pm           ON sm.pm_id         = pm.id
        JOIN sandbox_odds_markets sob ON sm.odds_market_id = sob.id
        -- Best row per (pm_id, pm_type): highest edge, then match confidence
        INNER JOIN (
            SELECT pm_id, pm_type,
                   MAX(best_edge + match_score * 0.001) AS rank_key
            FROM sandbox_matches
            GROUP BY pm_id, pm_type
        ) best ON sm.pm_id = best.pm_id
               AND sm.pm_type = best.pm_type
               AND (sm.best_edge + sm.match_score * 0.001) = best.rank_key
        {where}
        ORDER BY sm.best_edge DESC, sm.match_score DESC, pm.volume DESC
        LIMIT ?
    """, params + [limit]).fetchall()
    c.close()

    result = []
    for r in rows:
        row = _d(r)
        # Implied probabilities from decimals
        for side, dec_key in (("a", "outcome_a_dec"), ("b", "outcome_b_dec")):
            dec = row.get(dec_key) or 0
            row[f"outcome_{side}_implied"] = round(1.0 / dec, 4) if dec else 0
        # Edge to percent
        for f in ("yes_edge", "no_edge", "best_edge"):
            if row.get(f) is not None:
                row[f + "_pct"] = round((row[f] or 0) * 100, 2)
        result.append(row)
    return jsonify(result)


@app.route("/api/sandbox/clear", methods=["POST"])
def clear_sandbox():
    c = _conn()
    c.execute("DELETE FROM sandbox_matches")
    c.execute("DELETE FROM sandbox_odds_markets")
    c.commit()
    c.close()
    return jsonify({"status": "ok"})


# ── Manual fetch trigger ───────────────────────────────────────────────────────

@app.route("/api/fetch", methods=["POST"])
def trigger_fetch():
    def _run():
        from fetcher import fetch_all
        fetch_all()
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started"})


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000, use_reloader=False)
