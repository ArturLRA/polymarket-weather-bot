"""
Previsao detalhada de ganhos — plano inteligente de crescimento.
"""
import sys, random, asyncio, httpx
from datetime import datetime, timezone, timedelta
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

ESTAGIOS = [(0,5),(300,15),(800,25),(2000,40),(5000,50),(15000,75)]
NOMES    = ["Seed","Arranque","Crescimento","Escala","Cruzeiro","Maturidade"]
PROTECAO_DD = 0.20
RETOMADA_DD = 0.10

async def fetch_batch(client, s, e):
    r = await client.get("https://api.binance.com/api/v3/klines",
        params={"symbol":"BTCUSDT","interval":"5m","startTime":s,"endTime":e,"limit":1000},timeout=30)
    return r.json()

async def fetch_all():
    end_ms   = int(datetime.now(timezone.utc).timestamp()*1000)
    start_ms = end_ms - DAYS*24*3600*1000
    iv       = 5*60*1000
    batches, cur = [], start_ms
    while cur < end_ms:
        batches.append((cur, min(cur+1000*iv, end_ms)))
        cur += 1000*iv
    async with httpx.AsyncClient() as c:
        results = await asyncio.gather(*[fetch_batch(c,s,e) for s,e in batches])
    candles, seen = [], set()
    for batch in results:
        for c in batch:
            if c[0] not in seen:
                seen.add(c[0])
                candles.append({"open":float(c[1]),"high":float(c[2]),"low":float(c[3]),
                    "close":float(c[4]),"volume":float(c[5]),"time":int(c[0])})
    candles.sort(key=lambda x: x["time"])
    return candles

def get_ind(window, curr):
    vd = compute_vwap(window); rd = compute_rsi(window)
    md = compute_macd(window); hd = compute_heiken_ashi(window)
    vwap  = vd.get("vwap"); slope = vd.get("slope",0); price = curr["close"]
    return {"price":price,"vwap":vwap,"vwap_slope":slope,"rsi":rd.get("rsi",50),"rsi_slope":rd.get("slope",0),
        "macd_line":md.get("line",0),"macd_histogram_delta":md.get("histogram_delta",0),
        "ha_color":hd.get("color"),"ha_streak":hd.get("streak",0),
        "failed_vwap_reclaim":detect_failed_vwap_reclaim(window,vwap),
        "regime":detect_regime(window,price,vwap,slope)}

def get_teto(br, em_defesa):
    t = ESTAGIOS[0][1]
    for minbr, maxteto in ESTAGIOS:
        if br >= minbr: t = maxteto
    return round(t*0.5, 2) if em_defesa else float(t)

def get_estagio(br):
    idx = 0
    for i, (minbr, _) in enumerate(ESTAGIOS):
        if br >= minbr: idx = i
    return NOMES[idx]

import json, os

CACHE_FILE = "cache_records.json"

if os.path.exists(CACHE_FILE):
    print("Carregando records do cache...")
    with open(CACHE_FILE) as f:
        records = json.load(f)
    print(f"{len(records)} records carregados\n")
