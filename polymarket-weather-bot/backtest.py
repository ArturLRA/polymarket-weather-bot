"""
Backtest do CryptoBot — versao rapida com otimizacao de filtros.

Uso:
    python backtest.py           # ultimos 7 dias
    python backtest.py --days 30 # ultimos 30 dias
"""
import sys
import httpx
import argparse
import asyncio
from datetime import datetime, timezone
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")

from crypto.price import (compute_vwap, compute_rsi, compute_macd,
                           compute_heiken_ashi, detect_failed_vwap_reclaim, detect_regime)
from crypto.decision import score_direction, apply_time_decay, decide

SYMBOL   = "BTCUSDT"
INTERVAL = "5m"   # troque para "15m" para testar mercados de 15min
WARMUP   = 30


async def fetch_batch(client, start_ms, end_ms):
    resp = await client.get(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": SYMBOL, "interval": INTERVAL,
                "startTime": start_ms, "endTime": end_ms, "limit": 1000},
        timeout=30
    )
    return resp.json()


async def fetch_all_candles(days: int) -> list:
    end_ms      = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms    = end_ms - days * 24 * 3600 * 1000
    interval_ms = 5 * 60 * 1000
    batches, cur = [], start_ms
    while cur < end_ms:
        batches.append((cur, min(cur + 1000 * interval_ms, end_ms)))
        cur += 1000 * interval_ms

    print(f"Baixando {days} dias ({len(batches)} requisicoes em paralelo)...")
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[fetch_batch(client, s, e) for s, e in batches])

    candles, seen = [], set()
    for batch in results:
        for c in batch:
            if c[0] not in seen:
                seen.add(c[0])
                candles.append({"open": float(c[1]), "high": float(c[2]),
                                 "low":  float(c[3]), "close": float(c[4]),
                                 "volume": float(c[5]), "time": int(c[0])})
    candles.sort(key=lambda x: x["time"])
    print(f"Total: {len(candles)} candles!\n")
    return candles


def compute_indicators(window, curr):
    vwap_d = compute_vwap(window)
    rsi_d  = compute_rsi(window)
    macd_d = compute_macd(window)
    ha_d   = compute_heiken_ashi(window)
    vwap   = vwap_d.get("vwap")
    slope  = vwap_d.get("slope", 0)
    price  = curr["close"]
    return {
        "price":                price,
        "vwap":                 vwap,
        "vwap_slope":           slope,
        "rsi":                  rsi_d.get("rsi", 50),
        "rsi_slope":            rsi_d.get("slope", 0),
        "macd_line":            macd_d.get("line", 0),
        "macd_histogram_delta": macd_d.get("histogram_delta", 0),
        "ha_color":             ha_d.get("color"),
        "ha_streak":            ha_d.get("streak", 0),
        "failed_vwap_reclaim":  detect_failed_vwap_reclaim(window, vwap),
        "regime":               detect_regime(window, price, vwap, slope),
    }


def pnl(acertos, erros, amt=10.0, price=0.5):
    g = acertos * amt * (1 - price) / price
    p = erros * amt
    return g - p


def show(label, a, e, extra=""):
    t = a + e
    if t == 0:
        return
    taxa = a / t * 100
    liq  = pnl(a, e)
    roi  = liq / (e * 10) * 100 if e > 0 else 0
    mark = " <--" if taxa > 52 and t >= 50 else ""
    print(f"  {label:<35} {a:>4}/{t:<5} ({taxa:4.1f}%) | ${liq:>+8.2f} | ROI {roi:>+5.1f}%{mark}")


