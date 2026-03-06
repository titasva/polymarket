"""
app.py — Flask API + frontend server for the Polymarket Arb Finder.
Run:  python app.py
"""

import sqlite3
import threading
from datetime import datetime

from flask import Flask, jsonify, render_template, request

from config import DB_PATH
from init_db import init_db

app = Flask(__name__)


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


# ── Config / settings ──────────────────────────────────────────────────────────

@app.route("/api/config")
def get_config():
    c = _conn()
    rows = c.execute("SELECT key, value FROM settings").fetchall()
    c.close()
    cfg = {r["key"]: r["value"] for r in rows}
    # Mask API key
    key = cfg.get("odds_api_key", "")
    cfg["odds_api_key_set"] = bool(key)
    if key:
        cfg["odds_api_key_masked"] = key[:4] + "…" + key[-4:] if len(key) > 8 else "••••"
        cfg["odds_api_key"] = ""   # never send raw key to frontend
    return jsonify(cfg)


@app.route("/api/config", methods=["POST"])
def set_config():
    body = request.get_json(force=True) or {}
    allowed = {
        "odds_api_key", "bookmakers", "fetch_interval",
        "match_threshold", "min_volume", "tracked_sports",
    }
    c = _conn()
    for k, v in body.items():
        if k in allowed and v is not None:
            c.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
                (k, str(v)),
            )
    c.commit()
    c.close()
    return jsonify({"status": "ok"})


# ── Stats ──────────────────────────────────────────────────────────────────────

@app.route("/api/stats")
def get_stats():
    c = _conn()
    def count(table, where=""):
        return c.execute(f"SELECT COUNT(*) n FROM {table} {where}").fetchone()["n"]

    stats = {
        "pm_markets":       count("pm_markets"),
        "odds_events":      count("odds_events"),
        "matched_markets":  count("market_matches"),
        "arb_opportunities": count("arb_log", "WHERE is_arb=1"),
        "last_fetch": _d(c.execute(
            "SELECT value FROM settings WHERE key='last_fetch'"
        ).fetchone()),
        "api_remaining": _d(c.execute(
            "SELECT value FROM settings WHERE key='odds_api_remaining'"
        ).fetchone()),
    }
    if stats["last_fetch"]:
        stats["last_fetch"] = stats["last_fetch"]["value"]
    if stats["api_remaining"]:
        stats["api_remaining"] = stats["api_remaining"]["value"]
    c.close()
    return jsonify(stats)


# ── Arbitrage feed ─────────────────────────────────────────────────────────────

@app.route("/api/arb")
def get_arb():
    limit     = min(int(request.args.get("limit", 100)), 500)
    only_arb  = request.args.get("only_arb", "false").lower() == "true"
    min_edge  = float(request.args.get("min_edge", 0))
    sport     = request.args.get("sport", "").strip()
    bookmaker = request.args.get("bookmaker", "").strip()

    conditions = ["al.max_edge >= ?"]
    params     = [min_edge / 100.0]   # front-end sends percent, we store fraction

    if only_arb:
        conditions.append("al.is_arb = 1")
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
            al.*,
            pm.question, pm.yes_price, pm.no_price, pm.volume,
            pm.end_date, pm.slug, pm.category,
            oe.sport, oe.competition,
            oe.home_team, oe.away_team, oe.commence_time,
            oe.bookmaker, oe.home_decimal, oe.away_decimal,
            oe.home_implied, oe.away_implied, oe.overround,
            mm.pm_yes_is_home, mm.match_score, mm.is_manual
        FROM arb_log al
        JOIN pm_markets    pm ON al.pm_id    = pm.id
        JOIN odds_events   oe ON al.event_id = oe.id
        JOIN market_matches mm ON mm.pm_id = al.pm_id AND mm.event_id = al.event_id
        INNER JOIN (
            SELECT pm_id, MAX(detected_at) latest
            FROM arb_log
            GROUP BY pm_id
        ) latest ON al.pm_id = latest.pm_id AND al.detected_at = latest.latest
        {where}
        ORDER BY al.is_arb DESC, al.max_edge DESC
        LIMIT ?
    """, params + [limit]).fetchall()
    c.close()

    result = []
    for r in rows:
        row = _d(r)
        # Convert stored fractions back to percentages for UI convenience
        for field in ("yes_edge", "no_edge", "max_edge",
                      "book_yes_implied", "book_no_implied"):
            if row.get(field) is not None:
                row[field + "_pct"] = round(row[field] * 100, 2)
        result.append(row)
    return jsonify(result)


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


# ── Events (for manual matching) ───────────────────────────────────────────────

@app.route("/api/events")
def get_events():
    q = request.args.get("q", "").strip()
    c = _conn()
    if q:
        rows = c.execute("""
            SELECT * FROM odds_events
            WHERE home_team LIKE ? OR away_team LIKE ? OR competition LIKE ?
            ORDER BY commence_time
            LIMIT 50
        """, (f"%{q}%", f"%{q}%", f"%{q}%")).fetchall()
    else:
        rows = c.execute("""
            SELECT * FROM odds_events
            ORDER BY commence_time
            LIMIT 100
        """).fetchall()
    c.close()
    return jsonify([_d(r) for r in rows])


# ── Manual match overrides ─────────────────────────────────────────────────────

@app.route("/api/markets/<pm_id>/match", methods=["POST"])
def set_match(pm_id):
    body       = request.get_json(force=True) or {}
    event_id   = body.get("event_id", "")
    yes_is_home = bool(body.get("yes_is_home", True))
    if not event_id:
        return jsonify({"error": "event_id required"}), 400

    c   = _conn()
    now = datetime.utcnow().isoformat()
    c.execute(
        """INSERT OR REPLACE INTO market_matches
           (pm_id, event_id, pm_yes_is_home, match_score, is_manual, created_at)
           VALUES (?,?,?,1.0,1,?)""",
        (pm_id, event_id, int(yes_is_home), now),
    )
    c.commit()
    c.close()
    return jsonify({"status": "ok"})


@app.route("/api/markets/<pm_id>/unmatch", methods=["POST"])
def unmatch(pm_id):
    c = _conn()
    c.execute("DELETE FROM market_matches WHERE pm_id=?", (pm_id,))
    c.commit()
    c.close()
    return jsonify({"status": "ok"})


# ── Manual fetch trigger ───────────────────────────────────────────────────────

@app.route("/api/fetch", methods=["POST"])
def trigger_fetch():
    def _run():
        from fetcher import fetch_all
        fetch_all()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return jsonify({"status": "started"})


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000, use_reloader=False)
