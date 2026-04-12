"""
Executor: envia ordens para a Polymarket via py-clob-client.
"""
from datetime import datetime
from config import (
    POLYMARKET_HOST, POLYMARKET_CHAIN_ID, PRIVATE_KEY,
    FUNDER_ADDRESS, SIGNATURE_TYPE, DRY_RUN,
)
from core.logger import log_trade


def get_client():
    """Cria e retorna um ClobClient autenticado."""
    if not PRIVATE_KEY:
        raise ValueError("POLY_PRIVATE_KEY não configurada no .env!")

    from py_clob_client.client import ClobClient

    kwargs = {
        "host": POLYMARKET_HOST,
        "key": PRIVATE_KEY,
        "chain_id": POLYMARKET_CHAIN_ID,
    }
    if FUNDER_ADDRESS:
        kwargs["funder"] = FUNDER_ADDRESS
        kwargs["signature_type"] = SIGNATURE_TYPE

    client = ClobClient(**kwargs)
    client.set_api_creds(client.create_or_derive_api_creds())
    return client


def place_bet(opportunity: dict, bet_size: float) -> dict:
    """
    Executa uma aposta na Polymarket.
    Se DRY_RUN=True, apenas simula e loga.
    """
    result = {
        "timestamp": datetime.now().isoformat(),
        "market": opportunity["market_question"],
        "outcome": opportunity["outcome_label"],
        "side": opportunity["side"],
        "amount": bet_size,
        "market_price": opportunity["market_price"],
        "estimated_prob": opportunity["estimated_prob"],
        "edge": opportunity["edge"],
        "dry_run": DRY_RUN,
        "status": "pending",
    }

    if DRY_RUN:
        result["status"] = "simulated"
        print(f"[DRY RUN] Aposta simulada: ${bet_size:.2f} em {opportunity['outcome_label']}")
        log_trade(result)
        return result

    if not opportunity.get("token_id"):
        result["status"] = "error"
        result["error"] = "token_id não encontrado"
        log_trade(result)
        return result

    try:
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        client = get_client()
        # BUY e SELL sempre resultam em ordem de compra:
        # - BUY: compra token YES do outcome
        # - SELL: compra token NO do outcome (token_id já é o NO token, vindo do comparator)
        order = MarketOrderArgs(
            token_id=opportunity["token_id"],
            amount=bet_size,
            side=BUY,
            order_type=OrderType.FOK,  # Fill or Kill
        )
        signed = client.create_market_order(order)
        resp = client.post_order(signed, OrderType.FOK)

        result["status"] = "executed"
        result["response"] = str(resp)
        print(f"[EXECUTOR] Ordem executada: ${bet_size:.2f} {opportunity['side']} em {opportunity['outcome_label']}")
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        print(f"[EXECUTOR] Erro: {e}")

    log_trade(result)
    return result