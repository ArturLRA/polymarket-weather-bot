"""
Scanner de mercados de clima na Polymarket.
Usa a Gamma API para buscar mercados ativos de temperatura diária.
"""
import httpx
import re
from datetime import datetime, timedelta
from config import GAMMA_API_URL, CITIES


WEATHER_KEYWORDS = [
    "highest temperature", "lowest temperature",
    "highest temp", "lowest temp",
    "daily high", "daily low",
    "weather in", "temperature in",
]


def search_weather_markets() -> list[dict]:
    """
    Busca mercados ativos de clima/temperatura na Gamma API.
    Filtra por palavras-chave no título, pois o filtro de tag não é confiável.
    """
    found = []
    offset = 0
    limit = 100
    max_pages = 30  # até 3000 mercados

    for _ in range(max_pages):
        resp = httpx.get(
            f"{GAMMA_API_URL}/markets",
            params={
                "active": True,
                "closed": False,
                "limit": limit,
                "offset": offset,
            },
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break

        for m in batch:
            question = m.get("question", "").lower()
            if any(kw in question for kw in WEATHER_KEYWORDS):
                found.append(m)

        if len(batch) < limit:
            break
        offset += limit

    return found


def parse_temperature_market(market: dict) -> dict | None:
    """
    Extrai informações estruturadas de um mercado de temperatura.
    Ex: "Highest temperature in London on April 11?"
    Retorna dict com city, date, outcomes, ou None se não parsear.
    """
    question = market.get("question", "")

    # Padrão: "Highest temperature in <City> on <Date>?"
    pattern = r"(?:Highest|Lowest)\s+temperature\s+in\s+(.+?)\s+on\s+(.+?)\?"
    match = re.search(pattern, question, re.IGNORECASE)
    if not match:
        return None

    city_name = match.group(1).strip()
    date_str = match.group(2).strip()

    # Tenta parsear a data
    try:
        # Formatos comuns: "April 11", "April 11, 2026"
        for fmt in ["%B %d, %Y", "%B %d"]:
            try:
                parsed = datetime.strptime(date_str, fmt)
                if parsed.year == 1900:  # sem ano
                    parsed = parsed.replace(year=datetime.now().year)
                break
            except ValueError:
                continue
        else:
            return None
    except Exception:
        return None

    # Extrai outcomes (opções de temperatura) e seus preços
    outcomes_raw = market.get("outcomes", "[]")
    if isinstance(outcomes_raw, str):
        import json
        try:
            outcomes_list = json.loads(outcomes_raw)
        except json.JSONDecodeError:
            outcomes_list = []
    else:
        outcomes_list = outcomes_raw

    prices_raw = market.get("outcomePrices", "[]")
    if isinstance(prices_raw, str):
        import json
        try:
            prices_list = json.loads(prices_raw)
        except json.JSONDecodeError:
            prices_list = []
    else:
        prices_list = prices_raw

    tokens = market.get("clobTokenIds", "[]")
    if isinstance(tokens, str):
        import json
        try:
            tokens_list = json.loads(tokens)
        except json.JSONDecodeError:
            tokens_list = []
    else:
        tokens_list = tokens

    outcomes = []
    for i, label in enumerate(outcomes_list):
        outcomes.append({
            "label": label,
            "price": float(prices_list[i]) if i < len(prices_list) else 0.0,
            "token_id": tokens_list[i] if i < len(tokens_list) else None,
        })

    is_highest = "highest" in question.lower()

    return {
        "condition_id": market.get("conditionId", ""),
        "question_id": market.get("questionId", ""),
        "question": question,
        "city": city_name,
        "date": parsed.strftime("%Y-%m-%d"),
        "type": "highest" if is_highest else "lowest",
        "outcomes": outcomes,
        "volume": float(market.get("volume", 0)),
        "liquidity": float(market.get("liquidity", 0)),
        "slug": market.get("slug", ""),
    }


def get_actionable_markets(days_ahead: int = 3) -> list[dict]:
    """
    Retorna mercados de temperatura parseados, filtrados por:
    - Cidades na nossa lista
    - Datas dentro de `days_ahead` dias
    - Pelo menos 2 outcomes
    """
    raw_markets = search_weather_markets()
    print(f"[Scanner] {len(raw_markets)} mercados encontrados na Gamma API")

    today = datetime.now().date()
    max_date = today + timedelta(days=days_ahead)
    city_names = {c["name"].lower() for c in CITIES}

    actionable = []
    for m in raw_markets:
        parsed = parse_temperature_market(m)
        if not parsed:
            continue

        market_date = datetime.strptime(parsed["date"], "%Y-%m-%d").date()
        if market_date < today or market_date > max_date:
            continue

        if parsed["city"].lower() not in city_names:
            continue

        if len(parsed["outcomes"]) < 2:
            continue

        actionable.append(parsed)

    print(f"[Scanner] {len(actionable)} mercados acionáveis após filtros")
    return actionable


if __name__ == "__main__":
    markets = get_actionable_markets()
    for m in markets[:5]:
        print(f"\n{m['question']}")
        print(f"  Cidade: {m['city']} | Data: {m['date']}")
        for o in m["outcomes"]:
            print(f"  {o['label']}: {o['price']:.2f} ({o['price']*100:.0f}%)")
