"""
Suite de testes completa — verifica todos os modulos antes de ir live.
Rode com: python tests.py
"""
import sys, os, json, time, shutil, traceback
sys.stdout.reconfigure(encoding="utf-8")

# ── Utilidades de teste ─────────────────────────────────────────
PASS = 0; FAIL = 0; WARN = 0
results = []

def ok(nome):
    global PASS; PASS += 1
    print(f"  [OK]   {nome}")
    results.append(("OK", nome))

def fail(nome, detalhe=""):
    global FAIL; FAIL += 1
    msg = f"  [FAIL] {nome}"
    if detalhe: msg += f"\n         → {detalhe}"
    print(msg)
    results.append(("FAIL", nome, detalhe))

def warn(nome, detalhe=""):
    global WARN; WARN += 1
    msg = f"  [WARN] {nome}"
    if detalhe: msg += f"\n         → {detalhe}"
    print(msg)
    results.append(("WARN", nome, detalhe))

def secao(titulo):
    print(f"\n{'─'*60}")
    print(f"  {titulo}")
    print(f"{'─'*60}")

# ── CANDLES FAKE para testes offline ───────────────────────────
def make_candles(n=100, preco_base=70000.0, trend="up"):
    candles = []
    p = preco_base
    for i in range(n):
        if trend == "up":    p += 10
        elif trend == "down": p -= 10
        else:                p += (5 if i % 2 == 0 else -4)
        vol = 1.0 + (i % 5) * 0.5
        candles.append({
            "open":   p - 5, "high": p + 10,
            "low":    p - 10, "close": p,
            "volume": vol, "time": int(time.time() * 1000) + i * 60000
        })
    return candles


# ══════════════════════════════════════════════════════════════
secao("1. MODULO DE RISCO (core/risk.py)")
# ══════════════════════════════════════════════════════════════

from core.risk import (kelly_bet_size, get_state, update_bankroll,
                        get_estagio_nome, _get_teto, _load_state, _save_state,
                        ESTAGIOS, NOMES_ESTAGIO, STATE_FILE)

# Backup do state real
STATE_BACKUP = STATE_FILE + ".bak"
if os.path.exists(STATE_FILE):
    shutil.copy(STATE_FILE, STATE_BACKUP)

try:
    # 1.1 Estágios
    assert get_estagio_nome(0)     == "Seed",        "0 deve ser Seed"
    assert get_estagio_nome(99)    == "Seed",        "99 deve ser Seed"
    assert get_estagio_nome(300)   == "Arranque",    "300 deve ser Arranque"
    assert get_estagio_nome(800)   == "Crescimento", "800 deve ser Crescimento"
    assert get_estagio_nome(2000)  == "Escala",      "2000 deve ser Escala"
    assert get_estagio_nome(5000)  == "Cruzeiro",    "5000 deve ser Cruzeiro"
    assert get_estagio_nome(15000) == "Maturidade",  "15000 deve ser Maturidade"
    ok("Estágios de crescimento mapeados corretamente")
except AssertionError as e:
    fail("Estágios de crescimento", str(e))

try:
    # 1.2 Teto normal vs defesa
    teto_normal = _get_teto(99, False)
    teto_defesa = _get_teto(99, True)
    assert teto_normal == 5.0,  f"Seed normal deve ser $5, got {teto_normal}"
    assert teto_defesa == 2.5,  f"Seed defesa deve ser $2.5, got {teto_defesa}"
    assert _get_teto(300,  False) == 15.0
    assert _get_teto(5000, False) == 50.0
    assert _get_teto(5000, True)  == 25.0
    ok("Tetos por estágio e modo defesa corretos")
except AssertionError as e:
    fail("Tetos por estágio", str(e))

try:
    # 1.3 Kelly com edge positivo
    bet = kelly_bet_size(0.65, 0.50, bankroll=100.0)
    assert 0 < bet <= 5.0, f"Aposta deve ser entre $0 e $5, got {bet}"
    ok(f"Kelly com edge positivo (prob=65%): aposta ${bet:.2f}")
except Exception as e:
    fail("Kelly com edge positivo", str(e))

