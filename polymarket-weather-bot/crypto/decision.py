"""
Motor de decisão para mercados BTC UP/DOWN 15min.
Porta fiel da lógica do PolymarketBTC15mAssistant:
  1. score_direction   — pontua UP vs DOWN com indicadores técnicos
  2. apply_time_decay  — reduz confiança conforme tempo passa
  3. decide            — thresholds por fase (EARLY / MID / LATE)
"""
from config import CRYPTO_MIN_EDGE, CRYPTO_MIN_PROB


# ── 1. SCORING DIRECIONAL ────────────────────────────────────

def score_direction(indicators: dict) -> dict:
    """
    Atribui pontos para UP e DOWN com base em 7 sinais.
    Pontuação máxima possível: UP=13, DOWN=13.

    Retorna probabilidades normalizadas (0.0–1.0).
    """
    up   = 0.0
    down = 0.0

    price    = indicators.get("price", 0)
    vwap     = indicators.get("vwap")
    slope    = indicators.get("vwap_slope", 0)
    rsi      = indicators.get("rsi", 50)
    rsi_sl   = indicators.get("rsi_slope", 0)
    macd_l   = indicators.get("macd_line", 0)
    hist_d   = indicators.get("macd_histogram_delta", 0)
    ha_color = indicators.get("ha_color")
    ha_streak = indicators.get("ha_streak", 0)
    failed   = indicators.get("failed_vwap_reclaim", False)

    # Preço vs VWAP (+2)
    if vwap:
        if price > vwap:   up   += 2
        elif price < vwap: down += 2

    # Slope do VWAP (+2)
    if slope > 0:    up   += 2
    elif slope < 0:  down += 2

    # RSI + slope (+2)
    if rsi > 55 and rsi_sl > 0:  up   += 2
    elif rsi < 45 and rsi_sl < 0: down += 2

    # MACD histogram expandindo (+2)
    if hist_d > 0:   up   += 2
    elif hist_d < 0: down += 2

    # MACD line vs zero (+1)
    if macd_l > 0:   up   += 1
    elif macd_l < 0: down += 1

    # Heiken Ashi consecutivos >= 2 (+1)
    if ha_color == "green" and ha_streak >= 2: up   += 1
    elif ha_color == "red"  and ha_streak >= 2: down += 1

    # Failed VWAP reclaim — forte sinal bearish (+3)
    if failed:
        down += 3

    total = up + down
    if total == 0:
        return {"prob_up": 0.5, "prob_down": 0.5, "up_points": 0, "down_points": 0}

    return {
        "prob_up":    round(up   / total, 4),
        "prob_down":  round(down / total, 4),
        "up_points":  up,
        "down_points": down,
    }


# ── 2. DECAY TEMPORAL ────────────────────────────────────────

def apply_time_decay(scores: dict, minutes_remaining: float, total_minutes: float = 5.0) -> dict:
    """
    Suaviza as probabilidades conforme o tempo passa.
    Em vez de multiplicar por zero (que destruía o sinal),
    interpola entre a probabilidade do modelo e 0.5 (incerteza).

    Com 100% do tempo restante: prob = modelo (confiança total)
    Com   0% do tempo restante: prob = 0.5    (sem confiança)
    """
    decay = min(max(minutes_remaining / total_minutes, 0.0), 1.0)
    prob_up   = 0.5 + (scores["prob_up"]   - 0.5) * decay
    prob_down = 0.5 + (scores["prob_down"] - 0.5) * decay
    return {
        **scores,
        "prob_up":    round(prob_up,   4),
        "prob_down":  round(prob_down, 4),
        "time_decay": round(decay, 3),
    }


# ── 3. DECISÃO COM PHASE THRESHOLDS ─────────────────────────

def get_phase(minutes_remaining: float, total_minutes: float = 5.0) -> str:
    """Fases relativas ao total da janela (funciona para 5min ou 15min)."""
    ratio = minutes_remaining / total_minutes
    if ratio > 0.60: return "EARLY"   # primeiros 40% do tempo
    if ratio > 0.30: return "MID"     # 30–60%
    return "LATE"                      # últimos 30%


