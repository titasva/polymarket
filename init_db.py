import sqlite3
from config import DB_PATH


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS pm_markets (
            id             TEXT PRIMARY KEY,
            question       TEXT,
            yes_price      REAL,
            no_price       REAL,
            yes_token_id   TEXT,
            no_token_id    TEXT,
            volume         REAL,
            end_date       TEXT,
            slug           TEXT,
            category       TEXT,
            last_updated   TEXT
        );

        CREATE TABLE IF NOT EXISTS odds_events (
            id             TEXT PRIMARY KEY,
            raw_event_id   TEXT,
            sport          TEXT,
            competition    TEXT,
            home_team      TEXT,
            away_team      TEXT,
            commence_time  TEXT,
            bookmaker      TEXT,
            bookmaker_key  TEXT,
            home_decimal   REAL,
            away_decimal   REAL,
            draw_decimal   REAL,
            home_implied   REAL,
            away_implied   REAL,
            overround      REAL,
            last_updated   TEXT
        );

        CREATE TABLE IF NOT EXISTS market_matches (
            pm_id          TEXT PRIMARY KEY,
            event_id       TEXT,
            pm_yes_is_home INTEGER,
            match_score    REAL,
            is_manual      INTEGER DEFAULT 0,
            created_at     TEXT DEFAULT (datetime('now'))
        );

        -- Edge snapshots (one row per fetch cycle per matched pair)
        CREATE TABLE IF NOT EXISTS edge_log (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            pm_id            TEXT,
            event_id         TEXT,
            pm_yes_price     REAL,
            pm_no_price      REAL,
            book_yes_decimal REAL,
            book_no_decimal  REAL,
            book_yes_implied REAL,
            book_no_implied  REAL,
            yes_edge         REAL,
            no_edge          REAL,
            max_edge         REAL,
            best_side        TEXT,   -- YES, NO, or NULL
            best_edge        REAL,   -- positive edge value (0 if none)
            detected_at      TEXT DEFAULT (datetime('now'))
        );

        -- Bets placed via auto-betting
        CREATE TABLE IF NOT EXISTS bets (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            pm_id        TEXT,
            question     TEXT,
            event_name   TEXT,
            bookmaker    TEXT,
            side         TEXT,        -- YES or NO
            pm_price     REAL,        -- PM price at time of bet
            book_implied REAL,        -- book's implied prob for same outcome
            edge_pct     REAL,        -- edge % at time of bet
            size_usdc    REAL,
            token_id     TEXT,
            order_id     TEXT,
            status       TEXT,        -- PLACED, FAILED, SKIPPED
            error        TEXT,
            placed_at    TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_edge_pm      ON edge_log(pm_id);
        CREATE INDEX IF NOT EXISTS idx_edge_det     ON edge_log(detected_at DESC);
        CREATE INDEX IF NOT EXISTS idx_edge_val     ON edge_log(max_edge DESC);
        CREATE INDEX IF NOT EXISTS idx_bets_placed  ON bets(placed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_pm_vol       ON pm_markets(volume DESC);
    """)
    conn.commit()

    # Migrations: add settlement columns if they don't exist yet
    for col, typedef in [
        ("outcome",    "TEXT"),          # WIN or LOSS
        ("pnl_usdc",   "REAL"),          # net profit/loss in USDC
        ("settled_at", "TEXT"),          # ISO timestamp of settlement
    ]:
        try:
            conn.execute(f"ALTER TABLE bets ADD COLUMN {col} {typedef}")
            conn.commit()
        except Exception:
            pass  # column already exists

    conn.close()
    print("Database initialised:", DB_PATH)


if __name__ == "__main__":
    init_db()