else:
    print("Baixando dados da Binance...")
    candles = asyncio.run(fetch_all())
    print(f"{len(candles)} candles\n")

    raw = []; skip = -1
    for i in range(WARMUP, len(candles)-1):
        if i <= skip: continue
        window = candles[i-WARMUP:i]; curr = candles[i]; nxt = candles[i+1]
        try:
            ind    = get_ind(window, curr)
            sc     = score_direction(ind)
            sc_adj = apply_time_decay(sc, 4.0, total_minutes=5.0)
            sc_adj["rsi"]    = ind["rsi"]
            sc_adj["regime"] = ind["regime"]
            dec = decide(sc_adj, 0.50, 4.0)
        except Exception:
            continue
        if dec["action"] != "ENTER": continue
        resultado = "UP" if nxt["close"] > curr["open"] else "DOWN"
        raw.append({"acertou":dec["side"]==resultado,"prob":dec["model_prob"],
                    "edge":dec["edge"],"ts":curr["time"]})
        skip = i+2

    ms_day_tmp = 86_400_000
    t0_tmp     = raw[0]["ts"]
    by_day_tmp = defaultdict(list)
    for r in raw:
        by_day_tmp[(r["ts"]-t0_tmp)//ms_day_tmp].append(r)

    records = []
    for day in sorted(by_day_tmp):
        for r in sorted(by_day_tmp[day], key=lambda x: x["edge"], reverse=True)[:MAX_BETS_DAY]:
            if random.random() <= EXEC_RATE:
                ask = 0.50 + SPREAD/2
                records.append({**r, "ganho_unit":(1-ask)/ask})

    with open(CACHE_FILE, "w") as f:
        json.dump(records, f)
    print(f"{len(records)} records gerados e salvos em cache\n")

ms_day = 86_400_000
t0     = records[0]["ts"]

def simular_completo(records, ini):
    br = ini; peak = ini; maxdd = 0.0
    em_defesa = False; n_defesas = 0
    ms30 = 30 * ms_day

    weekly  = defaultdict(lambda: {"a":0,"t":0,"br":ini,"teto":0.0})
    monthly = defaultdict(lambda: {"a":0,"t":0,"br":ini,"teto_vals":[],"lucro":0.0})
    trim    = defaultdict(lambda: {"a":0,"t":0,"br":ini,"teto_vals":[]})
    marcos  = {}

    for r in records:
        teto  = get_teto(br, em_defesa)
        p = r["prob"]; b = r["ganho_unit"]
        kelly = max((p*b-(1-p))/b, 0.0)*0.25*br
        bet   = min(kelly, teto)
        if bet < 0.05 or br < 1.0: continue

        br -= GAS
        br += bet*b if r["acertou"] else -bet
        br  = max(br, 0.0)

        if br > peak:
            peak = br
            if em_defesa and (peak-br)/peak < RETOMADA_DD:
                em_defesa = False
        dd = (peak-br)/peak*100 if peak > 0 else 0
        if dd > maxdd: maxdd = dd
        if not em_defesa and dd >= PROTECAO_DD*100:
            em_defesa = True; n_defesas += 1

        for marco in [500,1000,2000,5000,10000,25000,50000]:
            if br >= marco and marco not in marcos:
                dias = (r["ts"]-t0)//ms_day
                marcos[marco] = {"dias":dias,"br":br,
                    "data":datetime.fromtimestamp(r["ts"]/1000,tz=timezone.utc)}

        sem = (r["ts"]-t0)//(7*ms_day)
        weekly[sem]["br"] = br
        weekly[sem]["t"] += 1
        if r["acertou"]: weekly[sem]["a"] += 1
        weekly[sem]["teto"] = teto

        mes = (r["ts"]-t0)//ms30
        monthly[mes]["br"] = br
        monthly[mes]["t"] += 1
        if r["acertou"]: monthly[mes]["a"] += 1
        monthly[mes]["teto_vals"].append(teto)

        q = (r["ts"]-t0)//(90*ms_day)
        trim[q]["br"] = br
        trim[q]["t"] += 1
        if r["acertou"]: trim[q]["a"] += 1
        trim[q]["teto_vals"].append(teto)

    prev = ini
    for mes in sorted(monthly):
        monthly[mes]["lucro"] = monthly[mes]["br"] - prev
        prev = monthly[mes]["br"]

    return br, maxdd, n_defesas, weekly, monthly, trim, marcos

br_final, maxdd, n_def, weekly, monthly, trim, marcos = simular_completo(records, USD_INI)

W = 78
meses_list = sorted(monthly)
lucros = [monthly[m]["lucro"] for m in meses_list]

print("="*W)
print("  PREVISAO DETALHADA DE GANHOS — PLANO INTELIGENTE")
print(f"  Investimento: R$500 em {datetime.now().strftime('%d/%m/%Y')} | 2 anos de operacao")
print("="*W)

print()
print("  MARCOS: QUANDO VOCE ATINGE CADA VALOR")
print(f"  {'PATRIMONIO':>12} {'EM REAIS':>12} {'DATA ESTIMADA':>16} {'DIAS':>6} {'MESES':>7}")
print(f"  {'-'*12} {'-'*12} {'-'*16} {'-'*6} {'-'*7}")
hoje = datetime.now(timezone.utc)
data_ref = datetime.fromtimestamp(t0/1000, tz=timezone.utc)
for m, info in sorted(marcos.items()):
    data_real = hoje + timedelta(days=info["dias"])
    print(f"  ${m:>11,}  R${m*BRL_USD:>10,.0f}  {data_real.strftime('%d/%m/%Y'):>16}  {info['dias']:>6}  {info['dias']//30:>7}")

print()
print("-"*W)
print("  PRIMEIRAS 12 SEMANAS — PERIODO CRITICO (dia a dia do crescimento)")
print(f"  {'SEM':>4} {'PERIODO':<24} {'ESTAGIO':<13} {'TETO':>5} {'BANKROLL':>10} {'LUCRO SEM':>11} {'APOSTAS':>8}")
print(f"  {'-'*4} {'-'*24} {'-'*13} {'-'*5} {'-'*10} {'-'*11} {'-'*8}")
prev_w = USD_INI
for sem in range(min(12, max(weekly.keys())+1)):
    if sem not in weekly: continue
    w = weekly[sem]
    lw = w["br"] - prev_w
    ds = datetime.fromtimestamp(t0/1000 + sem*7*86400, tz=timezone.utc)
    de = ds + timedelta(days=6)
    periodo = f"{ds.strftime('%d/%m')} a {de.strftime('%d/%m/%y')}"
    data_real_s = hoje + timedelta(days=sem*7)
    data_real_e = data_real_s + timedelta(days=6)
    periodo_real = f"{data_real_s.strftime('%d/%m')} a {data_real_e.strftime('%d/%m/%y')}"
    est = get_estagio(w["br"])
    print(f"  {sem+1:>4} {periodo_real:<24} {est:<13} ${w['teto']:>3.0f}  ${w['br']:>8,.0f}  R${lw*BRL_USD:>+8,.0f}  {w['t']:>8}")
    prev_w = w["br"]

print()
print("-"*W)
print("  VISAO MENSAL COMPLETA:")
print(f"  {'MES':>4} {'DATA REAL':<12} {'ESTAGIO':<13} {'TETO':>5} {'BANKROLL':>11} {'LUCRO MES':>12} {'ACERTO':>7} {'BARRA'}")
print(f"  {'-'*4} {'-'*12} {'-'*13} {'-'*5} {'-'*11} {'-'*12} {'-'*7} {'-'*20}")
prev_m = USD_INI
max_lucro = max(abs(l) for l in lucros) if lucros else 1
for mes in meses_list:
    m   = monthly[mes]
    lm  = m["lucro"]
    ds  = datetime.fromtimestamp(t0/1000 + mes*30*86400, tz=timezone.utc)
    data_real = hoje + timedelta(days=mes*30)
    data_label = data_real.strftime("%b/%Y")
    tmed = sum(m["teto_vals"])/len(m["teto_vals"]) if m["teto_vals"] else 0
    est  = get_estagio(m["br"])
    wr_m = m["a"]/m["t"]*100 if m["t"] else 0
    bar_len = int(abs(lm)/max_lucro*16)
    bar = "█"*bar_len if lm >= 0 else "░"*bar_len
    print(f"  {mes+1:>4} {data_label:<12} {est:<13} ${tmed:>3.0f}  ${m['br']:>9,.0f}  R${lm*BRL_USD:>+9,.0f}  {wr_m:>6.1f}%  {bar}")
    prev_m = m["br"]

print()
print("-"*W)
print("  VISAO TRIMESTRAL:")
print(f"  {'TRIM':>5} {'PERIODO':<26} {'ESTAGIO':<13} {'TETO':>5} {'BANKROLL':>11} {'LUCRO TRIM':>12} {'ACERTO':>7}")
print(f"  {'-'*5} {'-'*26} {'-'*13} {'-'*5} {'-'*11} {'-'*12} {'-'*7}")
nomes_trim = ["1T/2024","2T/2024","3T/2024","4T/2024","1T/2025","2T/2025","3T/2025","4T/2025"]
prev_t = USD_INI
for q in sorted(trim):
    td  = trim[q]
    lt  = td["br"] - prev_t
    tmed = sum(td["teto_vals"])/len(td["teto_vals"]) if td["teto_vals"] else 0
    wr_t = td["a"]/td["t"]*100 if td["t"] else 0
    ds   = hoje + timedelta(days=q*90)
    de   = ds + timedelta(days=89)
    periodo = f"{ds.strftime('%b/%Y')} - {de.strftime('%b/%Y')}"
    est  = get_estagio(td["br"])
    label = nomes_trim[q] if q < len(nomes_trim) else f"T{q+1}"
    print(f"  {label:>5} {periodo:<26} {est:<13} ${tmed:>3.0f}  ${td['br']:>9,.0f}  R${lt*BRL_USD:>+9,.0f}  {wr_t:>6.1f}%")
    prev_t = td["br"]

print()
print("="*W)
lucros_ano1 = [monthly[m]["lucro"] for m in meses_list if m < 12]
lucros_ano2 = [monthly[m]["lucro"] for m in meses_list if m >= 12]
br_12 = monthly[11]["br"] if 11 in monthly else USD_INI

print("  RESUMO FINAL:")
print()
print(f"  {'HORIZONTE':<22} {'BANKROLL USD':>13} {'BANKROLL BRL':>13}")
print(f"  {'-'*22} {'-'*13} {'-'*13}")
checkpoints = [(3,"3 meses"),(6,"6 meses"),(9,"9 meses"),(12,"12 meses (1 ano)"),
               (15,"15 meses"),(18,"18 meses"),(21,"21 meses"),(24,"24 meses (2 anos)")]
for mes_alvo, label in checkpoints:
    if mes_alvo-1 in monthly:
        br_cp = monthly[mes_alvo-1]["br"]
        print(f"  {label:<22} ${br_cp:>11,.0f}  R${br_cp*BRL_USD:>11,.0f}")

print()
print(f"  Lucro medio ANO 1/mes : R${sum(lucros_ano1)/len(lucros_ano1)*BRL_USD:>+10,.0f}  "
      f"(variacao: R${min(lucros_ano1)*BRL_USD:+,.0f} a R${max(lucros_ano1)*BRL_USD:+,.0f})")
if lucros_ano2:
    print(f"  Lucro medio ANO 2/mes : R${sum(lucros_ano2)/len(lucros_ano2)*BRL_USD:>+10,.0f}  "
          f"(variacao: R${min(lucros_ano2)*BRL_USD:+,.0f} a R${max(lucros_ano2)*BRL_USD:+,.0f})")
print()
print(f"  Max Drawdown          : {maxdd:.1f}% (maior queda do pico antes de recuperar)")
print(f"  Ativacoes de defesa   : {n_def}x em 2 anos")
print(f"  Taxa de acerto media  : {sum(m['a'] for m in monthly.values())/sum(m['t'] for m in monthly.values())*100:.1f}%")
print("="*W)
