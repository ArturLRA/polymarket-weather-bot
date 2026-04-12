
"""
CryptoBot — aposta em mercados BTC UP/DOWN 15min na Polymarket.
Pipeline:
  CryptoScanner → CryptoPrice (indicadores) → CryptoDecision → Risk → Executor → Logger

Rode em paralelo com o bot de clima:
  Terminal 1: python bot.py        (clima, ciclo de 60 min)
  Terminal 2: python crypto_bot.py (BTC, ciclo de 60 s)
"""
import time
import traceback
from datetime import datetime

from config import CRYPTO_CHECK_INTERVAL_SECONDS, DRY_RUN, CRYPTO_LOG_FILE
from crypto.scanner import get_active_btc_market
from crypto.price import get_all_indicators
from crypto.decision import score_direction, apply_time_decay, decide, format_crypto_signal
from core.risk import kelly_bet_size, format_bet, get_state, update_bankroll, get_estagio_nome
from core.executor import place_bet
from core.logger import log_trade, log_opportunity


def run_crypto_cycle():
    """Executa um ciclo completo de análise BTC UP/DOWN."""
    state   = get_state()
    br      = state["bankroll"]
    estagio = get_estagio_nome(br)
    defesa  = " | ⚠ MODO DEFESA" if state["em_defesa"] else ""

    print(f"\n{'─'*60}")
    print(f"[CryptoBot] {datetime.now().strftime('%H:%M:%S')} | "
          f"Modo: {'DRY RUN' if DRY_RUN else '⚠️  LIVE'} | "
          f"Bankroll: ${br:.2f} | Estágio: {estagio}{defesa}")

    # 1. Mercado ativo
    market = get_active_btc_market()
    if not market:
        print("[CryptoBot] Nenhum mercado BTC ativo no momento.")
        return

    minutes_remaining = market["minutes_remaining"]
    if minutes_remaining <= 1.0:
        print(f"[CryptoBot] Mercado expirando em {minutes_remaining:.1f} min, aguardando próximo.")
        return

    # 2. Indicadores técnicos (Binance)
    indicators = get_all_indicators()
    if not indicators:
        print("[CryptoBot] Falha ao obter indicadores da Binance.")
        return

    # 3. Scoring direcional
    scores = score_direction(indicators)

    # 4. Decay temporal — usa duração real da janela (15min ou 5min)
    total_minutes = market.get("window_minutes", 15.0)
    scores_adj = apply_time_decay(scores, minutes_remaining, total_minutes=total_minutes)
    scores_adj["rsi"]    = indicators.get("rsi", 50)
    scores_adj["regime"] = indicators.get("regime", "")

    # 5. Decisão
    market_yes_price = market["yes_price"]
    decision = decide(scores_adj, market_yes_price, minutes_remaining)

    print(f"\n  {format_crypto_signal(decision, scores_adj, indicators, minutes_remaining)}")
    print(f"  Mercado: {market['question']}")
    print(f"  Volume: ${market['volume']:,.0f} USDC")

    if decision["action"] == "NO_TRADE":
        log_opportunity({
            "market_question": market["question"],
            "outcome_label":   "BTC UP/DOWN",
            "side":            "NO_TRADE",
            "market_price":    market_yes_price,
            "estimated_prob":  scores_adj.get("prob_up"),
            "edge":            0,
        }, reason=decision.get("reason", "no_edge"))
        return

    # 6. Seleciona token correto
    # UP → comprar YES token | DOWN → comprar NO token
    if decision["token"] == "YES":
        token_id     = market.get("yes_token")
        bet_price    = decision["market_price"]      # preço do YES
        bet_prob     = decision["model_prob"]
    else:
        token_id     = market.get("no_token")
        bet_price    = decision["market_price"]      # preço do NO (já calculado)
        bet_prob     = decision["model_prob"]

    if not token_id:
        print(f"  [CryptoBot] token_id não encontrado para {decision['token']}, pulando.")
        return

    # 7. Kelly bet sizing
    bet_size = kelly_bet_size(bet_prob, bet_price)
    if bet_size < 1.0:
        print(f"  Aposta muito pequena (${bet_size:.2f}), pulando.")
        log_opportunity({
            "market_question": market["question"],
            "outcome_label":   f"BTC {decision['side']}",
            "side":            "BUY",
            "market_price":    bet_price,
            "estimated_prob":  bet_prob,
            "edge":            decision["edge"],
        }, reason="bet_too_small")
        return

    print(f"\n  {format_bet(bet_size, {'market_price': bet_price, 'side': 'BUY'})}")

    # 8. Executar
    opp = {
        "market_question": market["question"],
        "outcome_label":   f"BTC {decision['side']} ({decision['strength']})",
        "token_id":        token_id,
        "market_price":    bet_price,
        "estimated_prob":  bet_prob,
        "edge":            decision["edge"],
        "side":            "BUY",   # sempre compramos (YES para UP, NO para DOWN)
        "slug":            market.get("slug", ""),
        "crypto":          True,
        "phase":           decision["phase"],
    }

    result = place_bet(opp, bet_size)
    print(f"  Status: {result['status']}")

    # Atualiza bankroll no estado persistido
    if result["status"] in ("simulated", "executed"):
        # Em DRY_RUN não sabemos o resultado real ainda — registra como aposta aberta
        # Em LIVE o resultado vem depois da resolução do mercado
        # Por ora subtrai a aposta (pior caso) e o logger corrige ao resolver
        if not DRY_RUN:
            update_bankroll(-bet_size)  # reserva o valor; será corrigido na resolução

    # Log separado para cripto
    result["log_file"] = CRYPTO_LOG_FILE
    log_trade(result)


def main():
    print("=" * 48)
    print("   CryptoBot — BTC UP/DOWN 15min")
    print(f"   Modo: {'DRY RUN (simulação)' if DRY_RUN else '⚠️  LIVE'}")
    print("=" * 48)
    print(f"Ciclo: a cada {CRYPTO_CHECK_INTERVAL_SECONDS}s")
    print("Rode em paralelo com: python bot.py")
    print()

    while True:
        try:
            run_crypto_cycle()
        except KeyboardInterrupt:
            print("\n[CryptoBot] Encerrado pelo usuário.")
            break
        except Exception as e:
            print(f"[CryptoBot] Erro no ciclo: {e}")
            traceback.print_exc()

        print(f"\n[CryptoBot] Próximo ciclo em {CRYPTO_CHECK_INTERVAL_SECONDS}s...")
        try:
            time.sleep(CRYPTO_CHECK_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            print("\n[CryptoBot] Encerrado pelo usuário.")
            break


if __name__ == "__main__":
    main()
