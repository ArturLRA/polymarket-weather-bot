"""
Configuração central do bot.
Use variáveis de ambiente ou edite diretamente.
"""
import os

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

# Cidades monitoradas (ajuste conforme mercados ativos)
CITIES = [
    {"name": "New York",    "lat": 40.7128,  "lon": -74.0060},
    {"name": "London",      "lat": 51.5074,  "lon": -0.1278},
    {"name": "Tokyo",       "lat": 35.6762,  "lon": 139.6503},
    {"name": "Hong Kong",   "lat": 22.3193,  "lon": 114.1694},
    {"name": "Shanghai",    "lat": 31.2304,  "lon": 121.4737},
    {"name": "Sydney",      "lat": -33.8688, "lon": 151.2093},
    {"name": "Dubai",       "lat": 25.2048,  "lon": 55.2708},
    {"name": "Singapore",   "lat": 1.3521,   "lon": 103.8198},
    {"name": "Paris",       "lat": 48.8566,  "lon": 2.3522},
    {"name": "Los Angeles", "lat": 34.0522,  "lon": -118.2437},
]

# ── IA (Claude API) ───────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
AI_MODEL = "claude-sonnet-4-6"

# ── GESTÃO DE RISCO ────────────────────────────────────────
BANKROLL = 100.0          # Capital total em USDC
MAX_BET_FRACTION = 0.10   # Máximo 10% do bankroll por aposta
MIN_EDGE = 0.05           # Edge mínimo de 5% para apostar
MIN_CONFIDENCE = 0.70     # Confiança mínima da previsão
KELLY_FRACTION = 0.25     # Quarter Kelly (conservador)

# ── BOT ────────────────────────────────────────────────────
CHECK_INTERVAL_MINUTES = 60
DRY_RUN = True            # True = simula, não aposta
LOG_FILE = "bot_history.json"
