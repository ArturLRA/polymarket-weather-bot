"""
Comparador: identifica oportunidades comparando previsão vs mercado.
"""
from config import MIN_EDGE, MIN_CONFIDENCE


def find_opportunities(market: dict, probabilities: list[dict], forecast: dict) -> list[dict]:
    """
    Compara probabilidades estimadas com preços do mercado.
    Retorna lista de oportunidades com edge positivo.
    """
    opportunities = []

    for p in probabilities:
        market_price = p["price"]
        estimated_prob = p["estimated_prob"]

        if market_price <= 0 or market_price >= 1:
            continue

        # Edge = probabilidade estimada - preço do mercado
        # Se edge > 0, o mercado está subprecificando esse outcome
        edge = estimated_prob - market_price

        # Também considerar venda (SHORT) se o mercado sobreprecia
        # Short edge = (1 - estimated_prob) - (1 - market_price) = market_price - estimated_prob
        if edge >= MIN_EDGE and estimated_prob >= MIN_CONFIDENCE:
            opportunities.append({
                "market_question": market["question"],
                "city": market["city"],
                "date": market["date"],
                "outcome_label": p["label"],
                "token_id": p.get("token_id"),
                "market_price": market_price,
                "estimated_prob": estimated_prob,
                "edge": round(edge, 4),
                "side": "BUY",
                "consensus_temp": forecast["consensus_max"],
                "model_spread": forecast["model_spread"],
                "model_count": forecast["model_count"],
                "slug": market.get("slug", ""),
            })
        elif -edge >= MIN_EDGE and (1 - estimated_prob) >= MIN_CONFIDENCE:
            # Oportunidade de SHORT (comprar NO)
            opportunities.append({
                "market_question": market["question"],
                "city": market["city"],
                "date": market["date"],
                "outcome_label": p["label"],
                "token_id": p.get("token_id"),
                "market_price": market_price,
                "estimated_prob": estimated_prob,
                "edge": round(-edge, 4),
                "side": "SELL",
                "consensus_temp": forecast["consensus_max"],
                "model_spread": forecast["model_spread"],
                "model_count": forecast["model_count"],
                "slug": market.get("slug", ""),
            })

    # Ordena por edge (maior primeiro)
    opportunities.sort(key=lambda x: x["edge"], reverse=True)
    return opportunities


def format_opportunity(opp: dict) -> str:
    """Formata oportunidade para exibição."""
    arrow = "[BUY ^]" if opp["side"] == "BUY" else "[SELL v]"
    return (
        f"[{arrow}] {opp['outcome_label']} | "
        f"Mercado: {opp['market_price']:.0%} → Estimado: {opp['estimated_prob']:.0%} | "
        f"Edge: {opp['edge']:.1%} | "
        f"Consenso: {opp['consensus_temp']}°C ({opp['model_count']} modelos, ±{opp['model_spread']}°C)\n"
        f"         {opp['market_question']}"
    )
