"""
Sistema de logging centralizado do bot.
Registra todas as apostas (reais e simuladas) e gera relatórios.
"""
import json
import os
from datetime import datetime
from config import LOG_FILE


def log_trade(trade: dict) -> None:
    """Salva uma aposta no histórico JSON."""
    trade["logged_at"] = datetime.now().isoformat()
    history = _load_history()
    history.append(trade)
    _save_history(history)


def log_opportunity(opp: dict, reason: str = "skipped") -> None:
    """Registra uma oportunidade que foi identificada mas não executada."""
    record = {
        "logged_at": datetime.now().isoformat(),
        "type": "opportunity",
        "reason": reason,
        "market": opp.get("market_question", ""),
        "outcome": opp.get("outcome_label", ""),
        "side": opp.get("side", ""),
        "market_price": opp.get("market_price"),
        "estimated_prob": opp.get("estimated_prob"),
        "edge": opp.get("edge"),
    }
    history = _load_history()
    history.append(record)
    _save_history(history)


def get_performance_report() -> dict:
    """
    Gera relatório de performance com base no histórico.
    Retorna dict com estatísticas agregadas.
    """
    history = _load_history()
    trades = [h for h in history if h.get("type") != "opportunity" and h.get("status") in ("simulated", "executed")]

    if not trades:
        return {"error": "Nenhuma aposta registrada ainda."}

    total_bets = len(trades)
    total_wagered = sum(t.get("amount", 0) for t in trades)
    dry_run_count = sum(1 for t in trades if t.get("dry_run", True))
    live_count = total_bets - dry_run_count

    # Agrupa por cidade para ver onde o bot aposta mais
    by_city: dict[str, int] = {}
    by_side: dict[str, int] = {}
    edges = []

    for t in trades:
        market = t.get("market", "")
        # Tenta extrair cidade do nome do mercado
        city = market.split(" in ")[-1].split(" on ")[0] if " in " in market else "?"
        by_city[city] = by_city.get(city, 0) + 1
        side = t.get("side", "?")
        by_side[side] = by_side.get(side, 0) + 1
        if t.get("edge"):
            edges.append(t["edge"])

    avg_edge = sum(edges) / len(edges) if edges else 0

    return {
        "total_bets": total_bets,
        "live_bets": live_count,
        "simulated_bets": dry_run_count,
        "total_wagered_usd": round(total_wagered, 2),
        "avg_edge_pct": round(avg_edge * 100, 2),
        "top_cities": sorted(by_city.items(), key=lambda x: x[1], reverse=True)[:5],
        "by_side": by_side,
        "first_bet": trades[0].get("timestamp", "?"),
        "last_bet": trades[-1].get("timestamp", "?"),
    }


def print_performance_report() -> None:
    """Imprime relatório de performance no terminal."""
    report = get_performance_report()

    if "error" in report:
        print(f"[Logger] {report['error']}")
        return

    print("\n" + "=" * 48)
    print("   RELATÓRIO DE PERFORMANCE")
    print("=" * 48)
    print(f"  Apostas totais:    {report['total_bets']}")
    print(f"  Live:              {report['live_bets']}")
    print(f"  Simuladas:         {report['simulated_bets']}")
    print(f"  Total apostado:    ${report['total_wagered_usd']:.2f} USDC")
    print(f"  Edge médio:        {report['avg_edge_pct']:.1f}%")
    print(f"\n  Top cidades:")
    for city, count in report["top_cities"]:
        print(f"    {city}: {count} aposta(s)")
    print(f"\n  Primeira aposta:   {report['first_bet'][:10]}")
    print(f"  Última aposta:     {report['last_bet'][:10]}")
    print("=" * 48)


def _load_history() -> list:
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_history(history: list) -> None:
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    print_performance_report()