"""
Simulacao realista — R$500 convertidos para USDC, 2 anos de operacao.
"""
import sys, asyncio, httpx, random
from datetime import datetime, timezone
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")

from crypto.price import (compute_vwap, compute_rsi, compute_macd,
                           compute_heiken_ashi, detect_failed_vwap_reclaim, detect_regime)
from crypto.decision import score_direction, apply_time_decay, decide

# ── Parametros ───────────────────────────────────────────────────
DAYS               = 730
WARMUP             = 30
CANDLES_PER_MARKET = 3

BRL_INVESTIDO      = 500.0
BRL_USD            = 5.0314          # cotacao do dia
USD_INICIAL        = round(BRL_INVESTIDO / BRL_USD, 2)

# Teto de aposta proporcional ao bankroll inicial (~5%)
MAX_BET            = round(USD_INICIAL * 0.05, 2)

SPREAD             = 0.02
GAS_FEE            = 0.01
MAX_BETS_DAY       = 15
EXEC_RATE          = 0.70
KELLY_FRAC         = 0.25

random.seed(42)

# ── Download ─────────────────────────────────────────────────────
async def fetch_batch(client, start_ms, end_ms):
    r = await client.get(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": "BTCUSDT", "interval": "5m",
                "startTime": start_ms, "endTime": end_ms, "limit": 1000},
        timeout=30
    )
    return r.json()

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

def get_ind(window, curr):
    vd = compute_vwap(window); rd = compute_rsi(window)
    md = compute_macd(window); hd = compute_heiken_ashi(window)
    vwap = vd.get("vwap"); slope = vd.get("slope", 0); price = curr["close"]
    return {
        "price": price, "vwap": vwap, "vwap_slope": slope,
        "rsi": rd.get("rsi", 50), "rsi_slope": rd.get("slope", 0),
        "macd_line": md.get("line", 0),
        "macd_histogram_delta": md.get("histogram_delta", 0),
        "ha_color": hd.get("color"), "ha_streak": hd.get("streak", 0),
        "failed_vwap_reclaim": detect_failed_vwap_reclaim(window, vwap),
        "regime": detect_regime(window, price, vwap, slope),
    }

print(f"Investimento: R${BRL_INVESTIDO:.0f} = ${USD_INICIAL:.2f} USDC (R${BRL_USD}/USD)")
print(f"Teto por aposta: ${MAX_BET:.2f} | Kelly {KELLY_FRAC:.0%} | Spread {SPREAD:.0%}")
print(f"Baixando {DAYS} dias de dados BTC...")
candles = asyncio.run(fetch_all())
print(f"Total: {len(candles)} candles\n")

# ── Coleta sinais ────────────────────────────────────────────────
raw = []
skip_until = -1
for i in range(WARMUP, len(candles) - 1):
    if i <= skip_until:
        continue
    window = candles[i - WARMUP: i]
    curr   = candles[i]; nxt = candles[i + 1]
    try:
        ind    = get_ind(window, curr)
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
    raw.append({
        "acertou": dec["side"] == resultado,
        "prob":    dec["model_prob"],
        "edge":    dec["edge"],
        "ts":      curr["time"],
    })
    skip_until = i + CANDLES_PER_MARKET - 1

