import os

DB_PATH = os.environ.get("DB_PATH", "arb.db")

# The Odds API (https://the-odds-api.com) — free tier: 500 req/month
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Sports polled per fetch cycle (each = 1 API request)
TRACKED_SPORTS = [
    "americanfootball_nfl",
    "americanfootball_ncaaf",
    "basketball_nba",
    "basketball_ncaab",
    "baseball_mlb",
    "icehockey_nhl",
    "soccer_epl",
    "soccer_uefa_champs_league",
    "soccer_usa_mls",
    "mma_mixed_martial_arts",
    "tennis_atp",
    "tennis_wta",
]

DEFAULT_BOOKMAKERS   = "pinnacle"
DEFAULT_FETCH_MIN    = 10        # minutes between fetches
MIN_VOLUME           = 5_000     # minimum Polymarket $ volume
PM_MARKET_LIMIT      = 300       # max markets to fetch per cycle
MATCH_THRESHOLD      = 0.50      # fuzzy match confidence (0–1)
