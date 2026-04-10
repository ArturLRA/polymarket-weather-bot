"""
Scanner de mercados de clima na Polymarket.
Usa a Gamma API /events com tag_slug='temperature'.

Formato real dos mercados:
  "Will the highest temperature in London be 17°C on April 10?"
  → cada temperatura é um mercado Yes/No separado
  → agrupamos por cidade+data para montar um mercado completo
"""
import httpx
import re
import json
from datetime import datetime, timedelta
from config import GAMMA_API_URL, CITIES


def search_weather_markets() -> list[dict]:
    found = []
    offset = 0
    limit = 100
    max_pages = 30

    for _ in range(max_pages):
        resp = httpx.get(
            f"{GAMMA_API_URL}/events",
            params={
                "active": True,
                "closed": False,
                "limit": limit,
                "offset": offset,
                "tag_slug": "temperature",
            },
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break

        for event in batch:
            for m in event.get("markets", []):
                found.append(m)

        if len(batch) < limit:
            break
        offset += limit

    return found


PATTERN_NEW = re.compile(
    r"Will the (highest|lowest) temperature in (.+?) be (.+?) on (.+?)\?",
    re.IGNORECASE,
)
PATTERN_OLD = re.compile(
    r"(Highest|Lowest) temperature in (.+?) on (.+?)\?",
    re.IGNORECASE,
)


def parse_market(market: dict) -> dict | None:
    question = market.get("question", "")

    m = PATTERN_NEW.search(question)
    if m:
        kind = m.group(1).lower()
        city = m.group(2).strip()
        temp_label = m.group(3).strip()
        date_str = m.group(4).strip()
    else:
        m = PATTERN_OLD.search(question)
        if not m:
            return None
        kind = m.group(1).lower()
        city = m.group(2).strip()
        temp_label = None
        date_str = m.group(3).strip()

    for fmt in ["%B %d, %Y", "%B %d"]:
        try:
            parsed_date = datetime.strptime(date_str, fmt)
            if parsed_date.year == 1900:
                parsed_date = parsed_date.replace(year=datetime.now().year)
            break
        except ValueError:
            continue
    else:
        return None

    prices_raw = market.get("outcomePrices", "[]")
    if isinstance(prices_raw, str):
        try:
            prices_list = [float(x) for x in json.loads(prices_raw)]
        except Exception:
            prices_list = []
    else:
        prices_list = [float(x) for x in prices_raw]

    outcomes_raw = market.get("outcomes", "[]")
    if isinstance(outcomes_raw, str):
        try:
            outcomes_list = json.loads(outcomes_raw)
        except Exception:
            outcomes_list = []
    else:
        outcomes_list = outcomes_raw

    tokens_raw = market.get("clobTokenIds", "[]")
    if isinstance(tokens_raw, str):
        try:
            tokens_list = json.loads(tokens_raw)
        except Exception:
            tokens_list = []
    else:
        tokens_list = tokens_raw

    yes_price = 0.0
    yes_token = None
    for i, outcome in enumerate(outcomes_list):
        if str(outcome).lower() == "yes":
            yes_price = prices_list[i] if i < len(prices_list) else 0.0
            yes_token = tokens_list[i] if i < len(tokens_list) else None
            break

    return {
        "condition_id": market.get("conditionId", ""),
        "question": question,
        "city": city,
        "date": parsed_date.strftime("%Y-%m-%d"),
        "type": kind,
        "temp_label": temp_label or question,
        "price": yes_price,
        "token_id": yes_token,
        "volume": float(market.get("volume", 0) or 0),
        "slug": market.get("slug", ""),
    }


def group_markets_by_city_date(parsed_list: list[dict]) -> list[dict]:
    groups: dict[str, dict] = {}

    for p in parsed_list:
        key = f"{p['city'].lower()}|{p['date']}|{p['type']}"
        if key not in groups:
            groups[key] = {
                "question": f"Highest temperature in {p['city']} on {p['date']}?",
                "city": p["city"],
                "date": p["date"],
                "type": p["type"],
                "outcomes": [],
                "volume": 0.0,
                "slug": p["slug"],
            }
        groups[key]["outcomes"].append({
            "label": p["temp_label"],
            "price": p["price"],
            "token_id": p["token_id"],
            "condition_id": p["condition_id"],
        })
        groups[key]["volume"] += p["volume"]

    return list(groups.values())


def get_actionable_markets(days_ahead: int = 3) -> list[dict]:
    raw_markets = search_weather_markets()
    print(f"[Scanner] {len(raw_markets)} mercados brutos encontrados")

    today = datetime.now().date()
    max_date = today + timedelta(days=days_ahead)
    city_names = {c["name"].lower() for c in CITIES}

    parsed = []
    for m in raw_markets:
        p = parse_market(m)
        if not p:
            continue
        market_date = datetime.strptime(p["date"], "%Y-%m-%d").date()
        if market_date < today or market_date > max_date:
            continue
        if p["city"].lower() not in city_names:
            continue
        parsed.append(p)

    grouped = group_markets_by_city_date(parsed)
    actionable = [g for g in grouped if len(g["outcomes"]) >= 2]

    print(f"[Scanner] {len(actionable)} mercados acionáveis após filtros")
    return actionable


if __name__ == "__main__":
    markets = get_actionable_markets(days_ahead=5)
    for m in markets[:5]:
        print(f"\n{m['question']}")
        print(f"  Cidade: {m['city']} | Data: {m['date']} | Tipo: {m['type']}")
        for o in m["outcomes"]:
            print(f"  {o['label']}: {o['price']:.2f} ({o['price']*100:.0f}%)")