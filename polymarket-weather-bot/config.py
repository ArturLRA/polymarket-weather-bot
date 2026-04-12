"""
Configuração central do bot.
Use variáveis de ambiente (.env) ou edite diretamente aqui.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── POLYMARKET ──────────────────────────────────────────────
POLYMARKET_HOST = "https://clob.polymarket.com"
POLYMARKET_CHAIN_ID = 137  # Polygon Mainnet

# Chave privada da carteira (NUNCA commite!)
PRIVATE_KEY = os.getenv("POLY_PRIVATE_KEY", "")
# Endereço funder (para proxy wallets — se usar EOA, pode deixar vazio)
FUNDER_ADDRESS = os.getenv("POLY_FUNDER_ADDRESS", "")
# 0 = EOA/MetaMask, 1 = email/Magic wallet
SIGNATURE_TYPE = int(os.getenv("POLY_SIGNATURE_TYPE", "0"))

# ── GAMMA API (busca de mercados) ──────────────────────────
GAMMA_API_URL = "https://gamma-api.polymarket.com"

# ── OPEN-METEO ─────────────────────────────────────────────
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# Cidades monitoradas — lista expandida com cidades que a API realmente publica.
# Para descobrir novas cidades: python scanner.py (mostra todas disponíveis)
# Adicione coordenadas sempre que incluir uma nova cidade.
CITIES = [
    # América do Norte
    {"name": "New York",      "lat": 40.7128,  "lon": -74.0060},
    {"name": "Los Angeles",   "lat": 34.0522,  "lon": -118.2437},
    {"name": "Chicago",       "lat": 41.8781,  "lon": -87.6298},
    {"name": "Miami",         "lat": 25.7617,  "lon": -80.1918},
    # Europa
    {"name": "London",        "lat": 51.5074,  "lon": -0.1278},
    {"name": "Paris",         "lat": 48.8566,  "lon": 2.3522},
    {"name": "Berlin",        "lat": 52.5200,  "lon": 13.4050},
    {"name": "Madrid",        "lat": 40.4168,  "lon": -3.7038},
    {"name": "Rome",          "lat": 41.9028,  "lon": 12.4964},
    {"name": "Amsterdam",     "lat": 52.3676,  "lon": 4.9041},
    {"name": "Vienna",        "lat": 48.2082,  "lon": 16.3738},
    {"name": "Athens",        "lat": 37.9838,  "lon": 23.7275},
    # Ásia
    {"name": "Tokyo",         "lat": 35.6762,  "lon": 139.6503},
    {"name": "Hong Kong",     "lat": 22.3193,  "lon": 114.1694},
    {"name": "Shanghai",      "lat": 31.2304,  "lon": 121.4737},
    {"name": "Beijing",       "lat": 39.9042,  "lon": 116.4074},
    {"name": "Seoul",         "lat": 37.5665,  "lon": 126.9780},
    {"name": "Singapore",     "lat": 1.3521,   "lon": 103.8198},
    {"name": "Mumbai",        "lat": 19.0760,  "lon": 72.8777},
    {"name": "Bangkok",       "lat": 13.7563,  "lon": 100.5018},
    # Oceania e Médio Oriente
    {"name": "Sydney",        "lat": -33.8688, "lon": 151.2093},
    {"name": "Melbourne",     "lat": -37.8136, "lon": 144.9631},
    {"name": "Dubai",         "lat": 25.2048,  "lon": 55.2708},
    # América Central / Caribe (aparece na API)
    {"name": "Panama City",   "lat": 8.9936,   "lon": -79.5197},
]

# ── IA (Claude API) ───────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
AI_MODEL = "claude-sonnet-4-6"

# ── GESTÃO DE RISCO ────────────────────────────────────────
BANKROLL = float(os.getenv("BANKROLL", "100.0"))      # Capital total em USDC
MAX_BET_FRACTION = float(os.getenv("MAX_BET_FRACTION", "0.10"))  # Máximo 10% por aposta
MIN_EDGE = float(os.getenv("MIN_EDGE", "0.05"))         # Edge mínimo de 5%
MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "0.70"))
KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", "0.25"))  # Quarter Kelly

# ── BOT (CLIMA) ────────────────────────────────────────────
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "60"))
DRY_RUN = os.getenv("DRY_RUN", "true").lower() != "false"
LOG_FILE = os.getenv("LOG_FILE", "bot_history.json")

# ── CRIPTO (BTC UP/DOWN 15min) ──────────────────────────────
BINANCE_BASE_URL = "https://api.binance.com"
BINANCE_SYMBOL = os.getenv("BINANCE_SYMBOL", "BTCUSDT")
BINANCE_CANDLE_INTERVAL = "1m"
BINANCE_CANDLE_LIMIT = 100

BTC_UPDOWN_SLUG_PREFIX = "btc-updown"  # slug real: btc-updown-5m-TIMESTAMP

# Indicadores técnicos
RSI_PERIOD           = int(os.getenv("RSI_PERIOD",        "14"))
MACD_FAST            = int(os.getenv("MACD_FAST",         "12"))
MACD_SLOW            = int(os.getenv("MACD_SLOW",         "26"))
MACD_SIGNAL_PERIOD   = int(os.getenv("MACD_SIGNAL",       "9"))
VWAP_SLOPE_LOOKBACK  = int(os.getenv("VWAP_SLOPE_LOOKBACK","5"))

# Thresholds por fase temporal (EARLY > 10min, MID 5-10min, LATE < 5min)
CRYPTO_MIN_EDGE = {
    "EARLY": float(os.getenv("CRYPTO_EARLY_EDGE", "0.05")),
    "MID":   float(os.getenv("CRYPTO_MID_EDGE",   "0.10")),
    "LATE":  float(os.getenv("CRYPTO_LATE_EDGE",  "0.20")),
}
CRYPTO_MIN_PROB = {
    "EARLY": float(os.getenv("CRYPTO_EARLY_PROB", "0.55")),
    "MID":   float(os.getenv("CRYPTO_MID_PROB",   "0.60")),
    "LATE":  float(os.getenv("CRYPTO_LATE_PROB",  "0.65")),
}

CRYPTO_CHECK_INTERVAL_SECONDS = int(os.getenv("CRYPTO_CHECK_INTERVAL_SECONDS", "60"))
CRYPTO_LOG_FILE = os.getenv("CRYPTO_LOG_FILE", "crypto_history.json")