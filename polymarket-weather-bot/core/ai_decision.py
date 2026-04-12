"""
Módulo de decisão com IA (Claude API).
Valida oportunidades antes de apostar. Opcional.
"""
import json
import httpx
from config import ANTHROPIC_API_KEY, AI_MODEL


def validate_opportunity(opportunity: dict, forecast: dict) -> dict:
    """Pede ao Claude para avaliar a oportunidade."""
    if not ANTHROPIC_API_KEY:
        return {
            "approved": True,
            "confidence": opportunity["estimated_prob"],
            "reasoning": "IA desativada — aprovado por lógica matemática.",
        }

    prompt = f"""Você é um analista de mercados de previsão de clima.
Analise esta oportunidade e diga se vale apostar.

MERCADO: {opportunity['market_question']}
AÇÃO: {opportunity['side']} em "{opportunity['outcome_label']}"
PREÇO DO MERCADO: {opportunity['market_price']:.0%}
PROBABILIDADE ESTIMADA: {opportunity['estimated_prob']:.0%}
EDGE: {opportunity['edge']:.1%}

DADOS DO MODELO:
- Consenso: {forecast['consensus_max']}°C
- Spread: {forecast['model_spread']}°C
- Modelos: {json.dumps(forecast['models'])}

Responda APENAS com JSON:
{{"approved": true/false, "confidence": 0.0-1.0, "reasoning": "explicação curta"}}"""

    try:
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": AI_MODEL,
                "max_tokens": 300,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        text = resp.json()["content"][0]["text"].strip()
        text = text.replace("```json", "").replace("```", "").strip()
        result = json.loads(text)
        return {
            "approved": bool(result.get("approved", False)),
            "confidence": float(result.get("confidence", 0)),
            "reasoning": result.get("reasoning", ""),
        }
    except Exception as e:
        print(f"[AI] Erro: {e}")
        return {
            "approved": opportunity["edge"] >= 0.10,
            "confidence": opportunity["estimated_prob"],
            "reasoning": f"Fallback: edge={opportunity['edge']:.1%}",
        }
