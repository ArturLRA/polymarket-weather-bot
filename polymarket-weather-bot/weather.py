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
        "models": {"best_match": daily["temperature_2m_max"][0]},
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
                result["models"][model] = d["temperature_2m_max"][0]
        except Exception:
            pass

    temps = [v for v in result["models"].values() if v is not None]
    result["consensus_max"] = round(sum(temps) / len(temps), 1)
    result["model_spread"] = round(max(temps) - min(temps), 1)
    result["model_count"] = len(temps)
    return result


def estimate_probabilities(forecast: dict, outcomes: list[dict]) -> list[dict]:
    """
    Estima probabilidade de cada outcome baseado em distribuição normal
    centrada no consenso dos modelos.
    """
    consensus = forecast["consensus_max"]
    base_std = 1.5
    model_std = forecast["model_spread"] / 2 if forecast["model_spread"] > 0 else 0
    std = max(base_std, base_std + model_std)

    def normal_cdf(x, mu, sigma):
        return 0.5 * (1 + math.erf((x - mu) / (sigma * math.sqrt(2))))

    probabilities = []
    for outcome in outcomes:
        label = outcome["label"]
        range_m = re.search(r"(\d+)\s*[-–]\s*(\d+)", label)
        gte_m = re.search(r"[≥>]\s*(\d+)", label)
        lte_m = re.search(r"[≤<]\s*(\d+)", label)
        exact_m = re.search(r"(\d+)\s*°?C?$", label)

        if range_m:
            lo, hi = float(range_m.group(1)), float(range_m.group(2))
            prob = normal_cdf(hi + 0.5, consensus, std) - normal_cdf(lo - 0.5, consensus, std)
        elif gte_m:
            prob = 1 - normal_cdf(float(gte_m.group(1)) - 0.5, consensus, std)
        elif lte_m:
            prob = normal_cdf(float(lte_m.group(1)) + 0.5, consensus, std)
        elif exact_m:
            t = float(exact_m.group(1))
            prob = normal_cdf(t + 0.5, consensus, std) - normal_cdf(t - 0.5, consensus, std)
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