try:
    # 1.4 Kelly sem edge — deve retornar 0
    bet_zero = kelly_bet_size(0.45, 0.50, bankroll=100.0)
    assert bet_zero == 0.0, f"Sem edge deve retornar 0, got {bet_zero}"
    ok("Kelly sem edge retorna $0.00")
except Exception as e:
    fail("Kelly sem edge", str(e))

try:
    # 1.5 Kelly com bankroll zerado
    bet_broke = kelly_bet_size(0.65, 0.50, bankroll=0.50)
    assert bet_broke == 0.0, f"Bankroll < $1 deve retornar 0, got {bet_broke}"
    ok("Kelly com bankroll < $1 retorna $0.00")
except Exception as e:
    fail("Kelly bankroll zerado", str(e))

try:
    # 1.6 Persistência do estado
    estado_teste = {"bankroll": 250.0, "peak": 300.0, "em_defesa": False, "n_defesas": 0}
    _save_state(estado_teste)
    carregado = _load_state()
    assert carregado["bankroll"]  == 250.0
    assert carregado["peak"]      == 300.0
    assert carregado["em_defesa"] == False
    ok("Estado do bankroll salvo e carregado corretamente")
except Exception as e:
    fail("Persistência de estado", str(e))

try:
    # 1.7 Update bankroll — ganho
    _save_state({"bankroll": 100.0, "peak": 100.0, "em_defesa": False, "n_defesas": 0})
    s = update_bankroll(+5.0)
    assert s["bankroll"] == 105.0, f"Esperado 105.0, got {s['bankroll']}"
    assert s["peak"]     == 105.0, "Pico deve subir junto"
    ok("Update bankroll após ganho: $100 + $5 = $105")
except Exception as e:
    fail("Update bankroll ganho", str(e))

try:
    # 1.8 Update bankroll — perda com ativação de defesa
    _save_state({"bankroll": 100.0, "peak": 100.0, "em_defesa": False, "n_defesas": 0})
    for _ in range(5):
        update_bankroll(-5.0)   # perde $5 cinco vezes = $75 (queda de 25% > 20%)
    s = get_state()
    assert s["em_defesa"] == True, f"Deveria estar em defesa, em_defesa={s['em_defesa']}"
    assert s["n_defesas"] >= 1,    "n_defesas deve ser >= 1"
    ok(f"Modo defesa ativado após queda de {(100-s['bankroll']):.0f}% (bankroll: ${s['bankroll']:.2f})")
except Exception as e:
    fail("Ativação do modo defesa", str(e))

try:
    # 1.9 Update bankroll — recuperação sai de defesa
    _save_state({"bankroll": 75.0, "peak": 100.0, "em_defesa": True, "n_defesas": 1})
    update_bankroll(+26.0)   # sobe para $101 > peak $100 → sai de defesa
    s = get_state()
    assert s["em_defesa"] == False, f"Deveria sair de defesa, em_defesa={s['em_defesa']}"
    ok("Modo defesa desativado após recuperação do pico")
except Exception as e:
    fail("Desativação do modo defesa", str(e))

# Restaura state real
if os.path.exists(STATE_BACKUP):
    shutil.copy(STATE_BACKUP, STATE_FILE)
    os.remove(STATE_BACKUP)


# ══════════════════════════════════════════════════════════════
secao("2. INDICADORES TÉCNICOS (crypto/price.py)")
# ══════════════════════════════════════════════════════════════

from crypto.price import (compute_vwap, compute_rsi, compute_macd,
                           compute_heiken_ashi, detect_failed_vwap_reclaim,
                           detect_regime, get_all_indicators)

candles_up   = make_candles(100, trend="up")
candles_down = make_candles(100, trend="down")
candles_sid  = make_candles(100, trend="sideways")

try:
    vd = compute_vwap(candles_up)
    assert "vwap" in vd and "slope" in vd, "VWAP deve ter campos vwap e slope"
    assert vd["vwap"] is not None, "VWAP não deve ser None com candles válidos"
    assert isinstance(vd["vwap"],  float), "VWAP deve ser float"
    assert isinstance(vd["slope"], float), "Slope deve ser float"
    ok(f"VWAP calculado: {vd['vwap']:.2f} | slope: {vd['slope']:.4f}")
except Exception as e:
    fail("compute_vwap", str(e))

