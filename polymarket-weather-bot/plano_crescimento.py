"""
Plano inteligente de crescimento de apostas — 2 anos com dados reais.
"""
import sys, random, asyncio, httpx
from datetime import datetime, timezone
from collections import defaultdict
sys.stdout.reconfigure(encoding="utf-8")

from crypto.price import (compute_vwap, compute_rsi, compute_macd,
                           compute_heiken_ashi, detect_failed_vwap_reclaim, detect_regime)
from crypto.decision import score_direction, apply_time_decay, decide

DAYS         = 730
WARMUP       = 30
random.seed(42)
BRL_USD      = 5.0314
USD_INI      = round(500 / BRL_USD, 2)
SPREAD       = 0.02
GAS          = 0.01
MAX_BETS_DAY = 15
EXEC_RATE    = 0.70

# ── Estagios: (bankroll_minimo_usd, teto_aposta_usd) ─────────────
ESTAGIOS = [
    (0,      5),
    (300,   15),
    (800,   25),
    (2000,  40),
    (5000,  50),
    (15000, 75),
]
NOMES = ["Seed", "Arranque", "Crescimento", "Escala", "Cruzeiro", "Maturidade"]

PROTECAO_DD = 0.20   # ativa modo defesa se cair 20% do pico
RETOMADA_DD = 0.10   # desativa quando recuperar 10% do pico

# ── Download ─────────────────────────────────────────────────────
async def fetch_batch(client, s, e):
    r = await client.get(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": "BTCUSDT", "interval": "5m",
                "startTime": s, "endTime": e, "limit": 1000},
        timeout=30
    )
    return r.json()

async def fetch_all():
    end_ms   = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - DAYS * 24 * 3600 * 1000
    iv       = 5 * 60 * 1000
    batches, cur = [], start_ms
    while cur < end_ms:
        batches.append((cur, min(cur + 1000 * iv, end_ms)))
        cur += 1000 * iv
    async with httpx.AsyncClient() as c:
        results = await asyncio.gather(*[fetch_batch(c, s, e) for s, e in batches])
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

import json as _json, os as _os
print("Baixando dados...")
candles = asyncio.run(fetch_all())
print(f"{len(candles)} candles\n")
_CACHE = "cache_candles.json"

# ── Coleta sinais ────────────────────────────────────────────────
raw = []; skip = -1
for i in range(WARMUP, len(candles) - 1):
    if i <= skip:
        continue
    window = candles[i - WARMUP: i]; curr = candles[i]; nxt = candles[i + 1]
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
    skip = i + 2

