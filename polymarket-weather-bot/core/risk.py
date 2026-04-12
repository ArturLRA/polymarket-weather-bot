"""
Gestão de risco: Kelly Criterion com plano inteligente de crescimento por estágios.

Estágios (baseados em 2 anos de backtest):
  Seed        $0–300      → teto $5/aposta
  Arranque    $300–800    → teto $15/aposta
  Crescimento $800–2000   → teto $25/aposta
  Escala      $2000–5000  → teto $40/aposta
  Cruzeiro    $5000–15000 → teto $50/aposta
  Maturidade  $15000+     → teto $75/aposta

Proteção automática de drawdown:
  → Bankroll cai 20% do pico: teto reduz 50% (modo defesa)
  → Bankroll recupera 10% do pico: volta ao estágio normal
"""
import json
import os
from config import BANKROLL, KELLY_FRACTION

# ── Estágios de crescimento ─────────────────────────────────────
ESTAGIOS = [
    (0,      5.0),
    (300,   15.0),
    (800,   25.0),
    (2000,  40.0),
    (5000,  50.0),
    (15000, 75.0),
]
NOMES_ESTAGIO = [
    "Seed", "Arranque", "Crescimento", "Escala", "Cruzeiro", "Maturidade"
]

PROTECAO_DD  = 0.20   # ativa defesa se cair 20% do pico
RETOMADA_DD  = 0.10   # desativa defesa quando recuperar 10%
STATE_FILE   = "bankroll_state.json"


# ── Estado persistente do bankroll ─────────────────────────────
def _load_state() -> dict:
    """Carrega estado do bankroll do disco."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "bankroll":  BANKROLL,
        "peak":      BANKROLL,
        "em_defesa": False,
        "n_defesas": 0,
    }

def _save_state(state: dict):
    """Persiste estado do bankroll em disco."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def get_state() -> dict:
    """Retorna estado atual (leitura pública)."""
    return _load_state()

def update_bankroll(resultado_usdc: float):
    """
    Atualiza bankroll após uma aposta.
    resultado_usdc: positivo = ganho, negativo = perda.
    """
    state = _load_state()
    state["bankroll"] = round(max(state["bankroll"] + resultado_usdc, 0.0), 2)

    # Atualiza pico
    if state["bankroll"] > state["peak"]:
        state["peak"] = state["bankroll"]
        # Se estava em defesa e recuperou, sai do modo defesa
        if state["em_defesa"]:
            dd_atual = (state["peak"] - state["bankroll"]) / state["peak"]
            if dd_atual < RETOMADA_DD:
                state["em_defesa"] = False

    # Verifica se entra em modo defesa
    if state["peak"] > 0:
        dd = (state["peak"] - state["bankroll"]) / state["peak"]
        if not state["em_defesa"] and dd >= PROTECAO_DD:
            state["em_defesa"] = True
            state["n_defesas"] = state.get("n_defesas", 0) + 1

    _save_state(state)
    return state


# ── Teto da aposta por estágio ───────────────────────────────────
def _get_teto(bankroll: float, em_defesa: bool) -> float:
    """Retorna o teto máximo de aposta para o bankroll atual."""
    teto = ESTAGIOS[0][1]
    for minbr, maxteto in ESTAGIOS:
        if bankroll >= minbr:
            teto = maxteto
    return round(teto * 0.5, 2) if em_defesa else teto

def get_estagio_nome(bankroll: float) -> str:
    """Retorna o nome do estágio atual."""
    nome = NOMES_ESTAGIO[0]
    for i, (minbr, _) in enumerate(ESTAGIOS):
        if bankroll >= minbr:
            nome = NOMES_ESTAGIO[i]
    return nome


# ── Kelly com estágios ───────────────────────────────────────────
def kelly_bet_size(estimated_prob: float, market_price: float,
                   bankroll: float = None,
                   kelly_fraction: float = None,
                   max_fraction: float = None) -> float:
    """
    Calcula tamanho ideal da aposta usando Kelly Criterion fracionário
    com plano inteligente de crescimento por estágios.

    Parâmetros:
      estimated_prob: probabilidade estimada pelo modelo (0–1)
      market_price:   preço atual do token no mercado (0–1)
      bankroll:       capital disponível (usa state persistido se None)
      kelly_fraction: fração Kelly (usa config se None)
      max_fraction:   ignorado — substituído pelo sistema de estágios
    """
    state = _load_state()

    if bankroll is None:
        bankroll = state["bankroll"]

    if kelly_fraction is None:
        kelly_fraction = KELLY_FRACTION

    if market_price <= 0 or market_price >= 1 or estimated_prob <= 0:
        return 0.0

    if bankroll < 1.0:
        return 0.0

    # Odds decimais
    odds = 1.0 / market_price
    b    = odds - 1          # lucro líquido por $1 apostado

    p = estimated_prob
    q = 1 - p

    # Kelly puro
    kelly = (p * b - q) / b
    if kelly <= 0:
        return 0.0

    # Aplica fração conservadora
    adjusted = kelly * kelly_fraction * bankroll

    # Teto pelo estágio atual (com proteção de drawdown)
    teto = _get_teto(bankroll, state["em_defesa"])

    bet = min(adjusted, teto)
    return round(max(bet, 0.0), 2)


def format_bet(bet_size: float, opportunity: dict) -> str:
    """Formata informações da aposta com contexto do estágio atual."""
    effective_price = (
        (1 - opportunity["market_price"])
        if opportunity.get("side") == "SELL"
        else opportunity["market_price"]
    )
    potential_win  = bet_size * (1.0 / effective_price - 1) if effective_price > 0 else 0
    potential_loss = bet_size
    ratio = f"1:{potential_win/potential_loss:.1f}" if potential_loss > 0 else "N/A"

    state   = _load_state()
    br      = state["bankroll"]
    teto    = _get_teto(br, state["em_defesa"])
    estagio = get_estagio_nome(br)
    defesa  = " [MODO DEFESA]" if state["em_defesa"] else ""
    dd      = (state["peak"] - br) / state["peak"] * 100 if state["peak"] > 0 else 0

    return (
        f"Aposta: ${bet_size:.2f} USDC\n"
        f"Ganho potencial: +${potential_win:.2f}\n"
        f"Perda potencial: -${potential_loss:.2f}\n"
        f"Risco/Retorno: {ratio}\n"
        f"Estágio: {estagio}{defesa} | Teto: ${teto:.0f} | "
        f"Bankroll: ${br:.2f} | DD: {dd:.1f}%"
    )
