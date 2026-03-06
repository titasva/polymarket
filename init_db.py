import sqlite3
from config import DB_PATH


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        -- key/value settings store
        CREATE TABLE IF NOT EXISTS settings (
            key     TEXT PRIMARY KEY,
            value   TEXT
        );

        -- Active Polymarket markets
        CREATE TABLE IF NOT EXISTS pm_markets (
            id           TEXT PRIMARY KEY,
            question     TEXT,
            yes_price    REAL,
            no_price     REAL,
            volume       REAL,
            end_date     TEXT,
            slug         TEXT,
            category     TEXT,
            last_updated TEXT
        );

        -- Odds events from bookmakers (one row per event × bookmaker)
        CREATE TABLE IF NOT EXISTS odds_events (
            id             TEXT PRIMARY KEY,   -- event_id + "_" + bookmaker_key
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

        -- Best auto/manual match between a PM market and an odds event
        CREATE TABLE IF NOT EXISTS market_matches (
            pm_id          TEXT PRIMARY KEY,
            event_id       TEXT,
            pm_yes_is_home INTEGER,   -- 1 = PM YES maps to home team winning
            match_score    REAL,
            is_manual      INTEGER DEFAULT 0,
            created_at     TEXT DEFAULT (datetime('now'))
        );

        -- Timestamped arb snapshots (one row per fetch cycle per matched pair)
        CREATE TABLE IF NOT EXISTS arb_log (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            pm_id            TEXT,
            event_id         TEXT,
            pm_yes_price     REAL,
            pm_no_price      REAL,
            book_yes_decimal REAL,
            book_no_decimal  REAL,
            book_yes_implied REAL,
            book_no_implied  REAL,
            yes_edge         REAL,   -- book_yes_implied - pm_yes  (+ means PM is cheap)
            no_edge          REAL,   -- book_no_implied  - pm_no
            max_edge         REAL,   -- max(|yes_edge|, |no_edge|)
            is_arb           INTEGER,
            arb_return_pct   REAL,
            best_strategy    TEXT,
            detected_at      TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_arb_pm      ON arb_log(pm_id);
        CREATE INDEX IF NOT EXISTS idx_arb_det     ON arb_log(detected_at DESC);
        CREATE INDEX IF NOT EXISTS idx_arb_edge    ON arb_log(max_edge DESC);
        CREATE INDEX IF NOT EXISTS idx_pm_vol      ON pm_markets(volume DESC);
        CREATE INDEX IF NOT EXISTS idx_oe_sport    ON odds_events(sport);
    """)
    conn.commit()
    conn.close()
    print("Database initialised:", DB_PATH)


if __name__ == "__main__":
    init_db()