try:
    vd_empty = compute_vwap([])
    assert vd_empty["vwap"] is None, "VWAP com lista vazia deve ser None"
    ok("VWAP com candles vazios retorna None (seguro)")
except Exception as e:
    fail("compute_vwap lista vazia", str(e))

try:
    rd = compute_rsi(candles_up)
    assert "rsi" in rd and "slope" in rd, "RSI deve ter campos rsi e slope"
    assert 0 <= rd["rsi"] <= 100, f"RSI deve estar entre 0-100, got {rd['rsi']}"
    ok(f"RSI calculado: {rd['rsi']:.1f} | slope: {rd['slope']:.4f}")
except Exception as e:
    fail("compute_rsi", str(e))

try:
    rd_few = compute_rsi(make_candles(5))
    assert rd_few["rsi"] == 50.0, "RSI com poucos candles deve retornar 50.0 (neutro)"
    ok("RSI com poucos candles retorna 50.0 (fallback seguro)")
except Exception as e:
    fail("compute_rsi poucos candles", str(e))

try:
    md = compute_macd(candles_up)
    for k in ["line", "signal", "histogram", "histogram_delta"]:
        assert k in md, f"MACD deve ter campo '{k}'"
        assert isinstance(md[k], float), f"MACD.{k} deve ser float"
    ok(f"MACD calculado: line={md['line']:.4f} | hist_delta={md['histogram_delta']:.4f}")
except Exception as e:
    fail("compute_macd", str(e))

try:
    hd = compute_heiken_ashi(candles_up)
    assert "color" in hd and "streak" in hd, "Heiken Ashi deve ter color e streak"
    assert hd["color"] in ("green", "red"), f"Color deve ser green/red, got {hd['color']}"
    assert hd["streak"] >= 1, f"Streak deve ser >= 1, got {hd['streak']}"
    ok(f"Heiken Ashi: color={hd['color']} | streak={hd['streak']}")
except Exception as e:
    fail("compute_heiken_ashi", str(e))

try:
    vwap_val = compute_vwap(candles_up)["vwap"]
    fvr = detect_failed_vwap_reclaim(candles_up, vwap_val)
    assert isinstance(fvr, bool), "Failed VWAP reclaim deve ser bool"
    ok(f"Failed VWAP reclaim detectado: {fvr}")
except Exception as e:
    fail("detect_failed_vwap_reclaim", str(e))

try:
    vd2  = compute_vwap(candles_up)
    reg  = detect_regime(candles_up, candles_up[-1]["close"], vd2["vwap"], vd2["slope"])
    assert reg in ("TREND_UP","TREND_DOWN","RANGE","CHOP"), f"Regime inválido: {reg}"
    ok(f"Regime detectado em tendência de alta: {reg}")
except Exception as e:
    fail("detect_regime trend_up", str(e))

try:
    reg_down = detect_regime(candles_down, candles_down[-1]["close"],
                              compute_vwap(candles_down)["vwap"],
                              compute_vwap(candles_down)["slope"])
    assert reg_down in ("TREND_UP","TREND_DOWN","RANGE","CHOP")
    ok(f"Regime detectado em tendência de baixa: {reg_down}")
except Exception as e:
    fail("detect_regime trend_down", str(e))


# ══════════════════════════════════════════════════════════════
secao("3. MOTOR DE DECISÃO (crypto/decision.py)")
# ══════════════════════════════════════════════════════════════

from crypto.decision import score_direction, apply_time_decay, decide, get_phase

try:
    # Indicadores fortes de alta
    ind_up = {
        "price": 71000, "vwap": 70000, "vwap_slope": 0.5,
        "rsi": 58, "rsi_slope": 0.3,
        "macd_line": 10, "macd_histogram_delta": 0.5,
        "ha_color": "green", "ha_streak": 3,
        "failed_vwap_reclaim": False, "regime": "TREND_UP"
    }
    sc = score_direction(ind_up)
    assert "prob_up" in sc and "prob_down" in sc
    assert sc["prob_up"] > sc["prob_down"], \
        f"Com indicadores de alta, prob_up deve ser maior. up={sc['prob_up']}, down={sc['prob_down']}"
    ok(f"Scoring UP correto: prob_up={sc['prob_up']:.2f} > prob_down={sc['prob_down']:.2f}")
