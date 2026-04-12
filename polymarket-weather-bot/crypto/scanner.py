"""
Scanner para mercados BTC Up or Down na Polymarket.
Busca eventos com tag 'bitcoin' e slug 'btc-updown-*' via Gamma API.
Retorna o mercado ativo mais próximo de expirar.
"""
import httpx
import json
from datetime import datetime, timezone
from config import GAMMA_API_URL


def _fetch_btc_updown_events() -> list[dict]:
    """
    Busca eventos BTC Up or Down via tag 'bitcoin'.
    Filtra pelos que têm slug começando com 'btc-updown'.
    """
    markets = []
    offset = 0
    while True:
        try:
            resp = httpx.get(
                f"{GAMMA_API_URL}/events",
                params={
                    "tag_slug": "bitcoin",
                    "active": True,
                    "closed": False,
                    "limit": 100,
                    "offset": offset,
                },
                timeout=30,
            )
            resp.raise_for_status()
            events = resp.json()
        except Exception:
            break

        if not events:
            break

        for event in events:
            slug = event.get("slug", "").lower()
            if slug.startswith("btc-updown"):
                for m in event.get("markets", []):
                    # Propaga o endDate do evento se o mercado não tiver
                    if not m.get("endDate"):
                        m["endDate"] = event.get("endDate", "")
                    markets.append(m)

        if len(events) < 100:
            break
        offset += 100

    return markets


def _attach_timing(markets: list[dict]) -> list[dict]:
    """Adiciona _minutes_remaining e _end_dt a cada mercado com endDate."""
    now = datetime.now(timezone.utc)
    result = []
    for m in markets:
        end_str = m.get("endDate") or m.get("end_date_iso") or m.get("endDateIso")
        if not end_str:
            continue
        try:
            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            minutes_remaining = (end_dt - now).total_seconds() / 60
            if minutes_remaining > 0:
                result.append({**m, "_end_dt": end_dt, "_minutes_remaining": minutes_remaining})
        except Exception:
            continue
    return result


def _parse_market(market: dict) -> dict | None:
    """
    Extrai dados relevantes de um mercado BTC Up or Down.
    Outcomes podem ser ["Up","Down"] ou ["Yes","No"] dependendo do mercado.
    Preço vem de lastTradePrice ou bestBid/bestAsk (outcomePrices é None nesta série).
    """
    question = market.get("question", "")

    outcomes_raw = market.get("outcomes", "[]")
    tokens_raw   = market.get("clobTokenIds", "[]")

    try:
        outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else (outcomes_raw or [])
        tokens   = json.loads(tokens_raw)   if isinstance(tokens_raw,   str) else (tokens_raw   or [])
    except Exception:
        return None

    if len(tokens) < 2:
        return None

    # Determina qual índice é UP e qual é DOWN
    up_idx, down_idx = 0, 1  # padrão: primeiro token = Up
    for i, outcome in enumerate(outcomes):
        o = str(outcome).lower()
        if o in ("up", "yes", "higher"):
            up_idx = i
        elif o in ("down", "no", "lower"):
            down_idx = i

    yes_token = tokens[up_idx]   if up_idx   < len(tokens) else None
    no_token  = tokens[down_idx] if down_idx < len(tokens) else None

    # Preço: tenta lastTradePrice, depois bestBid/bestAsk, default 0.5
    last = market.get("lastTradePrice")
    bid  = market.get("bestBid")
    ask  = market.get("bestAsk")
    if last is not None:
        try:
            yes_price = float(last)
        except Exception:
            yes_price = 0.5
    elif bid is not None and ask is not None:
        try:
            yes_price = (float(bid) + float(ask)) / 2
        except Exception:
            yes_price = 0.5
    else:
        yes_price = 0.5

    window_min = _market_window_minutes(market)
    return {
        "question":          question,
        "yes_price":         yes_price,
        "yes_token":         yes_token,
        "no_token":          no_token,
        "condition_id":      market.get("conditionId", ""),
        "slug":              market.get("slug", ""),
        "minutes_remaining": market.get("_minutes_remaining", 0),
        "window_minutes":    window_min,
        "end_dt":            market.get("_end_dt"),
        "volume":            float(market.get("volume", 0) or 0),
    }


def _market_window_minutes(market: dict) -> float:
    """Estima a duração da janela em minutos pelo slug ou título."""
    slug = market.get("slug", "").lower()
    question = market.get("question", "").lower()
    if "15m" in slug or "15min" in slug:
        return 15.0
    if "5m" in slug or "5min" in slug:
        return 5.0
    # Tenta inferir pelo título: "10:00AM-10:15AM" = 15min
    import re
    m = re.search(r'(\d+:\d+[ap]m)-(\d+:\d+[ap]m)', question)
    if m:
        from datetime import datetime
        fmt = "%I:%M%p"
        try:
            t1 = datetime.strptime(m.group(1).upper(), fmt)
            t2 = datetime.strptime(m.group(2).upper(), fmt)
            diff = (t2 - t1).seconds / 60
            return diff if diff > 0 else 15.0
        except Exception:
            pass
    return 15.0  # default: 15min


def get_active_btc_market() -> dict | None:
    """
    Retorna o mercado BTC Up or Down de 15 minutos atualmente ativo,
    priorizando mercados de 15min por terem mais liquidez.
    """
    raw = _fetch_btc_updown_events()
    print(f"[CryptoScanner] {len(raw)} mercados BTC Up/Down encontrados")

    if not raw:
        print("[CryptoScanner] Nenhum mercado BTC Up/Down ativo no momento.")
        return None

    timed = _attach_timing(raw)
    if not timed:
        print("[CryptoScanner] Nenhum mercado BTC com tempo restante.")
        return None

    # Prefere mercados de 15min (mais liquidez); fallback para qualquer um
    timed_15 = [m for m in timed if _market_window_minutes(m) == 15.0]
    pool = timed_15 if timed_15 else timed

    # Mais urgente primeiro dentro do pool
    pool.sort(key=lambda x: x["_minutes_remaining"])
    best = pool[0]

    parsed = _parse_market(best)
    if not parsed:
        print("[CryptoScanner] Falha ao parsear mercado BTC.")
        return None

    print(
        f"[CryptoScanner] Mercado ativo: {parsed['question']} | "
        f"{parsed['minutes_remaining']:.1f} min restantes | "
        f"YES (UP): {parsed['yes_price']:.0%}"
    )
    return parsed


if __name__ == "__main__":
    market = get_active_btc_market()
    if market:
        print(f"\nMercado: {market['question']}")
        print(f"  YES (UP): {market['yes_price']:.0%} | token: {market['yes_token']}")
        print(f"  NO  (DOWN): {1-market['yes_price']:.0%} | token: {market['no_token']}")
        print(f"  {market['minutes_remaining']:.1f} minutos restantes")
    else:
        print("\nNenhum mercado BTC ativo no momento.")
