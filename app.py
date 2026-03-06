"""
app.py – Flask server for the Polymarket Tracker.
Run:  python app.py
"""

import sqlite3
from datetime import datetime

from flask import Flask, jsonify, render_template, request, abort

from init_db import init_db
from fetcher import update_trader

DB_PATH = "polymarket.db"
app = Flask(__name__)


# ─── DB helper ────────────────────────────────────────────────────────────────

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def row_to_dict(row):
    return dict(row) if row else None


# ─── Page ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ─── API: traders ─────────────────────────────────────────────────────────────

@app.route("/api/traders", methods=["GET"])
def list_traders():
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM traders ORDER BY added_at DESC"
    ).fetchall()
    conn.close()
    return jsonify([row_to_dict(r) for r in rows])


@app.route("/api/traders", methods=["POST"])
def add_trader():
    body = request.get_json(force=True) or {}
    address = (body.get("address") or "").strip().lower()
    if not address:
        abort(400, "address is required")

    conn = get_conn()
    existing = conn.execute(
        "SELECT address FROM traders WHERE address = ?", (address,)
    ).fetchone()
    if existing:
        conn.close()
        return jsonify({"status": "already_exists"})

    conn.execute(
        "INSERT INTO traders (address) VALUES (?)", (address,)
    )
    conn.commit()
    conn.close()

    # Kick off an immediate fetch in the background (best-effort)
    try:
        update_trader(address)
    except Exception as exc:
        app.logger.warning("Initial fetch failed for %s: %s", address, exc)

    conn = get_conn()
    trader = row_to_dict(
        conn.execute("SELECT * FROM traders WHERE address = ?", (address,)).fetchone()
    )
    conn.close()
    return jsonify(trader), 201


@app.route("/api/traders/<address>", methods=["DELETE"])
def remove_trader(address):
    address = address.lower()
    conn = get_conn()
    conn.execute("DELETE FROM trades WHERE trader_address = ?", (address,))
    conn.execute("DELETE FROM traders WHERE address = ?", (address,))
    conn.commit()
    conn.close()
    return jsonify({"status": "removed"})


@app.route("/api/traders/<address>/refresh", methods=["POST"])
def refresh_trader(address):
    address = address.lower()
    try:
        update_trader(address)
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500
    return jsonify({"status": "ok"})


# ─── API: trades ──────────────────────────────────────────────────────────────

@app.route("/api/trades", methods=["GET"])
def list_trades():
    address = request.args.get("address", "").strip().lower()
    limit = min(int(request.args.get("limit", 100)), 500)
    offset = int(request.args.get("offset", 0))

    conn = get_conn()
    if address:
        rows = conn.execute(
            """SELECT t.*, tr.username, tr.profile_image
               FROM trades t
               JOIN traders tr ON t.trader_address = tr.address
               WHERE t.trader_address = ?
               ORDER BY t.timestamp DESC
               LIMIT ? OFFSET ?""",
            (address, limit, offset),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT t.*, tr.username, tr.profile_image
               FROM trades t
               JOIN traders tr ON t.trader_address = tr.address
               ORDER BY t.timestamp DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
    conn.close()
    return jsonify([row_to_dict(r) for r in rows])


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
