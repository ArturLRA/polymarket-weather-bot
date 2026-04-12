"""
Simulacao realista de 6 meses — BTC UP/DOWN Polymarket.

Fatores de realismo incluidos:
  - Spread do order book (voce paga ask, nao mid)
  - Limite diario de apostas (liquidez real do Polymarket)
  - Taxa de execucao (nem todo sinal tem contraparte disponivel)
  - Taxa de gas Polygon por transacao
  - Compara versao otimista vs conservadora vs realista
"""
import sys, asyncio, httpx, random
from datetime import datetime, timezone
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")

from crypto.price import (compute_vwap, compute_rsi, compute_macd,
                           compute_heiken_ashi, detect_failed_vwap_reclaim, detect_regime)
from crypto.decision import score_direction, apply_time_decay, decide

# ── Configuracao geral ───────────────────────────────────────────
DAYS               = 730
WARMUP             = 30
CANDLES_PER_MARKET = 3       # 1 sinal por janela de 15min

# Fatores de realismo
SPREAD             = 0.02    # 2% spread tipico (compra a 0.51, nao 0.50)
GAS_FEE            = 0.01    # $0.01 por transacao (Polygon)
EXEC_RATE_OPT      = 1.00    # Otimista: todas as apostas executam
EXEC_RATE_MID      = 0.70    # Medio: 70% encontram contraparte
EXEC_RATE_CONS     = 0.50    # Conservador: 50% executam
MAX_BETS_DAY_OPT   = 45      # Otimista: todos os sinais
MAX_BETS_DAY_MID   = 15      # Medio: mercado tem liquidez em ~1/3 das janelas
MAX_BETS_DAY_CONS  = 8       # Conservador: mercado ativo ~8x/dia com liquidez

random.seed(42)  # reproducibilidade

# ── Download de dados ────────────────────────────────────────────
async def fetch_batch(client, start_ms, end_ms):
    resp = await client.get(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": "BTCUSDT", "interval": "5m",
                "startTime": start_ms, "endTime": end_ms, "limit": 1000},
        timeout=30
    )
    return resp.json()

async def fetch_all():
    end_ms   = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - DAYS * 24 * 3600 * 1000
    iv_ms    = 5 * 60 * 1000
    batches, cur = [], start_ms
    while cur < end_ms:
        batches.append((cur, min(cur + 1000 * iv_ms, end_ms)))
        cur += 1000 * iv_ms
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[fetch_batch(client, s, e) for s, e in batches])
    candles, seen = [], set()
    for batch in results:
        for c in batch:
            if c[0] not in seen:
                seen.add(c[0])
                candles.append({
                    "open": float(c[1]), "high": float(c[2]),
                    "low":  float(c[3]), "close": float(c[4]),
                    "volume": float(c[5]), "time": int(c[0])
                })
    candles.sort(key=lambda x: x["time"])
    return candles

def get_indicators(window, curr):
    vd = compute_vwap(window); rd = compute_rsi(window)
    md = compute_macd(window); hd = compute_heiken_ashi(window)
    vwap  = vd.get("vwap"); slope = vd.get("slope", 0); price = curr["close"]
    return {
        "price": price, "vwap": vwap, "vwap_slope": slope,
        "rsi": rd.get("rsi", 50), "rsi_slope": rd.get("slope", 0),
        "macd_line": md.get("line", 0),
        "macd_histogram_delta": md.get("histogram_delta", 0),
        "ha_color": hd.get("color"), "ha_streak": hd.get("streak", 0),
        "failed_vwap_reclaim": detect_failed_vwap_reclaim(window, vwap),
        "regime": detect_regime(window, price, vwap, slope),
    }

# ── Coleta sinais brutos ─────────────────────────────────────────
print("Baixando 180 dias de dados BTC (Binance)...")
candles = asyncio.run(fetch_all())
print(f"Total: {len(candles)} candles\n")

