import os

DB_PATH = os.environ.get("DB_PATH", "arb.db")

# The Odds API (https://the-odds-api.com) — free tier: 500 req/month
# Each sport slug = 1 API request per fetch cycle.
# Leave tracked_sports setting BLANK in the UI to auto-discover all active sports.
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Fallback list used only when /v4/sports auto-discovery fails AND the user
# has not configured a custom list. Covers all major markets Polymarket lists.
TRACKED_SPORTS = [
    # American football
    "americanfootball_nfl",
    "americanfootball_ncaaf",
    "americanfootball_cfl",
    # Baseball
    "baseball_mlb",
    # Basketball
    "basketball_nba",
    "basketball_ncaab",
    "basketball_wnba",
    "basketball_euroleague",
    # Ice hockey
    "icehockey_nhl",
    "icehockey_sweden_hockey_league",
    # Soccer — top leagues
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_france_ligue_one",
    "soccer_netherlands_eredivisie",
    "soccer_portugal_primeira_liga",
    "soccer_turkey_super_league",
    "soccer_belgium_first_div",
    "soccer_england_efl_champ",
    "soccer_scotland_premiership",
    # Soccer — international & cups
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
    "soccer_conmebol_copa_libertadores",
    "soccer_fifa_world_cup",
    # Soccer — Americas & Oceania
    "soccer_usa_mls",
    "soccer_mexico_ligamx",
    "soccer_brazil_campeonato",
    "soccer_argentina_primera_division",
    "soccer_australia_aleague",
    # Combat sports
    "mma_mixed_martial_arts",
    "boxing_boxing",
    # Rugby
    "rugbyleague_nrl",
    "rugbyleague_super_league",
    "rugbyunion_premiership",
    "rugbyunion_six_nations",
    # Australian rules
    "aussierules_afl",
    # Cricket
    "cricket_ipl",
    "cricket_big_bash",
    # Golf
    "golf_pga_tour",
    "golf_masters_tournament",
    "golf_us_open",
    "golf_the_open_championship",
    # Motor racing
    "motorsport_formula1",
]

DEFAULT_BOOKMAKERS    = "pinnacle"
DEFAULT_FETCH_MIN     = 10       # minutes between fetches
MIN_VOLUME            = 1_000    # minimum Polymarket $ volume
PM_MARKET_LIMIT       = 500      # max markets to fetch per cycle
MATCH_THRESHOLD       = 0.40     # fuzzy match confidence (0–1)

# Betting defaults
DEFAULT_BET_SIZE      = 10.0     # USDC per bet
DEFAULT_MIN_EDGE_PCT  = 2.5      # % edge required to trigger auto-bet
DEFAULT_AUTO_BET      = False    # off by default until user enables
DEFAULT_MAX_BETS_DAY  = 10       # safety cap on bets per day
