"""
Módulo de previsão climática via Open-Meteo (gratuito, sem API key).
Puxa dados de GFS e ECMWF e calcula consenso.
"""
import httpx
import re
import math
from config import OPEN_METEO_URL, CITIES


def get_forecast(city_name: str, target_date: str) -> dict | None:
    """Busca previsão de temperatura para uma cidade e data."""
    city = next((c for c in CITIES if c["name"].lower() == city_name.lower()), None)
    if not city:
        print(f"[Weather] Cidade não encontrada: {city_name}")
        return None

    params = {
        "latitude": city["lat"],
        "longitude": city["lon"],
        "daily": "temperature_2m_max,temperature_2m_min",
        "start_date": target_date,
        "end_date": target_date,
        "timezone": "auto",
    }

    try:
        resp = httpx.get(OPEN_METEO_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[Weather] Erro Open-Meteo: {e}")
        return None

    daily = data.get("daily", {})
    if not daily.get("temperature_2m_max"):
        return None

    result = {
        "city": city_name,
        "date": target_date,
        "temp_max": daily["temperature_2m_max"][0],
        "temp_min": daily["temperature_2m_min"][0],
        "models_max": {"best_match": daily["temperature_2m_max"][0]},
        "models_min": {"best_match": daily["temperature_2m_min"][0]},
    }

    # Busca modelos individuais
    for model, url in [
        ("gfs", "https://api.open-meteo.com/v1/gfs"),
        ("ecmwf", "https://api.open-meteo.com/v1/ecmwf"),
    ]:
        try:
            r = httpx.get(url, params=params, timeout=15)
            r.raise_for_status()
            d = r.json().get("daily", {})
            if d.get("temperature_2m_max"):
                result["models_max"][model] = d["temperature_2m_max"][0]
            if d.get("temperature_2m_min"):
                result["models_min"][model] = d["temperature_2m_min"][0]
        except Exception:
            pass

    temps_max = [v for v in result["models_max"].values() if v is not None]
    temps_min = [v for v in result["models_min"].values() if v is not None]
    result["consensus_max"] = round(sum(temps_max) / len(temps_max), 1)
    result["consensus_min"] = round(sum(temps_min) / len(temps_min), 1)
    result["model_spread"] = round(max(temps_max) - min(temps_max), 1)
    result["model_count"] = len(temps_max)
    result["models"] = result["models_max"]  # usado pelo ai_decision
    return result


def _to_celsius(temp: float, label: str) -> float:
    """Converte para Celsius se o label indicar Fahrenheit."""
    if "°F" in label:
        return (temp - 32) * 5 / 9
    return temp


def _parse_temp_label(label: str):
    """
    Extrai tipo e valor(es) de temperatura do label do outcome.
    Retorna (tipo, valor) onde tipo é 'range', 'gte', 'lte', 'exact' ou None.
    Converte automaticamente Fahrenheit para Celsius.
    """
    # Faixa: "between 74-75°F" ou "74–75°C"
    m = re.search(r"(\d+)\s*[-–]\s*(\d+)", label)
    if m:
        lo = _to_celsius(float(m.group(1)), label)
        hi = _to_celsius(float(m.group(2)), label)
        return "range", (lo, hi)

    # >= : símbolo (≥, >) ou texto ("or higher", "or above")
    m = re.search(r"[≥>]\s*(\d+)", label)
    if m:
        return "gte", _to_celsius(float(m.group(1)), label)
    m = re.search(r"(\d+)[^\d]*(?:or higher|or above)", label, re.IGNORECASE)
    if m:
        return "gte", _to_celsius(float(m.group(1)), label)

    # <= : símbolo (≤, <) ou texto ("or below", "or lower")
    m = re.search(r"[≤<]\s*(\d+)", label)
    if m:
        return "lte", _to_celsius(float(m.group(1)), label)
    m = re.search(r"(\d+)[^\d]*(?:or below|or lower)", label, re.IGNORECASE)
    if m:
        return "lte", _to_celsius(float(m.group(1)), label)

    # Exato: "26°C", "80°F", "26"
    m = re.search(r"^(\d+)\s*°?[CcFf]?\s*$", label.strip())
    if m:
        return "exact", _to_celsius(float(m.group(1)), label)

    return None, None


def estimate_probabilities(forecast: dict, outcomes: list[dict], temp_type: str = "highest") -> list[dict]:
    """
    Estima probabilidade de cada outcome baseado em distribuição normal
    centrada no consenso dos modelos.
    temp_type: "highest" usa consensus_max, "lowest" usa consensus_min.
    Suporta labels em °C e °F, com padrões de texto ("or below", "or higher").
    """
    consensus = forecast["consensus_max"] if temp_type == "highest" else forecast["consensus_min"]
    base_std = 1.5
    model_std = forecast["model_spread"] / 2 if forecast["model_spread"] > 0 else 0
    std = max(base_std, base_std + model_std)

    def normal_cdf(x, mu, sigma):
        return 0.5 * (1 + math.erf((x - mu) / (sigma * math.sqrt(2))))

    probabilities = []
    for outcome in outcomes:
        label = outcome["label"]
        kind, val = _parse_temp_label(label)

        if kind == "range":
            lo, hi = val
            prob = normal_cdf(hi + 0.5, consensus, std) - normal_cdf(lo - 0.5, consensus, std)
        elif kind == "gte":
            prob = 1 - normal_cdf(val - 0.5, consensus, std)
        elif kind == "lte":
            prob = normal_cdf(val + 0.5, consensus, std)
        elif kind == "exact":
            prob = normal_cdf(val + 0.5, consensus, std) - normal_cdf(val - 0.5, consensus, std)
        else:
            prob = 0.0

        probabilities.append({**outcome, "estimated_prob": round(max(0.001, min(0.999, prob)), 4)})

    total = sum(p["estimated_prob"] for p in probabilities)
    if total > 0:
        for p in probabilities:
            p["estimated_prob"] = round(p["estimated_prob"] / total, 4)
    return probabilities


if __name__ == "__main__":
    from datetime import date, timedelta
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    f = get_forecast("London", tomorrow)
    if f:
        print(f"London {f['date']}: max={f['temp_max']}°C, consenso={f['consensus_max']}°C, spread={f['model_spread']}°C")