# ── Aplica filtros de realismo ───────────────────────────────────
ms_day = 86_400_000
t0     = raw[0]["ts"]
by_day = defaultdict(list)
for r in raw:
    by_day[(r["ts"] - t0) // ms_day].append(r)

records = []
for day in sorted(by_day):
    sinais = sorted(by_day[day], key=lambda x: x["edge"], reverse=True)[:MAX_BETS_DAY]
    for r in sinais:
        if random.random() <= EXEC_RATE:
            ask        = 0.50 + SPREAD / 2
            ganho_unit = (1.0 - ask) / ask
            records.append({**r, "ganho_unit": ganho_unit})

# ── Simulacao com bankroll composto ─────────────────────────────
br    = USD_INICIAL
peak  = USD_INICIAL
maxdd = 0.0
ms30  = 30 * ms_day
monthly = defaultdict(lambda: {"a": 0, "t": 0, "br": 0.0, "lucro": 0.0,
                                "apostas_ganhas": 0.0, "apostas_perdidas": 0.0})
total_gas  = 0.0
seq_losses = 0
max_seq    = 0

for r in records:
    p     = r["prob"]
    b     = r["ganho_unit"]
    kelly = max((p * b - (1-p)) / b, 0.0)
    bet   = min(kelly * KELLY_FRAC * br, MAX_BET)
    if bet < 0.05 or br < 1.0:
        continue

    br        -= GAS_FEE
    total_gas += GAS_FEE

    prev_br = br
    if r["acertou"]:
        br       += bet * b
        seq_losses = 0
        monthly[(r["ts"] - t0) // ms30]["apostas_ganhas"] += bet * b
    else:
        br        -= bet
        seq_losses += 1
        monthly[(r["ts"] - t0) // ms30]["apostas_perdidas"] += bet

    br = max(br, 0.0)
    if br > peak:
        peak = br
    dd = (peak - br) / peak * 100 if peak > 0 else 0
    if dd > maxdd:
        maxdd = dd
    if seq_losses > max_seq:
        max_seq = seq_losses

    mes = (r["ts"] - t0) // ms30
    monthly[mes]["t"] += 1
    if r["acertou"]:
        monthly[mes]["a"] += 1
    monthly[mes]["br"] = br

# ── Calcula lucro mensal ─────────────────────────────────────────
meses_list = sorted(monthly)
lucros_mes = []
prev_br_m  = USD_INICIAL
for mes in meses_list:
    lm = monthly[mes]["br"] - prev_br_m
    monthly[mes]["lucro"] = lm
    lucros_mes.append(lm)
    prev_br_m = monthly[mes]["br"]

# Estatisticas mensais
lucro_medio   = sum(lucros_mes) / len(lucros_mes) if lucros_mes else 0
lucro_mediana = sorted(lucros_mes)[len(lucros_mes)//2] if lucros_mes else 0
meses_pos     = sum(1 for l in lucros_mes if l > 0)
meses_neg     = sum(1 for l in lucros_mes if l < 0)
pior_mes      = min(lucros_mes) if lucros_mes else 0
melhor_mes    = max(lucros_mes) if lucros_mes else 0

total_apostas = len(records)
acertos_total = sum(1 for r in records if r["acertou"])
wr_total      = acertos_total / total_apostas * 100 if total_apostas else 0

lucro_usd  = br - USD_INICIAL
lucro_brl  = lucro_usd * BRL_USD
final_brl  = br * BRL_USD

W = 72

print("=" * W)
print(f"  SIMULACAO REAL — R$500 investidos | 2 Anos | Cenario Medio")
print("=" * W)
print(f"  Investimento      : R${BRL_INVESTIDO:.0f}  =  ${USD_INICIAL:.2f} USDC  (R${BRL_USD}/USD)")
print(f"  Teto por aposta   : ${MAX_BET:.2f}  |  Kelly {KELLY_FRAC:.0%}  |  Spread {SPREAD:.0%}")
print(f"  Filtros           : {MAX_BETS_DAY} apostas/dia max  |  {EXEC_RATE:.0%} taxa execucao")
print(f"  Total de apostas  : {total_apostas:,} ({total_apostas/DAYS:.1f}/dia)")
print(f"  Taxa de acerto    : {wr_total:.1f}%")
print()

print("-" * W)
print("  RESULTADO FINAL")
print("-" * W)
print(f"  Bankroll final    :   ${br:>10,.2f} USDC")
print(f"  Lucro total       :   ${lucro_usd:>+10,.2f} USDC")
print(f"                    :   R${lucro_brl:>+10,.2f}")
print(f"  Valor final em BRL:   R${final_brl:>10,.2f}")
print(f"  ROI               :   {lucro_usd/USD_INICIAL*100:>+9.1f}%")
print(f"  Max Drawdown      :   {maxdd:>9.1f}%")
print(f"  Maior sequencia   :   {max_seq} apostas perdidas seguidas")
print(f"  Gas total pago    :   ${total_gas:>9.2f} USDC")
print()

print("-" * W)
print("  ESTATISTICAS MENSAIS")
print("-" * W)
print(f"  Lucro medio/mes   :   ${lucro_medio:>+9.2f} USDC  =  R${lucro_medio*BRL_USD:>+9.2f}")
print(f"  Lucro mediana/mes :   ${lucro_mediana:>+9.2f} USDC  =  R${lucro_mediana*BRL_USD:>+9.2f}")
print(f"  Melhor mes        :   ${melhor_mes:>+9.2f} USDC  =  R${melhor_mes*BRL_USD:>+9.2f}")
print(f"  Pior mes          :   ${pior_mes:>+9.2f} USDC  =  R${pior_mes*BRL_USD:>+9.2f}")
print(f"  Meses positivos   :   {meses_pos}/{len(lucros_mes)}")
print(f"  Meses negativos   :   {meses_neg}/{len(lucros_mes)}")
print()

print("-" * W)
print(f"  {'MES':<6} {'DATA':<9} {'APOST':>6} {'ACERTO':>7} {'BANKROLL USD':>13} {'LUCRO MES USD':>14} {'LUCRO MES BRL':>14}")
print(f"  {'-'*6} {'-'*9} {'-'*6} {'-'*7} {'-'*13} {'-'*14} {'-'*14}")

prev_br_m = USD_INICIAL
for mes in meses_list:
    m      = monthly[mes]
    wr_m   = m["a"] / m["t"] * 100 if m["t"] else 0
    lm_usd = m["lucro"]
    lm_brl = lm_usd * BRL_USD
    data_s = datetime.fromtimestamp(t0/1000 + mes*30*86400, tz=timezone.utc).strftime("%b/%Y")
    sinal  = "+" if lm_usd >= 0 else " "
    # barra visual
    max_abs = max(abs(l) for l in lucros_mes) if lucros_mes else 1
    bar_len = int(abs(lm_usd) / max_abs * 12)
    bar     = ("█" * bar_len) if lm_usd >= 0 else ("░" * bar_len)
    print(f"  Mes {mes+1:<2} {data_s:<9} {m['t']:>6} {wr_m:>6.1f}%  ${m['br']:>11,.2f}  ${lm_usd:>+12,.2f}  R${lm_brl:>+11,.2f}  {bar}")
    prev_br_m = m["br"]

print()
print("=" * W)
print("  RESUMO EM REAIS")
print("=" * W)
print(f"  Voce investe hoje        :  R${BRL_INVESTIDO:,.0f}")
print(f"  Apos 2 anos (cenario med):  R${final_brl:,.2f}")
print(f"  Lucro total em reais     :  R${lucro_brl:+,.2f}")
print(f"  Lucro medio por mes      :  R${lucro_medio*BRL_USD:+,.2f}")
print(f"  Lucro mediano por mes    :  R${lucro_mediana*BRL_USD:+,.2f}")
print()
print("  AVISOS IMPORTANTES:")
print("  * Cotacao BRL/USD varia — resultado final em reais pode ser")
print("    maior ou menor dependendo da variacao cambial")
print("  * Backtest nao garante resultado futuro")
print("  * Recomendado: rodar 2-4 semanas em DRY_RUN antes de investir")
print("=" * W)