def run_backtest(days: int):
    candles = asyncio.run(fetch_all_candles(days))

    # Coleta todos os dados para analise multi-filtro
    records = []
    for i in range(WARMUP, len(candles) - 1):
        window = candles[i - WARMUP: i]
        curr   = candles[i]
        nxt    = candles[i + 1]
        try:
            ind        = compute_indicators(window, curr)
            scores     = score_direction(ind)
            scores_adj = apply_time_decay(scores, 4.0, total_minutes=5.0)
            scores_adj["rsi"]    = ind.get("rsi", 50)
            scores_adj["regime"] = ind.get("regime", "")
            decision   = decide(scores_adj, 0.50, 4.0)
        except Exception:
            continue

        if decision["action"] != "ENTER":
            continue

        resultado = "UP" if nxt["close"] > curr["open"] else "DOWN"
        acertou   = decision["side"] == resultado

        records.append({
            "acertou":  acertou,
            "strength": decision["strength"],
            "regime":   ind["regime"],
            "rsi":      ind["rsi"],
            "side":     decision["side"],
            "edge":     decision["edge"],
        })

    if not records:
        print("Nenhum sinal gerado.")
        return

    total = len(records)
    print(f"Total de sinais: {total}\n")
    print("=" * 70)
    print(f"  {'FILTRO':<35} {'ACERTOS':>10}   {'P&L':>9}   ROI")
    print("=" * 70)

    # ── Sem filtro (baseline) ─────────────────────────────────
    a = sum(1 for r in records if r["acertou"])
    show("SEM FILTRO (baseline)", a, total - a)

    # ── Por forca ─────────────────────────────────────────────
    print()
    for st in ["STRONG", "GOOD", "OPTIONAL"]:
        sub = [r for r in records if r["strength"] == st]
        a = sum(1 for r in sub if r["acertou"])
        show(f"Apenas {st}", a, len(sub) - a)

    # ── STRONG por regime ─────────────────────────────────────
    print()
    strong = [r for r in records if r["strength"] == "STRONG"]
    for reg in ["TREND_UP", "TREND_DOWN", "RANGE", "CHOP"]:
        sub = [r for r in strong if r["regime"] == reg]
        a = sum(1 for r in sub if r["acertou"])
        show(f"STRONG + {reg}", a, len(sub) - a)

    # ── STRONG por direcao ────────────────────────────────────
    print()
    for side in ["UP", "DOWN"]:
        sub = [r for r in strong if r["side"] == side]
        a = sum(1 for r in sub if r["acertou"])
        show(f"STRONG + {side}", a, len(sub) - a)

    # ── STRONG + RSI extremo ──────────────────────────────────
    print()
    for rsi_min, rsi_max, label in [
        (60, 100, "STRONG + RSI > 60"),
        (0,  40,  "STRONG + RSI < 40"),
        (40,  60, "STRONG + RSI 40-60 (neutro)"),
    ]:
        sub = [r for r in strong if rsi_min <= r["rsi"] <= rsi_max]
        a = sum(1 for r in sub if r["acertou"])
        show(label, a, len(sub) - a)

    # ── STRONG + edge alto ────────────────────────────────────
    print()
    for min_edge in [0.25, 0.30, 0.35, 0.40]:
        sub = [r for r in strong if r["edge"] >= min_edge]
        a = sum(1 for r in sub if r["acertou"])
        show(f"STRONG + edge >= {min_edge:.0%}", a, len(sub) - a)

    # ── Melhor combinacao ─────────────────────────────────────
    print()
    print("  Combinacoes:")
    combos = [
        ("STRONG + TREND", lambda r: r["strength"] == "STRONG" and "TREND" in r["regime"]),
        ("STRONG + TREND + edge>=30%", lambda r: r["strength"] == "STRONG" and "TREND" in r["regime"] and r["edge"] >= 0.30),
        ("STRONG + nao CHOP", lambda r: r["strength"] == "STRONG" and r["regime"] != "CHOP"),
        ("STRONG + nao CHOP + edge>=25%", lambda r: r["strength"] == "STRONG" and r["regime"] != "CHOP" and r["edge"] >= 0.25),
        ("STRONG + RSI extremo + TREND", lambda r: r["strength"] == "STRONG" and "TREND" in r["regime"] and (r["rsi"] > 60 or r["rsi"] < 40)),
    ]
    for label, fn in combos:
        sub = [r for r in records if fn(r)]
        a = sum(1 for r in sub if r["acertou"])
        show(label, a, len(sub) - a)

    print("=" * 70)
    print("  '<--' = taxa > 52% com pelo menos 50 apostas")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    run_backtest(args.days)