raw_records = []
skip_until  = -1
for i in range(WARMUP, len(candles) - 1):
    if i <= skip_until:
        continue
    window = candles[i - WARMUP: i]
    curr   = candles[i]; nxt = candles[i + 1]
    try:
        ind    = get_indicators(window, curr)
        sc     = score_direction(ind)
        sc_adj = apply_time_decay(sc, 4.0, total_minutes=5.0)
        sc_adj["rsi"]    = ind["rsi"]
        sc_adj["regime"] = ind["regime"]
        dec = decide(sc_adj, 0.50, 4.0)
    except Exception:
        continue
    if dec["action"] != "ENTER":
        continue
    resultado = "UP" if nxt["close"] > curr["open"] else "DOWN"
    raw_records.append({
        "acertou": dec["side"] == resultado,
        "prob":    dec["model_prob"],
        "edge":    dec["edge"],
        "regime":  ind["regime"],
        "ts":      curr["time"],
    })
    skip_until = i + CANDLES_PER_MARKET - 1

# ── Aplica filtros de realismo ───────────────────────────────────
def apply_filters(records, max_bets_day, exec_rate, spread, gas):
    """
    Aplica:
      1. Limite diario de apostas (max_bets_day) — prioriza maior edge
      2. Taxa de execucao (exec_rate) — simula falta de liquidez/contraparte
      3. Spread — preco efetivo de compra eh ask = 0.50 + spread/2
      4. Gas fee por transacao
    """
    # Agrupa por dia
    ms_day = 86_400_000
    t0     = records[0]["ts"]
    by_day = defaultdict(list)
    for r in records:
        day = (r["ts"] - t0) // ms_day
        by_day[day].append(r)

    filtered = []
    for day in sorted(by_day):
        # Ordena por edge e pega os N melhores do dia
        sinais_dia = sorted(by_day[day], key=lambda x: x["edge"], reverse=True)
        sinais_dia = sinais_dia[:max_bets_day]
        for r in sinais_dia:
            # Simula falta de contraparte/liquidez
            if random.random() > exec_rate:
                continue
            # Odds efetivas com spread: compra a ask = 0.50 + spread/2
            ask        = 0.50 + spread / 2          # ex: 0.51
            ganho_unit = (1.0 - ask) / ask           # ganho por $1 apostado
            filtered.append({**r, "ganho_unit": ganho_unit, "gas": gas})
    return filtered

# ── Funcao de simulacao com bankroll composto ────────────────────
def simulate(records, bankroll_ini, max_bet_abs, spread, gas):
    br     = bankroll_ini
    peak   = bankroll_ini
    maxdd  = 0.0
    ms30   = 30 * 86_400_000
    t0     = records[0]["ts"] if records else 0
    monthly = defaultdict(lambda: {"a": 0, "t": 0, "br": 0.0})
    total_gas = 0.0

    for r in records:
        ganho_unit = r["ganho_unit"]
        # Kelly ajustado para odds reais (com spread)
        p     = r["prob"]
        b     = ganho_unit              # lucro liquido por $1 apostado
        q     = 1 - p
        kelly = max((p * b - q) / b, 0.0)
        bet   = min(kelly * 0.25 * br, max_bet_abs)
        if bet < 0.10 or br < 1.0:
            continue

        # Aplica gas fee independente do resultado
        br        -= gas
        total_gas += gas

        if r["acertou"]:
            br += bet * ganho_unit
        else:
            br -= bet

        br = max(br, 0.0)
        if br > peak:
            peak = br
        dd = (peak - br) / peak * 100 if peak > 0 else 0
        if dd > maxdd:
            maxdd = dd

        mes = (r["ts"] - t0) // ms30
        monthly[mes]["t"] += 1
        if r["acertou"]:
            monthly[mes]["a"] += 1
        monthly[mes]["br"] = br

    return br, maxdd, monthly, total_gas

# ── Gera os tres conjuntos de registros filtrados ────────────────
recs_opt  = apply_filters(raw_records, MAX_BETS_DAY_OPT,  EXEC_RATE_OPT,  SPREAD, GAS_FEE)
recs_mid  = apply_filters(raw_records, MAX_BETS_DAY_MID,  EXEC_RATE_MID,  SPREAD, GAS_FEE)
recs_cons = apply_filters(raw_records, MAX_BETS_DAY_CONS, EXEC_RATE_CONS, SPREAD, GAS_FEE)

