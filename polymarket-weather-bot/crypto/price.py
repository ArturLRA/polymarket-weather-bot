"""
Preço BTC e indicadores técnicos via Binance.
Porta fiel da lógica do PolymarketBTC15mAssistant (Node.js → Python):
  VWAP + slope, RSI + slope, MACD (12/26/9), Heiken Ashi streak,
  detecção de failed VWAP reclaim e regime de mercado.
"""
import httpx
from config import (
    BINANCE_BASE_URL, BINANCE_SYMBOL, BINANCE_CANDLE_INTERVAL, BINANCE_CANDLE_LIMIT,
    RSI_PERIOD, MACD_FAST, MACD_SLOW, MACD_SIGNAL_PERIOD, VWAP_SLOPE_LOOKBACK,
)


# ── COLETA DE DADOS ──────────────────────────────────────────

def get_candles() -> list[dict]:
    """Busca candles OHLCV de 1 minuto do BTC via Binance REST."""
    resp = httpx.get(
        f"{BINANCE_BASE_URL}/api/v3/klines",
        params={
            "symbol":   BINANCE_SYMBOL,
            "interval": BINANCE_CANDLE_INTERVAL,
            "limit":    BINANCE_CANDLE_LIMIT,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return [
        {
            "open":   float(k[1]),
            "high":   float(k[2]),
            "low":    float(k[3]),
            "close":  float(k[4]),
            "volume": float(k[5]),
            "time":   int(k[0]),
        }
        for k in resp.json()
    ]


def get_btc_price() -> float | None:
    """Retorna preço spot atual do BTC."""
    try:
        resp = httpx.get(
            f"{BINANCE_BASE_URL}/api/v3/ticker/price",
            params={"symbol": BINANCE_SYMBOL},
            timeout=5,
        )
        resp.raise_for_status()
        return float(resp.json()["price"])
    except Exception as e:
        print(f"[CryptoPrice] Erro ao buscar preço spot: {e}")
        return None


# ── INDICADORES ──────────────────────────────────────────────

def compute_vwap(candles: list[dict]) -> dict:
    """
    VWAP = Σ(typical_price × volume) / Σ(volume)
    Slope = inclinação do VWAP nos últimos VWAP_SLOPE_LOOKBACK candles.
    """
    if not candles:
        return {"vwap": None, "slope": 0.0}

    cum_tpv = sum((c["high"] + c["low"] + c["close"]) / 3 * c["volume"] for c in candles)
    cum_vol = sum(c["volume"] for c in candles)
    vwap = cum_tpv / cum_vol if cum_vol > 0 else None

    slope = 0.0
    if vwap and len(candles) >= VWAP_SLOPE_LOOKBACK:
        recent = candles[-VWAP_SLOPE_LOOKBACK:]
        series = []
        ctpv = cvol = 0.0
        for c in recent:
            ctpv += (c["high"] + c["low"] + c["close"]) / 3 * c["volume"]
            cvol += c["volume"]
            if cvol > 0:
                series.append(ctpv / cvol)
        if len(series) >= 2:
            slope = (series[-1] - series[0]) / (len(series) - 1)

    return {"vwap": vwap, "slope": slope}


def compute_rsi(candles: list[dict]) -> dict:
    """
    RSI com suavização de Wilder + slope (última variação).
    """
    closes = [c["close"] for c in candles]
    if len(closes) < RSI_PERIOD + 2:
        return {"rsi": 50.0, "slope": 0.0}

    diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [max(d, 0.0) for d in diffs]
    losses = [max(-d, 0.0) for d in diffs]

    avg_g = sum(gains[:RSI_PERIOD]) / RSI_PERIOD
    avg_l = sum(losses[:RSI_PERIOD]) / RSI_PERIOD

    rsi_series = []
    for i in range(RSI_PERIOD, len(gains)):
        avg_g = (avg_g * (RSI_PERIOD - 1) + gains[i]) / RSI_PERIOD
        avg_l = (avg_l * (RSI_PERIOD - 1) + losses[i]) / RSI_PERIOD
        rs = avg_g / avg_l if avg_l > 0 else 100.0
        rsi_series.append(100.0 - 100.0 / (1.0 + rs))

    if not rsi_series:
        return {"rsi": 50.0, "slope": 0.0}

    rsi   = rsi_series[-1]
    slope = rsi_series[-1] - rsi_series[-2] if len(rsi_series) >= 2 else 0.0
    return {"rsi": rsi, "slope": slope}


def _ema(values: list[float], period: int) -> list[float]:
    """Exponential Moving Average."""
    if not values:
        return []
    k = 2.0 / (period + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def compute_macd(candles: list[dict]) -> dict:
    """
    MACD (12/26/9):
      line      = EMA12 - EMA26
      signal    = EMA9(line)
      histogram = line - signal
      histogram_delta = variação do histograma (sinal de aceleração)
    """
    closes = [c["close"] for c in candles]
    min_len = MACD_SLOW + MACD_SIGNAL_PERIOD
    if len(closes) < min_len:
        return {"line": 0.0, "signal": 0.0, "histogram": 0.0, "histogram_delta": 0.0}

    ema_fast = _ema(closes, MACD_FAST)
    ema_slow = _ema(closes, MACD_SLOW)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal    = _ema(macd_line, MACD_SIGNAL_PERIOD)
    histogram = [m - s for m, s in zip(macd_line, signal)]

    hist      = histogram[-1]  if histogram           else 0.0
    hist_prev = histogram[-2]  if len(histogram) >= 2 else hist

    return {
        "line":              macd_line[-1] if macd_line else 0.0,
        "signal":            signal[-1]    if signal    else 0.0,
        "histogram":         hist,
        "histogram_delta":   hist - hist_prev,
    }


def compute_heiken_ashi(candles: list[dict]) -> dict:
    """
    Heiken Ashi: detecta cor atual e quantos candles consecutivos da mesma cor.
    HA_close = (O+H+L+C)/4  |  HA_open = (prev_HA_open + prev_HA_close)/2
    """
    if not candles:
        return {"color": None, "streak": 0}

    ha_open = (candles[0]["open"] + candles[0]["close"]) / 2
    color   = None
    streak  = 0

    for c in candles:
        ha_close = (c["open"] + c["high"] + c["low"] + c["close"]) / 4
        new_color = "green" if ha_close >= ha_open else "red"
        streak = streak + 1 if new_color == color else 1
        color  = new_color
        ha_open = (ha_open + ha_close) / 2

    return {"color": color, "streak": streak}


def detect_failed_vwap_reclaim(candles: list[dict], vwap: float) -> bool:
    """
    Padrão bearish: preço cruzou VWAP para cima nos últimos candles
    mas voltou a fechar abaixo — sinal de fraqueza compradora.
    """
    if len(candles) < 3 or not vwap:
        return False
    closes = [c["close"] for c in candles[-6:]]
    crossed_above  = any(c > vwap for c in closes[:-2])
    currently_below = closes[-1] < vwap
    return crossed_above and currently_below


def detect_regime(candles: list[dict], price: float, vwap: float, slope: float) -> str:
    """
    Classifica regime: TREND_UP | TREND_DOWN | RANGE | CHOP.
    CHOP   → baixo volume + preço colado ao VWAP
    RANGE  → 3+ crossovers VWAP nos últimos 10 candles
    TREND  → preço e slope alinhados
    """
    if not price or not vwap:
        return "CHOP"

    if len(candles) >= 10:
        vols    = [c["volume"] for c in candles]
        avg_vol = sum(vols) / len(vols)
        if avg_vol > 0 and vols[-1] / avg_vol < 0.6 and abs(price - vwap) < 0.001 * vwap:
            return "CHOP"

        closes     = [c["close"] for c in candles[-10:]]
        crossovers = sum(
            1 for i in range(1, len(closes))
            if (closes[i - 1] > vwap) != (closes[i] > vwap)
        )
        if crossovers >= 3:
            return "RANGE"

    if price > vwap and slope > 0:
        return "TREND_UP"
    if price < vwap and slope < 0:
        return "TREND_DOWN"
    return "RANGE"


# ── AGREGADOR ────────────────────────────────────────────────

def get_all_indicators() -> dict | None:
    """
    Coleta candles da Binance e calcula todos os indicadores.
    Retorna dict pronto para crypto_decision.py.
    """
    try:
        candles = get_candles()
    except Exception as e:
        print(f"[CryptoPrice] Erro ao buscar candles Binance: {e}")
        return None

    if len(candles) < MACD_SLOW + MACD_SIGNAL_PERIOD + 5:
        print(f"[CryptoPrice] Candles insuficientes ({len(candles)})")
        return None

    price    = candles[-1]["close"]
    vwap_d   = compute_vwap(candles)
    rsi_d    = compute_rsi(candles)
    macd_d   = compute_macd(candles)
    ha_d     = compute_heiken_ashi(candles)

    vwap      = vwap_d["vwap"]
    vwap_slope = vwap_d["slope"]

    return {
        "price":                price,
        "vwap":                 vwap,
        "vwap_slope":           vwap_slope,
        "rsi":                  rsi_d["rsi"],
        "rsi_slope":            rsi_d["slope"],
        "macd_line":            macd_d["line"],
        "macd_histogram":       macd_d["histogram"],
        "macd_histogram_delta": macd_d["histogram_delta"],
        "ha_color":             ha_d["color"],
        "ha_streak":            ha_d["streak"],
        "failed_vwap_reclaim":  detect_failed_vwap_reclaim(candles, vwap) if vwap else False,
        "regime":               detect_regime(candles, price, vwap, vwap_slope),
    }


if __name__ == "__main__":
    ind = get_all_indicators()
    if ind:
        print(f"BTC:   ${ind['price']:,.2f}")
        print(f"VWAP:  ${ind['vwap']:,.2f}  (slope: {ind['vwap_slope']:+.2f})")
        print(f"RSI:   {ind['rsi']:.1f}  (slope: {ind['rsi_slope']:+.2f})")
        print(f"MACD:  line={ind['macd_line']:.2f}  hist={ind['macd_histogram']:.2f}  delta={ind['macd_histogram_delta']:+.2f}")
        print(f"HA:    {ind['ha_color']} × {ind['ha_streak']} candles")
        print(f"Regime: {ind['regime']}  |  Failed VWAP reclaim: {ind['failed_vwap_reclaim']}")