ms_day = 86_400_000
t0     = raw[0]["ts"]
by_day = defaultdict(list)
for r in raw:
    by_day[(r["ts"] - t0) // ms_day].append(r)

records = []
for day in sorted(by_day):
    for r in sorted(by_day[day], key=lambda x: x["edge"], reverse=True)[:MAX_BETS_DAY]:
        if random.random() <= EXEC_RATE:
            ask = 0.50 + SPREAD / 2
            records.append({**r, "ganho_unit": (1 - ask) / ask})

with open("cache_records.json", "w") as _f:
    _json.dump(records, _f)
print(f"Cache salvo: {len(records)} records\n")

# ── Plano inteligente ────────────────────────────────────────────
def get_teto(br, em_defesa):
    teto = ESTAGIOS[0][1]
    for minbr, maxteto in ESTAGIOS:
        if br >= minbr:
            teto = maxteto
    return round(teto * 0.5, 2) if em_defesa else float(teto)

def get_nome_estagio(br):
    nome = NOMES[0]
    for i, (minbr, _) in enumerate(ESTAGIOS):
        if br >= minbr:
            nome = NOMES[i]
    return nome

def simular_plano(records, ini):
    br = ini; peak = ini; maxdd = 0.0
    ms30 = 30 * ms_day
    monthly = defaultdict(lambda: {
        "a": 0, "t": 0, "br": 0.0, "teto_vals": [], "defesa_count": 0
    })
    em_defesa     = False
    defesa_count  = 0
    lucro_acum    = 0.0

    for r in records:
        teto = get_teto(br, em_defesa)
        p = r["prob"]; b = r["ganho_unit"]
        kelly = max((p * b - (1 - p)) / b, 0.0) * 0.25 * br
        bet   = min(kelly, teto)
        if bet < 0.05 or br < 1.0:
            continue

        br -= GAS
        if r["acertou"]:
            br += bet * b
        else:
            br -= bet
        br = max(br, 0.0)

        if br > peak:
            peak = br
            if em_defesa and (peak - br) / peak < RETOMADA_DD:
                em_defesa = False

        dd = (peak - br) / peak * 100 if peak > 0 else 0
        if dd > maxdd:
            maxdd = dd
        if not em_defesa and dd >= PROTECAO_DD * 100:
            em_defesa = True
            defesa_count += 1

        mes = (r["ts"] - t0) // ms30
        monthly[mes]["t"] += 1
        if r["acertou"]:
            monthly[mes]["a"] += 1
        monthly[mes]["br"] = br
        monthly[mes]["teto_vals"].append(teto)
        if em_defesa:
            monthly[mes]["defesa_count"] += 1

    return br, maxdd, monthly, defesa_count

def simular_fixo(records, ini, teto_fixo):
    br = ini; peak = ini; maxdd = 0.0
    ms30 = 30 * ms_day
    monthly = defaultdict(lambda: {"a": 0, "t": 0, "br": 0.0})
    for r in records:
        p = r["prob"]; b = r["ganho_unit"]
        kelly = max((p * b - (1 - p)) / b, 0.0) * 0.25 * br
        bet   = min(kelly, teto_fixo)
        if bet < 0.05 or br < 1.0:
            continue
        br -= GAS
        br += bet * b if r["acertou"] else -bet
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
    return br, maxdd, monthly

def lucro_mensal_medio(mo, ini):
    meses = sorted(mo); prev = ini; lms = []
    for m in meses:
        lms.append(mo[m]["br"] - prev)
        prev = mo[m]["br"]
    return sum(lms) / len(lms) if lms else 0

br_plano, dd_plano, mo_plano, n_defesas = simular_plano(records, USD_INI)
br_f5,    dd_f5,    mo_f5    = simular_fixo(records, USD_INI, 5)
br_f20,   dd_f20,   mo_f20   = simular_fixo(records, USD_INI, 20)
br_f50,   dd_f50,   mo_f50   = simular_fixo(records, USD_INI, 50)

W = 76

print("=" * W)
print("  PLANO INTELIGENTE DE CRESCIMENTO — 2 ANOS")
print(f"  Investimento: R$500 = ${USD_INI:.2f} USDC | Cenario medio realista")
print("=" * W)

print()
print("  OS 6 ESTAGIOS DO PLANO:")
print(f"  {'ESTAGIO':<16} {'BANKROLL (BRL)':<20} {'MAX APOSTA':>11} {'EM DEFESA':>11}")
print(f"  {'-'*16} {'-'*20} {'-'*11} {'-'*11}")
for i, (minbr, teto) in enumerate(ESTAGIOS):
    prox_br = ESTAGIOS[i + 1][0] * BRL_USD if i + 1 < len(ESTAGIOS) else None
    faixa = f"R${minbr*BRL_USD:,.0f} – R${prox_br:,.0f}" if prox_br else f"R${minbr*BRL_USD:,.0f}+"
    defesa = f"${teto*0.5:.0f}"
    print(f"  {NOMES[i]:<16} {faixa:<20} ${teto:>10}  {defesa:>11}")

print()
print("  REGRA DE PROTECAO AUTOMATICA:")
print("  → Bankroll cai 20% do pico  =  teto reduz 50% automaticamente")
print("  → Bankroll recupera 10%     =  volta ao estagio normal")

print()
print("-" * W)
print("  COMPARACAO FINAL:")
print(f"  {'ESTRATEGIA':<32} {'FINAL':>10} {'FINAL BRL':>12} {'MED/MES':>11} {'MAX DD':>8}")
print(f"  {'-'*32} {'-'*10} {'-'*12} {'-'*11} {'-'*8}")
for label, br, dd, mo in [
    ("Teto fixo $5  (conservador)",  br_f5,    dd_f5,    mo_f5),
    ("Teto fixo $20",                br_f20,   dd_f20,   mo_f20),
    ("Teto fixo $50 (agressivo)",    br_f50,   dd_f50,   mo_f50),
    (">>> PLANO INTELIGENTE <<<",    br_plano, dd_plano, mo_plano),
]:
    lm  = lucro_mensal_medio(mo, USD_INI) * BRL_USD
    star = " ◄" if "PLANO" in label else ""
    print(f"  {label:<32} ${br:>8,.0f}  R${br*BRL_USD:>9,.0f}  R${lm:>+8,.0f}  {dd:>7.1f}%{star}")

print()
print("-" * W)
print("  EVOLUCAO MES A MES — PLANO INTELIGENTE:")
print(f"  {'MES':<6} {'DATA':<9} {'ESTAGIO':<13} {'TETO':>5} {'BANKROLL USD':>13} {'LUCRO BRL':>11} {'DEFESA':>7}")
print(f"  {'-'*6} {'-'*9} {'-'*13} {'-'*5} {'-'*13} {'-'*11} {'-'*7}")
prev = USD_INI
for mes in sorted(mo_plano):
    m    = mo_plano[mes]
    lm   = m["br"] - prev
    data = datetime.fromtimestamp(t0 / 1000 + mes * 30 * 86400, tz=timezone.utc).strftime("%b/%Y")
    tmed = sum(m["teto_vals"]) / len(m["teto_vals"]) if m["teto_vals"] else 0
    est  = get_nome_estagio(m["br"])
    def_flag = "⚠" if m["defesa_count"] > m["t"] * 0.3 else ""
    print(f"  Mes {mes+1:<2} {data:<9} {est:<13} ${tmed:>3.0f}  ${m['br']:>11,.0f}  R${lm*BRL_USD:>+8,.0f}  {def_flag:>7}")
    prev = m["br"]

print()
print("=" * W)
print("  RESULTADO FINAL:")
print(f"  R$500 investidos hoje")
print(f"  Apos 2 anos com o plano: R${br_plano*BRL_USD:,.0f}  (${br_plano:,.0f} USDC)")
print(f"  Lucro total            : R${(br_plano-USD_INI)*BRL_USD:+,.0f}")
lm_med = lucro_mensal_medio(mo_plano, USD_INI) * BRL_USD
print(f"  Lucro medio por mes    : R${lm_med:+,.0f}")
print(f"  Max Drawdown           : {dd_plano:.1f}%  (vs {dd_f50:.0f}% no teto fixo $50)")
print(f"  Ativacoes de defesa    : {n_defesas}x em 2 anos")
print()
print("  GANHO vs OUTRAS ESTRATEGIAS:")
print(f"  +R${(br_plano-br_f5)*BRL_USD:,.0f} a mais que teto fixo $5")
print(f"  +R${(br_plano-br_f20)*BRL_USD:,.0f} a mais que teto fixo $20")
print(f"  Com {dd_f50-dd_plano:.0f}pp menos de drawdown que teto fixo $50")
print("=" * W)