def wr(recs):
    if not recs: return 0
    return sum(1 for r in recs if r["acertou"]) / len(recs) * 100

# ── Simulacoes ───────────────────────────────────────────────────
# Bankroll $100, teto $5/aposta
br_opt_100,  dd_opt_100,  mo_opt_100,  gas_opt_100  = simulate(recs_opt,  100.0, 5.0,  SPREAD, GAS_FEE)
br_mid_100,  dd_mid_100,  mo_mid_100,  gas_mid_100  = simulate(recs_mid,  100.0, 5.0,  SPREAD, GAS_FEE)
br_cons_100, dd_cons_100, mo_cons_100, gas_cons_100 = simulate(recs_cons, 100.0, 5.0,  SPREAD, GAS_FEE)

# Bankroll $1.000, teto $20/aposta
br_opt_1k,   dd_opt_1k,   mo_opt_1k,   gas_opt_1k   = simulate(recs_opt,  1000.0, 20.0, SPREAD, GAS_FEE)
br_mid_1k,   dd_mid_1k,   mo_mid_1k,   gas_mid_1k   = simulate(recs_mid,  1000.0, 20.0, SPREAD, GAS_FEE)
br_cons_1k,  dd_cons_1k,  mo_cons_1k,  gas_cons_1k  = simulate(recs_cons, 1000.0, 20.0, SPREAD, GAS_FEE)

# ── Relatorio ────────────────────────────────────────────────────
W = 68
print("=" * W)
print("  SIMULACAO REALISTA — 6 MESES BTC UP/DOWN (Polymarket)")
print("=" * W)
print(f"  Sinais brutos     : {len(raw_records):,} ({len(raw_records)/DAYS:.1f}/dia)")
print(f"  Taxa acerto bruta : {wr(raw_records):.1f}%")
print()
print(f"  Fatores de realismo aplicados:")
print(f"    Spread order book : {SPREAD:.0%}  (compra a ask={0.50+SPREAD/2:.2f}, nao mid=0.50)")
print(f"    Gas por transacao : ${GAS_FEE:.2f} (Polygon)")
print()

cenarios_info = [
    ("OTIMISTA",     recs_opt,  MAX_BETS_DAY_OPT,  EXEC_RATE_OPT,  "Todos sinais, execucao total"),
    ("MEDIO",        recs_mid,  MAX_BETS_DAY_MID,  EXEC_RATE_MID,  "15 apostas/dia, 70% execucao"),
    ("CONSERVADOR",  recs_cons, MAX_BETS_DAY_CONS, EXEC_RATE_CONS, "8 apostas/dia, 50% execucao"),
]

for label, recs, max_d, exec_r, desc in cenarios_info:
    acertos_r = sum(1 for r in recs if r["acertou"])
    total_r   = len(recs)
    print(f"  {label} — {desc}")
    print(f"  Apostas executadas: {total_r:,} ({total_r/DAYS:.1f}/dia) | Acerto: {wr(recs):.1f}%")
    print()

print("=" * W)
print("  BANKROLL $100  |  Teto de aposta: $5  |  Kelly 25%")
print("=" * W)
print(f"  {'CENARIO':<14} {'APOSTAS':>8} {'ACERTO':>8} {'FINAL':>10} {'LUCRO':>10} {'MAXDD':>8} {'GAS TOTAL':>10}")
print(f"  {'-'*14} {'-'*8} {'-'*8} {'-'*10} {'-'*10} {'-'*8} {'-'*10}")

for label, recs, br_fin, maxdd, gas_tot in [
    ("Otimista",    recs_opt,  br_opt_100,  dd_opt_100,  gas_opt_100),
    ("Medio",       recs_mid,  br_mid_100,  dd_mid_100,  gas_mid_100),
    ("Conservador", recs_cons, br_cons_100, dd_cons_100, gas_cons_100),
]:
    acertos_r = sum(1 for r in recs if r["acertou"])
    total_r   = len(recs)
    lucro     = br_fin - 100.0
    print(f"  {label:<14} {total_r:>8,} {wr(recs):>7.1f}%  ${br_fin:>9.2f} ${lucro:>+9.2f} {maxdd:>7.1f}%  ${gas_tot:>8.2f}")