def decide(scores: dict, market_yes_price: float, minutes_remaining: float) -> dict:
    """
    Compara probabilidade do modelo vs preço do mercado.
    Thresholds de edge e probabilidade mínima aumentam conforme
    a janela de 15 minutos se aproxima do fim.

    market_yes_price: preço do token YES (0–1)
      YES = BTC encerra ACIMA do preço inicial → UP
      NO  = BTC encerra ABAIXO               → DOWN
    """
    total_minutes = 15.0  # mercados BTC Up/Down de 15 minutos (padrão)
    phase    = get_phase(minutes_remaining, total_minutes)
    min_edge = CRYPTO_MIN_EDGE[phase]
    min_prob = CRYPTO_MIN_PROB[phase]

    if not (0 < market_yes_price < 1):
        return {"action": "NO_TRADE", "reason": "Preço inválido", "phase": phase}

    # Filtros baseados em backtest de 30 dias:
    # - RSI neutro (40-60): 57.5% acerto vs RSI extremo que perde dinheiro
    # - Regime CHOP: 48.1% acerto (pior que cara-ou-coroa) → evitar
    rsi    = scores.get("rsi",    50)
    regime = scores.get("regime", "")

    if not (40 <= rsi <= 60):
        return {
            "action": "NO_TRADE",
            "reason": f"RSI {rsi:.1f} fora da zona neutra (40-60)",
            "phase":  phase,
        }

    if regime == "CHOP":
        return {
            "action": "NO_TRADE",
            "reason": f"Regime CHOP — sem tendencia definida",
            "phase":  phase,
        }

    market_up   = market_yes_price
    market_down = 1 - market_yes_price

    prob_up   = scores.get("prob_up",   0)
    prob_down = scores.get("prob_down", 0)

    edge_up   = prob_up   - market_up
    edge_down = prob_down - market_down

    def strength(edge: float) -> str:
        if edge >= 0.20: return "STRONG"
        if edge >= 0.10: return "GOOD"
        return "OPTIONAL"

    # Só entra em sinais STRONG — backtest mostrou GOOD e OPTIONAL perdem dinheiro
    if edge_up > min_edge and prob_up > min_prob and strength(edge_up) == "STRONG":
        return {
            "action":       "ENTER",
            "side":         "UP",
            "token":        "YES",
            "phase":        phase,
            "edge":         round(edge_up, 4),
            "model_prob":   prob_up,
            "market_price": market_up,
            "strength":     "STRONG",
            "rsi":          rsi,
        }

    if edge_down > min_edge and prob_down > min_prob and strength(edge_down) == "STRONG":
        return {
            "action":       "ENTER",
            "side":         "DOWN",
            "token":        "NO",
            "phase":        phase,
            "edge":         round(edge_down, 4),
            "model_prob":   prob_down,
            "market_price": market_down,
            "strength":     "STRONG",
            "rsi":          rsi,
        }

    # Identificar motivo real da rejeição
    if edge_up >= min_edge or edge_down >= min_edge:
        # Edge ok, mas probabilidade insuficiente
        best_prob = max(prob_up, prob_down)
        reason = f"Prob {best_prob:.0%} < mínimo {min_prob:.0%} (fase {phase})"
    else:
        best = max(edge_up, edge_down)
        reason = f"Edge {best:.1%} < mínimo {min_edge:.1%} (fase {phase})"

    return {
        "action": "NO_TRADE",
        "reason": reason,
        "phase":  phase,
    }


# ── FORMATAÇÃO ───────────────────────────────────────────────

def format_crypto_signal(decision: dict, scores: dict, indicators: dict, minutes_remaining: float) -> str:
    """Formata sinal de cripto para exibição no terminal."""
    price  = indicators.get("price", 0)
    rsi    = indicators.get("rsi", 0)
    regime = indicators.get("regime", "?")
    decay  = scores.get("time_decay", 1.0)

    header = (
        f"BTC: ${price:,.2f} | RSI: {rsi:.1f} | "
        f"Regime: {regime} | Decay: {decay:.0%} | "
        f"{minutes_remaining:.1f} min restantes"
    )

    if decision["action"] == "NO_TRADE":
        return f"[NO TRADE] {decision.get('reason', '')} | {header}"

    arrow = "[UP  ↑]" if decision["side"] == "UP" else "[DOWN ↓]"
    return (
        f"{arrow} {decision['strength']} | "
        f"Edge: {decision['edge']:.1%} | "
        f"Modelo: {decision['model_prob']:.0%} vs Mercado: {decision['market_price']:.0%} | "
        f"Fase: {decision['phase']} | {header}"
    )
