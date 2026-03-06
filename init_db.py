import sqlite3

DB_PATH = "polymarket.db"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS traders (
            address     TEXT PRIMARY KEY,
            username    TEXT,
            bio         TEXT,
            profile_image TEXT,
            website     TEXT,
            twitter     TEXT,
            added_at    TEXT DEFAULT (datetime('now')),
            last_updated TEXT
        );

        CREATE TABLE IF NOT EXISTS trades (
            id              TEXT PRIMARY KEY,
            trader_address  TEXT NOT NULL,
            market_title    TEXT,
            market_icon     TEXT,
            outcome         TEXT,
            side            TEXT,
            size            REAL,
            price           REAL,
            usdc_size       REAL,
            condition_id    TEXT,
            transaction_hash TEXT,
            timestamp       INTEGER,
            FOREIGN KEY (trader_address) REFERENCES traders(address)
        );

        CREATE INDEX IF NOT EXISTS idx_trades_trader ON trades(trader_address);
        CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp DESC);
    """)
    conn.commit()
    conn.close()
    print("Database initialised at", DB_PATH)


if __name__ == "__main__":
    init_db()
