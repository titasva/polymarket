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
    "americanfootball_nfl_preseason",
    "americanfootball_ncaaf",
    "americanfootball_cfl",
    # Baseball
    "baseball_mlb",
    "baseball_mlb_preseason",
    "baseball_ncaa",
    # Basketball
    "basketball_nba",
    "basketball_nba_preseason",
    "basketball_ncaab",
    "basketball_wnba",
    "basketball_euroleague",
    "basketball_nbl",
    # Ice hockey
    "icehockey_nhl",
    "icehockey_sweden_hockey_league",
    "icehockey_sweden_allsvenskan",
    "icehockey_liiga",
    "icehockey_czech_extraliga",
    # Soccer — top European leagues
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_germany_bundesliga2",
    "soccer_italy_serie_a",
    "soccer_italy_serie_b",
    "soccer_france_ligue_one",
    "soccer_france_ligue_two",
    "soccer_netherlands_eredivisie",
    "soccer_portugal_primeira_liga",
    "soccer_turkey_super_league",
    "soccer_belgium_first_div",
    "soccer_england_efl_champ",
    "soccer_england_league1",
    "soccer_england_league2",
    "soccer_scotland_premiership",
    "soccer_spain_segunda_division",
    "soccer_austria_bundesliga",
    "soccer_denmark_superliga",
    "soccer_norway_eliteserien",
    "soccer_sweden_allsvenskan",
    "soccer_switzerland_superleague",
    "soccer_greece_super_league",
    "soccer_russia_premier_league",
    "soccer_poland_ekstraklasa",
    "soccer_czech_liga",
    "soccer_ukraine_premier_league",
    "soccer_croatia_hnl",
    "soccer_romania_liga1",
    # Soccer — international & cups
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
    "soccer_uefa_conference_league",
    "soccer_conmebol_copa_libertadores",
    "soccer_conmebol_copa_sudamericana",
    "soccer_fifa_world_cup",
    "soccer_concacaf_champs_league",
    "soccer_africa_cup_of_nations",
    # Soccer — Americas & Oceania
    "soccer_usa_mls",
    "soccer_usa_usl_championship",
    "soccer_mexico_ligamx",
    "soccer_brazil_campeonato",
    "soccer_brazil_serie_b",
    "soccer_argentina_primera_division",
    "soccer_chile_campeonato",
    "soccer_colombia_primera_a",
    "soccer_australia_aleague",
    "soccer_japan_j_league",
    "soccer_south_korea_kleague1",
    "soccer_china_superleague",
    # Combat sports
    "mma_mixed_martial_arts",
    "boxing_boxing",
    # Rugby
    "rugbyleague_nrl",
    "rugbyleague_super_league",
    "rugbyunion_premiership",
    "rugbyunion_six_nations",
    "rugbyunion_world_cup",
    "rugbyunion_united_rugby_championship",
    # Australian rules
    "aussierules_afl",
    # Cricket
    "cricket_ipl",
    "cricket_big_bash",
    "cricket_test_match",
    "cricket_odi",
    # Golf
    "golf_pga_tour",
    "golf_masters_tournament",
    "golf_us_open",
    "golf_the_open_championship",
    "golf_dp_world_tour",
    # Motor racing
    "motorsport_formula1",
    # Tennis
    "tennis_atp_australian_open",
    "tennis_atp_french_open",
    "tennis_atp_wimbledon",
    "tennis_atp_us_open",
    "tennis_wta_australian_open",
    "tennis_wta_french_open",
    "tennis_wta_wimbledon",
    "tennis_wta_us_open",
    # Esports — Polymarket & Pinnacle both cover these
    "esports_lol",
    "esports_csgo",
    "esports_dota2",
    "esports_valorant",
    "esports_overwatch",
    "esports_r6_siege",
    "esports_rocket_league",
    "esports_call_of_duty",
    "esports_starcraft2",
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