except Exception as e:
    fail("score_direction UP", str(e))

try:
    # Indicadores fortes de baixa
    ind_down = {
        "price": 69000, "vwap": 70000, "vwap_slope": -0.5,
        "rsi": 42, "rsi_slope": -0.3,
        "macd_line": -10, "macd_histogram_delta": -0.5,
        "ha_color": "red", "ha_streak": 3,
        "failed_vwap_reclaim": True, "regime": "TREND_DOWN"
    }
    sc_d = score_direction(ind_down)
    assert sc_d["prob_down"] > sc_d["prob_up"], \
        f"Com indicadores de baixa, prob_down deve ser maior. down={sc_d['prob_down']}, up={sc_d['prob_up']}"
    ok(f"Scoring DOWN correto: prob_down={sc_d['prob_down']:.2f} > prob_up={sc_d['prob_up']:.2f}")
except Exception as e:
    fail("score_direction DOWN", str(e))

try:
    # Time decay — início da janela (pouco decay)
    sc_adj_early = apply_time_decay(sc, minutes_remaining=14.0, total_minutes=15.0)
    assert sc_adj_early["time_decay"] > 0.9, \
        f"Início da janela deve ter decay alto (>0.9), got {sc_adj_early['time_decay']}"
    ok(f"Time decay no início (14/15 min): {sc_adj_early['time_decay']:.2f} (quase sem decay)")
except Exception as e:
    fail("apply_time_decay início", str(e))

try:
    # Time decay — fim da janela (muito decay)
    sc_adj_late = apply_time_decay(sc, minutes_remaining=1.0, total_minutes=15.0)
    assert sc_adj_late["time_decay"] < 0.15, \
        f"Fim da janela deve ter decay baixo (<0.15), got {sc_adj_late['time_decay']}"
    ok(f"Time decay no fim (1/15 min): {sc_adj_late['time_decay']:.2f} (forte decay)")
except Exception as e:
    fail("apply_time_decay fim", str(e))

try:
    # Fases
    assert get_phase(14.0, 15.0) == "EARLY"
    assert get_phase(7.0,  15.0) == "MID"
    assert get_phase(2.0,  15.0) == "LATE"
    ok("Fases EARLY/MID/LATE calculadas corretamente")
except Exception as e:
    fail("get_phase", str(e))

try:
    # Decisão de ENTRAR com sinal forte
    sc_enter = apply_time_decay(sc, 14.0, 15.0)
    sc_enter["rsi"]    = 52
    sc_enter["regime"] = "TREND_UP"
    dec = decide(sc_enter, 0.50, 14.0)
    assert dec["action"] == "ENTER", \
        f"Com sinal STRONG deve entrar. Action={dec['action']}, reason={dec.get('reason','')}"
    assert dec["strength"] == "STRONG"
    ok(f"Decisão ENTER correta: side={dec['side']} | edge={dec['edge']:.1%} | fase={dec['phase']}")
except Exception as e:
    fail("decide ENTER", str(e))

try:
    # Bloqueio por RSI extremo
    sc_rsi = apply_time_decay(sc, 14.0, 15.0)
    sc_rsi["rsi"]    = 75
    sc_rsi["regime"] = "TREND_UP"
    dec_rsi = decide(sc_rsi, 0.50, 14.0)
    assert dec_rsi["action"] == "NO_TRADE", \
        f"RSI extremo deve bloquear. Action={dec_rsi['action']}"
    ok(f"Bloqueio por RSI extremo (75): {dec_rsi.get('reason','')}")
except Exception as e:
    fail("decide bloqueio RSI", str(e))

try:
    # Bloqueio por CHOP
    sc_chop = apply_time_decay(sc, 14.0, 15.0)
    sc_chop["rsi"]    = 50
    sc_chop["regime"] = "CHOP"
    dec_chop = decide(sc_chop, 0.50, 14.0)
    assert dec_chop["action"] == "NO_TRADE", \
        f"Regime CHOP deve bloquear. Action={dec_chop['action']}"
    ok(f"Bloqueio por regime CHOP: {dec_chop.get('reason','')}")
except Exception as e:
    fail("decide bloqueio CHOP", str(e))

