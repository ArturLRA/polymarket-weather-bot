"""
Gestão de risco: Kelly Criterion para calcular tamanho de aposta.
"""
from config import BANKROLL, MAX_BET_FRACTION, KELLY_FRACTION


def kelly_bet_size(estimated_prob: float, market_price: float, bankroll: float = None) -> float:
    """
    Calcula tamanho ideal da aposta usando Kelly Criterion fracionário.
    
    Kelly = (p * b - q) / b
    onde p = probabilidade, q = 1-p, b = odds decimais - 1
    
    Retorna valor em USDC.
    """
    if bankroll is None:
        bankroll = BANKROLL

    if market_price <= 0 or market_price >= 1 or estimated_prob <= 0:
        return 0.0

    # Odds decimais: quanto você recebe por $1 apostado
    # Se preço = 0.40, odds = 1/0.40 = 2.5, lucro = 1.5 por $1
    odds = 1.0 / market_price
    b = odds - 1  # lucro líquido por unidade apostada

    p = estimated_prob
    q = 1 - p

    # Kelly fraction
    kelly = (p * b - q) / b
    if kelly <= 0:
        return 0.0

    # Aplica fração conservadora
    adjusted = kelly * KELLY_FRACTION

    # Limita ao máximo por aposta
    bet = min(adjusted * bankroll, MAX_BET_FRACTION * bankroll)
    return round(max(0, bet), 2)


def format_bet(bet_size: float, opportunity: dict) -> str:
    """Formata informações da aposta."""
    potential_win = bet_size * (1.0 / opportunity["market_price"] - 1)
    potential_loss = bet_size
    return (
        f"Aposta: ${bet_size:.2f} USDC\n"
        f"Ganho potencial: +${potential_win:.2f}\n"
        f"Perda potencial: -${potential_loss:.2f}\n"
        f"Risco/Retorno: 1:{potential_win/potential_loss:.1f}" if potential_loss > 0 else "N/A"
    )
