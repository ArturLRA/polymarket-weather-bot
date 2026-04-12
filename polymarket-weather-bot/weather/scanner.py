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

# Formatos de data tentados em ordem
DATE_FORMATS = ["%B %d, %Y", "%B %d", "%b %d, %Y", "%b %d"]


def _parse_date(date_str: str) -> datetime | None:
    date_str = date_str.strip()
    for fmt in DATE_FORMATS:
        try:
            parsed = datetime.strptime(date_str, fmt)
            if parsed.year == 1900:
                # Sem ano — inferir o mais próximo (pode cruzar virada do ano)
                now = datetime.now()
                parsed = parsed.replace(year=now.year)
                # Se a data ficou muito no passado (>60 dias), provavelmente é ano seguinte
                if (parsed.date() - now.date()).days < -60:
                    parsed = parsed.replace(year=now.year + 1)
            return parsed
        except ValueError:
            continue
    return None


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

    parsed_date = _parse_date(date_str)
    if not parsed_date:
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
    no_token = None
    for i, outcome in enumerate(outcomes_list):
        outcome_lower = str(outcome).lower()
        if outcome_lower == "yes":
            yes_price = prices_list[i] if i < len(prices_list) else 0.0
            yes_token = tokens_list[i] if i < len(tokens_list) else None
        elif outcome_lower == "no":
            no_token = tokens_list[i] if i < len(tokens_list) else None

    return {
        "condition_id": market.get("conditionId", ""),
        "question": question,
        "city": city,
        "date": parsed_date.strftime("%Y-%m-%d"),
        "type": kind,
        "temp_label": temp_label or question,
        "price": yes_price,
        "token_id": yes_token,
        "no_token_id": no_token,
        "volume": float(market.get("volume", 0) or 0),
        "slug": market.get("slug", ""),
    }


def group_markets_by_city_date(parsed_list: list[dict]) -> list[dict]:
    groups: dict[str, dict] = {}

    for p in parsed_list:
        key = f"{p['city'].lower()}|{p['date']}|{p['type']}"
        if key not in groups:
            kind_label = "Highest" if p["type"] == "highest" else "Lowest"
            groups[key] = {
                "question": f"{kind_label} temperature in {p['city']} on {p['date']}?",
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
            "no_token_id": p["no_token_id"],
            "condition_id": p["condition_id"],
        })
        groups[key]["volume"] += p["volume"]

    return list(groups.values())


def _normalize(name: str) -> str:
    """Normaliza nome de cidade para comparação: minúsculo, sem espaços extras."""
    return name.lower().strip()


def get_actionable_markets(days_ahead: int = 5) -> list[dict]:
    raw_markets = search_weather_markets()
    print(f"[Scanner] {len(raw_markets)} mercados brutos encontrados")

    today = datetime.now().date()
    max_date = today + timedelta(days=days_ahead)

    # Conjunto de nomes normalizados das cidades configuradas
    city_names_normalized = {_normalize(c["name"]) for c in CITIES}

    parsed = []
    skipped_city = set()
    skipped_date = 0

    for m in raw_markets:
        p = parse_market(m)
        if not p:
            continue

        market_date = datetime.strptime(p["date"], "%Y-%m-%d").date()

        # Inclui hoje e até days_ahead no futuro
        if market_date < today:
            skipped_date += 1
            continue
        if market_date > max_date:
            skipped_date += 1
            continue

        city_norm = _normalize(p["city"])
        if city_norm not in city_names_normalized:
            skipped_city.add(p["city"])
            continue

        parsed.append(p)

    if skipped_city:
        print(f"[Scanner] Cidades fora da config (adicione ao config.py se quiser): {sorted(skipped_city)}")

    grouped = group_markets_by_city_date(parsed)
    actionable = [g for g in grouped if len(g["outcomes"]) >= 2]

    print(f"[Scanner] {len(actionable)} mercados acionáveis após filtros")
    return actionable


def list_all_available_cities(days_ahead: int = 5) -> list[str]:
    """Lista todas as cidades com mercados ativos, independente da config."""
    raw_markets = search_weather_markets()
    today = datetime.now().date()
    max_date = today + timedelta(days=days_ahead)
    cities = set()
    for m in raw_markets:
        p = parse_market(m)
        if not p:
            continue
        market_date = datetime.strptime(p["date"], "%Y-%m-%d").date()
        if today <= market_date <= max_date:
            cities.add(p["city"])
    return sorted(cities)


if __name__ == "__main__":
    print("=== Cidades disponíveis na API (próximos 5 dias) ===")
    cities = list_all_available_cities()
    for c in cities:
        print(f"  - {c}")

    print("\n=== Mercados acionáveis (cidades da config) ===")
    markets = get_actionable_markets(days_ahead=5)
    for m in markets[:10]:
        print(f"\n{m['question']}")
        print(f"  Cidade: {m['city']} | Data: {m['date']} | Tipo: {m['type']} | Outcomes: {len(m['outcomes'])}")
        for o in m["outcomes"][:3]:
            print(f"  {o['label']}: {o['price']:.2f} ({o['price']*100:.0f}%)")
        if len(m["outcomes"]) > 3:
            print(f"  ... e mais {len(m['outcomes'])-3} outcomes")