try:
    # Preço inválido
    dec_inv = decide(sc_enter, 1.01, 14.0)
    assert dec_inv["action"] == "NO_TRADE"
    ok("Preço inválido (>1) bloqueado corretamente")
except Exception as e:
    fail("decide preço inválido", str(e))


# ══════════════════════════════════════════════════════════════
secao("4. SCANNER DE MERCADOS (crypto/scanner.py)")
# ══════════════════════════════════════════════════════════════

from crypto.scanner import get_active_btc_market

try:
    print("  Conectando ao Polymarket (Gamma API)...")
    market = get_active_btc_market()
    if market is None:
        warn("Scanner retornou None — sem mercado BTC ativo agora",
             "Normal fora do horário comercial (mercados de 15min podem não estar ativos)")
    else:
        for campo in ["question","yes_price","yes_token","no_token","minutes_remaining","volume"]:
            assert campo in market, f"Campo obrigatório ausente: {campo}"
        assert 0 < market["yes_price"] < 1, \
            f"Preço YES deve estar entre 0 e 1, got {market['yes_price']}"
        assert market["minutes_remaining"] > 0, "Mercado não pode estar expirado"
        ok(f"Mercado encontrado: {market['question'][:50]}...")
        ok(f"YES price: {market['yes_price']:.2f} | Volume: ${market['volume']:,.0f} | "
           f"{market['minutes_remaining']:.1f} min restantes")
except Exception as e:
    fail("get_active_btc_market", str(e))


# ══════════════════════════════════════════════════════════════
secao("5. INDICADORES AO VIVO (Binance API)")
# ══════════════════════════════════════════════════════════════

try:
    print("  Conectando à Binance...")
    indicators = get_all_indicators()
    if indicators is None:
        fail("get_all_indicators retornou None — falha na conexão com Binance")
    else:
        campos = ["price","vwap","rsi","macd_line","ha_color","regime"]
        for c in campos:
            assert c in indicators, f"Campo ausente: {c}"
        assert indicators["price"] > 0, "Preço BTC deve ser positivo"
        assert 0 <= indicators["rsi"] <= 100, "RSI deve estar entre 0-100"
        assert indicators["regime"] in ("TREND_UP","TREND_DOWN","RANGE","CHOP")
        ok(f"BTC: ${indicators['price']:,.2f} | RSI: {indicators['rsi']:.1f} | "
           f"Regime: {indicators['regime']}")
        ok(f"VWAP: {indicators['vwap']:.2f} | MACD: {indicators['macd_line']:.4f} | "
           f"HA: {indicators['ha_color']} x{indicators['ha_streak']}")
except Exception as e:
    fail("get_all_indicators (Binance ao vivo)", str(e))


# ══════════════════════════════════════════════════════════════
secao("6. LOGGER (core/logger.py)")
# ══════════════════════════════════════════════════════════════

from core.logger import log_trade, log_opportunity, _load_history, _save_history

TEST_LOG = "test_history.json"

try:
    # Salva e carrega
    test_data = [{"timestamp":"2026-01-01","market":"TEST","amount":10.0,"dry_run":True}]
    _save_history.__globals__["LOG_FILE"] = TEST_LOG
    _save_history(test_data)
    loaded = _load_history.__func__(TEST_LOG) if hasattr(_load_history, "__func__") else None

    # Acesso direto ao arquivo
    with open(TEST_LOG) as f:
        loaded = json.load(f)
    assert len(loaded) == 1
    assert loaded[0]["market"] == "TEST"
    ok("Logger: salva e carrega histórico corretamente")
    os.remove(TEST_LOG)
except Exception as e:
    fail("Logger save/load", str(e))
    if os.path.exists(TEST_LOG): os.remove(TEST_LOG)

try:
    # Arquivo inexistente retorna lista vazia
    loaded_vazio = _load_history()
    assert isinstance(loaded_vazio, list), "Deve retornar lista mesmo sem arquivo"
    ok("Logger: arquivo inexistente retorna [] sem crash")
except Exception as e:
    fail("Logger arquivo inexistente", str(e))


# ══════════════════════════════════════════════════════════════
secao("7. CICLO COMPLETO DO BOT (DRY RUN)")
# ══════════════════════════════════════════════════════════════

from crypto_bot import run_crypto_cycle