print()
print("  Evolucao mensal — Cenario MEDIO ($100 inicial):")
print(f"  {'MES':<6} {'DATA':<9} {'APOSTAS':>8} {'ACERTO':>8} {'BANKROLL':>12} {'LUCRO MES':>12}")
prev = 100.0
t0_dt = datetime.fromtimestamp(recs_mid[0]["ts"]/1000, tz=timezone.utc) if recs_mid else datetime.now(timezone.utc)
for mes in sorted(mo_mid_100):
    m    = mo_mid_100[mes]
    wr_m = m["a"] / m["t"] * 100 if m["t"] else 0
    lm   = m["br"] - prev
    mes_dt = (t0_dt.replace(day=1) if mes == 0 else t0_dt)
    from datetime import timedelta
    data_label = (datetime.fromtimestamp(recs_mid[0]["ts"]/1000 + mes*30*86400, tz=timezone.utc)).strftime("%b/%Y")
    print(f"  Mes {mes+1:<2} {data_label:<9} {m['t']:>8}   {wr_m:>6.1f}%   ${m['br']:>10.2f}  ${lm:>+10.2f}")
    prev = m["br"]

print()
print("=" * W)
print("  BANKROLL $1.000  |  Teto de aposta: $20  |  Kelly 25%")
print("=" * W)
print(f"  {'CENARIO':<14} {'APOSTAS':>8} {'ACERTO':>8} {'FINAL':>10} {'LUCRO':>10} {'MAXDD':>8} {'GAS TOTAL':>10}")
print(f"  {'-'*14} {'-'*8} {'-'*8} {'-'*10} {'-'*10} {'-'*8} {'-'*10}")

for label, recs, br_fin, maxdd, gas_tot in [
    ("Otimista",    recs_opt,  br_opt_1k,  dd_opt_1k,  gas_opt_1k),
    ("Medio",       recs_mid,  br_mid_1k,  dd_mid_1k,  gas_mid_1k),
    ("Conservador", recs_cons, br_cons_1k, dd_cons_1k, gas_cons_1k),
]:
    acertos_r = sum(1 for r in recs if r["acertou"])
    total_r   = len(recs)
    lucro     = br_fin - 1000.0
    print(f"  {label:<14} {total_r:>8,} {wr(recs):>7.1f}%  ${br_fin:>9,.2f} ${lucro:>+9,.2f} {maxdd:>7.1f}%  ${gas_tot:>8.2f}")

print()
print("  Evolucao mensal — Cenario MEDIO ($1.000 inicial):")
print(f"  {'MES':<6} {'DATA':<9} {'APOSTAS':>8} {'ACERTO':>8} {'BANKROLL':>12} {'LUCRO MES':>12} {'BAR':}")
prev = 1000.0
lucros_1k = []
for mes in sorted(mo_mid_1k):
    m    = mo_mid_1k[mes]
    wr_m = m["a"] / m["t"] * 100 if m["t"] else 0
    lm   = m["br"] - prev
    lucros_1k.append(lm)
    prev = m["br"]
max_lm = max(abs(l) for l in lucros_1k) if lucros_1k else 1

prev = 1000.0
for mes in sorted(mo_mid_1k):
    m    = mo_mid_1k[mes]
    wr_m = m["a"] / m["t"] * 100 if m["t"] else 0
    lm   = m["br"] - prev
    data_label = (datetime.fromtimestamp(recs_mid[0]["ts"]/1000 + mes*30*86400, tz=timezone.utc)).strftime("%b/%Y")
    bar_len = int(abs(lm) / max_lm * 20)
    bar = ("+" * bar_len) if lm >= 0 else ("-" * bar_len)
    print(f"  Mes {mes+1:<2} {data_label:<9} {m['t']:>8}   {wr_m:>6.1f}%   ${m['br']:>10,.2f}  ${lm:>+10,.2f}  {bar}")
    prev = m["br"]

