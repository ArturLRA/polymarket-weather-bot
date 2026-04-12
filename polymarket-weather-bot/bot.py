"""
Bot principal — loop que roda periodicamente.
"""
import time
import traceback
from datetime import datetime

from config import CHECK_INTERVAL_MINUTES, DRY_RUN
from weather.scanner import get_actionable_markets
from weather.forecast import get_forecast, estimate_probabilities
from weather.comparator import find_opportunities, format_opportunity
from core.ai_decision import validate_opportunity
from core.risk import kelly_bet_size, format_bet
from core.executor import place_bet
from core.logger import log_opportunity


def run_cycle():
    """Executa um ciclo completo de análise."""
    print(f"\n{'='*60}")
    print(f"[Bot] Ciclo iniciado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"[Bot] Modo: {'DRY RUN (simulação)' if DRY_RUN else '⚠️  LIVE (apostas reais)'}")
    print(f"{'='*60}")

    # 1. Buscar mercados
    markets = get_actionable_markets(days_ahead=3)
    if not markets:
        print("[Bot] Nenhum mercado acionável encontrado.")
        return

    all_opportunities = []

    for market in markets:
        # 2. Buscar previsão
        forecast = get_forecast(market["city"], market["date"])
        if not forecast:
            continue

        # 3. Estimar probabilidades
        probs = estimate_probabilities(forecast, market["outcomes"], market["type"])

        # 4. Encontrar oportunidades
        opps = find_opportunities(market, probs, forecast)
        for opp in opps:
            opp["_forecast"] = forecast
        all_opportunities.extend(opps)

    if not all_opportunities:
        print("[Bot] Nenhuma oportunidade com edge suficiente.")
        return

    print(f"\n[Bot] {len(all_opportunities)} oportunidades encontradas:\n")

    for opp in all_opportunities:
        print(format_opportunity(opp))
        forecast = opp.pop("_forecast")

        # 5. Validar com IA
        ai_result = validate_opportunity(opp, forecast)
        print(f"  IA: {'✓ Aprovado' if ai_result['approved'] else '✗ Rejeitado'} — {ai_result['reasoning']}")

        if not ai_result["approved"]:
            log_opportunity(opp, reason="ia_rejected")
            print()
            continue

        # 6. Calcular tamanho da aposta
        # SELL = comprar NO token: usar probabilidade e preço do lado NO
        if opp["side"] == "SELL":
            bet_size = kelly_bet_size(1 - opp["estimated_prob"], 1 - opp["market_price"])
        else:
            bet_size = kelly_bet_size(opp["estimated_prob"], opp["market_price"])
        if bet_size < 1.0:
            print(f"  Aposta muito pequena (${bet_size:.2f}), pulando.\n")
            log_opportunity(opp, reason="bet_too_small")
            continue

        print(f"  {format_bet(bet_size, opp)}")

        # 7. Executar
        result = place_bet(opp, bet_size)
        print(f"  Status: {result['status']}\n")


def main():
    """Loop principal do bot."""
    print("=" * 48)
    print("   Polymarket Weather Bot v1.0")
    print("   Apostas automatizadas em clima")
    print("=" * 48)

    while True:
        try:
            run_cycle()
        except KeyboardInterrupt:
            print("\n[Bot] Encerrado pelo usuário.")
            break
        except Exception as e:
            print(f"[Bot] Erro no ciclo: {e}")
            traceback.print_exc()

        print(f"\n[Bot] Próximo ciclo em {CHECK_INTERVAL_MINUTES} minutos...")
        try:
            time.sleep(CHECK_INTERVAL_MINUTES * 60)
        except KeyboardInterrupt:
            print("\n[Bot] Encerrado pelo usuário.")
            break


if __name__ == "__main__":
    main()