try:
    # Faz backup do state e crypto_history
    if os.path.exists(STATE_FILE):
        shutil.copy(STATE_FILE, STATE_BACKUP)
    _save_state({"bankroll": 99.38, "peak": 99.38, "em_defesa": False, "n_defesas": 0})

    print("  Executando ciclo completo em DRY RUN...")
    run_crypto_cycle()
    ok("Ciclo completo executado sem exceções")

    # Verifica se crypto_history foi criado/atualizado
    from config import CRYPTO_LOG_FILE
    if os.path.exists(CRYPTO_LOG_FILE):
        with open(CRYPTO_LOG_FILE) as f:
            hist = json.load(f)
        if hist:
            ultimo = hist[-1]
            assert "timestamp" in ultimo
            assert "market"    in ultimo
            assert "dry_run"   in ultimo
            ok(f"Registro salvo no histórico: {ultimo.get('outcome','—')} | "
               f"${ultimo.get('amount','?')} | status={ultimo.get('status','?')}")
        else:
            warn("Histórico criado mas vazio (NO_TRADE em todos os ciclos)")
    else:
        warn("crypto_history.json não criado (nenhum sinal gerado — mercado pode não estar ativo)")

except Exception as e:
    fail("Ciclo completo DRY RUN", traceback.format_exc())
finally:
    if os.path.exists(STATE_BACKUP):
        shutil.copy(STATE_BACKUP, STATE_FILE)
        os.remove(STATE_BACKUP)


# ══════════════════════════════════════════════════════════════
secao("8. INTEGRIDADE DOS ARQUIVOS DE CONFIGURAÇÃO")
# ══════════════════════════════════════════════════════════════

try:
    from config import (PRIVATE_KEY, DRY_RUN, BANKROLL, KELLY_FRACTION,
                        POLYMARKET_HOST, GAMMA_API_URL, BINANCE_BASE_URL,
                        CRYPTO_MIN_EDGE, CRYPTO_MIN_PROB)
    ok(f"config.py carregado | DRY_RUN={DRY_RUN} | BANKROLL=${BANKROLL}")
    ok(f"KELLY_FRACTION={KELLY_FRACTION} | MIN_EDGE EARLY={CRYPTO_MIN_EDGE['EARLY']:.0%}")
    if not PRIVATE_KEY:
        warn("POLY_PRIVATE_KEY não configurada — bot não vai executar apostas reais")
    else:
        ok(f"Chave privada configurada ({PRIVATE_KEY[:8]}...)")
    if DRY_RUN:
        ok("DRY_RUN=true — modo seguro ativo")
    else:
        warn("DRY_RUN=false — MODO LIVE ATIVO. Certifique-se que tem USDC na carteira!")
except Exception as e:
    fail("config.py", str(e))

try:
    state = get_state()
    assert "bankroll" in state
    assert "peak"     in state
    assert "em_defesa" in state
    ok(f"bankroll_state.json | bankroll=${state['bankroll']:.2f} | "
       f"pico=${state['peak']:.2f} | defesa={state['em_defesa']}")
except Exception as e:
    fail("bankroll_state.json", str(e))


# ══════════════════════════════════════════════════════════════
# RESULTADO FINAL
# ══════════════════════════════════════════════════════════════
print(f"\n{'═'*60}")
print(f"  RESULTADO DOS TESTES")
print(f"{'═'*60}")
print(f"  ✓ Passou   : {PASS}")
print(f"  ✗ Falhou   : {FAIL}")
print(f"  ⚠ Alertas  : {WARN}")
print(f"{'─'*60}")

if FAIL == 0 and WARN == 0:
    print("  SISTEMA PRONTO — pode ativar com segurança.")
elif FAIL == 0:
    print("  SISTEMA FUNCIONAL com alertas.")
    print("  Revise os [WARN] antes de ir para LIVE.")
else:
    print("  SISTEMA COM FALHAS — NÃO vá para LIVE.")
    print("  Corrija os [FAIL] antes de usar.")
    print()
    print("  Falhas encontradas:")
    for r in results:
        if r[0] == "FAIL":
            print(f"    → {r[1]}")
            if len(r) > 2 and r[2]: print(f"       {r[2][:100]}")
print(f"{'═'*60}")