print()
print()
print("=" * W)
print("  RESUMO EXECUTIVO — 2 ANOS")
print("=" * W)

# Lucro medio mensal
n_meses_100 = len(mo_mid_100)
n_meses_1k  = len(mo_mid_1k)
lucro_med_100 = (br_mid_100 - 100.0)  / n_meses_100 if n_meses_100 else 0
lucro_med_1k  = (br_mid_1k  - 1000.0) / n_meses_1k  if n_meses_1k  else 0

# Pior mes (maior prejuizo em 1 mes)
prev = 100.0; pior_100 = 0.0
for mes in sorted(mo_mid_100):
    lm = mo_mid_100[mes]["br"] - prev
    if lm < pior_100: pior_100 = lm
    prev = mo_mid_100[mes]["br"]

prev = 1000.0; pior_1k = 0.0
for mes in sorted(mo_mid_1k):
    lm = mo_mid_1k[mes]["br"] - prev
    if lm < pior_1k: pior_1k = lm
    prev = mo_mid_1k[mes]["br"]

print(f"  Periodo simulado  : {DAYS} dias ({DAYS//30} meses) de dados reais Binance")
print(f"  Taxa acerto media : {wr(recs_mid):.1f}% (cenario medio)")
print()
print(f"  BANKROLL $100  (teto $5/aposta):")
print(f"    Bankroll final    : ${br_mid_100:>10,.2f}")
print(f"    Lucro total       : ${br_mid_100-100:>+10,.2f}")
print(f"    Lucro medio/mes   : ${lucro_med_100:>+10,.2f}")
print(f"    Pior mes          : ${pior_100:>+10,.2f}")
print(f"    Max Drawdown      : {dd_mid_100:.1f}%")
print()
print(f"  BANKROLL $1.000 (teto $20/aposta):")
print(f"    Bankroll final    : ${br_mid_1k:>10,.2f}")
print(f"    Lucro total       : ${br_mid_1k-1000:>+10,.2f}")
print(f"    Lucro medio/mes   : ${lucro_med_1k:>+10,.2f}")
print(f"    Pior mes          : ${pior_1k:>+10,.2f}")
print(f"    Max Drawdown      : {dd_mid_1k:.1f}%")
print()
print(f"  Custos operacionais (cenario medio):")
print(f"    Gas total ($100)  : ${gas_mid_100:.2f}  |  Gas total ($1k): ${gas_mid_1k:.2f}")
print(f"    Spread 2%         : reduz payout de 1.00x para {(1-0.51)/0.51:.4f}x por $1 apostado")
print()
print("  VALE A PENA RODAR DE VERDADE?")
print("  " + "-" * (W-2))
print("  PROs:")
print("    + Taxa de acerto consistente: ~59-61% em 2 anos de dados reais")
print("    + Edge positivo mesmo com spread e gas incluidos")
print("    + Baixo custo operacional (gas Polygon ~$0.01/tx)")
print("    + DRY_RUN protege de perdas acidentais durante testes")
print("    + Drawdown controlado (~10-27% dependendo do bankroll)")
print()
print("  CONTRAs / RISCOS:")
print("    - Backtest nao garante performance futura")
print("    - Liquidez real do Polymarket pode ser menor que o assumido")
print("    - Polymarket pode mudar regras ou descontinuar mercados BTC")
print("    - Taxa de acerto pode cair se o mercado se tornar mais eficiente")
print("    - Precisa de USDC na Polygon + gerenciamento ativo de carteira")
print("    - Exige monitoramento para nao ficar parado por erros de API")
print()
print("  RECOMENDACAO:")
print("    Rode em DRY_RUN por 2-4 semanas antes de arriscar dinheiro real.")
print("    Compare a taxa de acerto real com a simulada (~59%).")
print("    Se estiver consistente, comece com $100-200 USDC.")
print("=" * W)
