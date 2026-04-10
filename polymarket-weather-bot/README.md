# Polymarket Weather Bot — Manual Completo

## O que é este projeto

Um bot em Python que aposta automaticamente em mercados de temperatura diária na Polymarket, usando previsões dos modelos meteorológicos GFS e ECMWF para encontrar mercados com preços errados.

**Estratégia:** Os modelos GFS e ECMWF atualizam a cada ~6 horas. O bot compara essas previsões com os preços da Polymarket e aposta quando encontra uma diferença significativa (edge).

---

## Estrutura do Projeto

```
polymarket-weather-bot/
├── .env.example      ← Template das variáveis de ambiente
├── .env              ← Suas configurações (criar a partir do .example)
├── requirements.txt  ← Dependências Python
├── config.py         ← Carrega configurações do .env
├── scanner.py        ← Busca mercados de temperatura na Polymarket
├── weather.py        ← Puxa previsões GFS/ECMWF via Open-Meteo
├── comparator.py     ← Compara previsão vs preço → calcula edge
├── ai_decision.py    ← (Opcional) IA valida a oportunidade
├── risk.py           ← Kelly Criterion → tamanho da aposta
├── executor.py       ← Envia ordens via py-clob-client
└── bot.py            ← Loop principal
```

---

## Passo 1 — Instalar Dependências

Requisitos: Python 3.10+ instalado.

```bash
cd polymarket-weather-bot
pip install -r requirements.txt
```

---

## Passo 2 — Criar Carteira Polygon

Você precisa de uma carteira Ethereum na rede Polygon com USDC.

### Opção A: Criar via Python (recomendado para o bot)

```python
from eth_account import Account
acct = Account.create()
print(f"Endereço:     {acct.address}")
print(f"Private Key:  {acct.key.hex()}")
```

**ATENÇÃO:** Guarde a private key em local seguro. Quem tem a key tem acesso total aos fundos.

### Opção B: Usar MetaMask

1. Instale o MetaMask (extensão do Chrome)
2. Crie ou importe uma carteira
3. Adicione a rede Polygon:
   - Network Name: Polygon
   - RPC URL: https://polygon-rpc.com
   - Chain ID: 137
   - Symbol: MATIC
   - Explorer: https://polygonscan.com
4. Exporte a private key: MetaMask → ⋮ → Detalhes da conta → Exportar chave privada

### Depositar USDC na Polygon

1. Compre USDC em uma exchange (Binance, Coinbase, etc.)
2. Envie o USDC para seu endereço na **rede Polygon** (NÃO na Ethereum!)
3. Comece com $50-100 USDC para testes
4. Você também precisa de um pouco de MATIC para gas (~$1 basta)

### Depositar na Polymarket

1. Vá em polymarket.com e conecte sua carteira
2. Deposite USDC na plataforma
3. Anote seu "funder address" (o endereço que aparece no Polymarket)

---

## Passo 3 — Configurar o .env

Copie o template e edite:

```bash
cp .env.example .env
```

Edite o `.env`:

```env
# Cole sua private key (sem 0x no início)
POLYMARKET_PRIVATE_KEY=abc123...

# Seu endereço na Polymarket
POLYMARKET_FUNDER_ADDRESS=0x1234...

# Chave da API do Anthropic (opcional, para validação IA)
ANTHROPIC_API_KEY=sk-ant-...

# Capital total disponível para apostas (em USDC)
BANKROLL=100.0

# Máximo % do bankroll por aposta (5% = conservador)
MAX_BET_PERCENT=5.0

# Edge mínimo para apostar (0.05 = 5%)
MIN_EDGE=0.05

# true = apenas simula, não aposta de verdade
DRY_RUN=true

# Cidades para monitorar (mercados mais líquidos)
TARGET_CITIES=New York,London,Tokyo,Shanghai,Hong Kong,Sydney,Paris
```

---

## Passo 4 — Testar em Modo Simulação

**SEMPRE comece com DRY_RUN=true!**

```bash
# Testa apenas o scanner (busca mercados)
python scanner.py

# Testa apenas as previsões meteorológicas
python weather.py

# Testa o comparador (encontra oportunidades)
python comparator.py

# Roda o ciclo completo em simulação
python bot.py --dry-run
```

---

## Passo 5 — Rodar de Verdade

Quando estiver confiante que funciona:

1. Mude `DRY_RUN=false` no `.env`
2. Comece com bankroll pequeno ($50)
3. Execute:

```bash
# Roda uma vez
python bot.py

# Roda em loop (a cada 6h)
python bot.py --loop
```

---

## Configurações Importantes

### MIN_EDGE (edge mínimo)

Quanto maior, mais seletivo o bot:
- `0.05` (5%) → Mais apostas, menor edge médio
- `0.10` (10%) → Menos apostas, maior edge médio
- `0.15` (15%) → Muito seletivo, poucas apostas

**Recomendação:** Comece com 0.10 e ajuste baseado nos resultados.

### MAX_BET_PERCENT (% máximo por aposta)

- `5.0` → Conservador (recomendado)
- `10.0` → Moderado
- `20.0` → Agressivo (não recomendado)

### Kelly Multiplier (no risk.py)

O bot usa "Quarter Kelly" (25% do Kelly) por padrão. Isso é mais conservador e reduz variância. Você pode ajustar no `risk.py`:

- `0.25` → Quarter Kelly (conservador, recomendado)
- `0.50` → Half Kelly (moderado)
- `1.00` → Full Kelly (máximo crescimento teórico, mas alta variância)

### TARGET_CITIES

Os mercados mais líquidos de temperatura são:
- New York, London, Tokyo, Shanghai, Hong Kong (maior volume)
- Sydney, Paris, Dubai, Singapore (menos volume)

Cidades com mais volume = melhor liquidez = ordens executam mais fácil.

### Adicionar Novas Cidades

Se a Polymarket criar mercados para novas cidades:

1. Adicione ao `TARGET_CITIES` no `.env`
2. Adicione as coordenadas no `CITY_COORDS` em `config.py`:
   ```python
   "Berlin": (52.52, 13.41),
   ```

---

## Como Funciona a Estratégia

### O Edge

Os modelos GFS e ECMWF são extremamente precisos para previsões de 1-2 dias. Quando eles concordam (spread < 1°C), a previsão é muito confiável.

O bot explora o fato de que os preços na Polymarket nem sempre acompanham as atualizações dos modelos, que acontecem a cada 6 horas. Exemplo:

```
Modelo atualiza → diz 22°C para Londres amanhã
Polymarket ainda → precifica 20°C como mais provável (60%)
O outcome "22°C" está a → $0.15 (15%)
Nossa estimativa real  → 45%
Edge → 45% - 15% = +30%
Ação → COMPRAR "22°C"
```

### Kelly Criterion

O Kelly Criterion é uma fórmula matemática que calcula o tamanho ideal da aposta baseado no edge e odds. Usar Quarter Kelly (25%) significa crescimento mais lento mas muito mais seguro.

### IA (Opcional)

O módulo `ai_decision.py` envia os dados para Claude Sonnet, que analisa se a oportunidade faz sentido. Custa ~$0.003 por consulta. Se não configurar a API key, o bot pula essa etapa.

---

## Custos

| Item | Custo/mês |
|------|-----------|
| Open-Meteo API | Grátis |
| Polymarket API | Grátis |
| API Claude (opcional) | ~$1-5 |
| Gas (Polygon) | ~$0.10-1.00 |
| **Total** | **~$1-6/mês** |

---

## Dicas e Cuidados

1. **Comece SEMPRE em DRY_RUN** — rode por pelo menos 1 semana simulando antes de apostar de verdade

2. **Acompanhe os resultados** — anote as previsões do bot vs o que realmente aconteceu para calibrar

3. **Horários dos model updates** — GFS atualiza ~00h, 06h, 12h, 18h UTC. ECMWF atualiza ~00h, 12h UTC. O melhor momento para rodar o bot é logo após esses updates

4. **Não aposte em tudo** — foque nos mercados com alta confiança (modelos concordam) e edge forte (>10%)

5. **Nunca aposte o que não pode perder** — isso é especulação, não renda garantida

6. **Cuidado com a private key** — nunca compartilhe, nunca suba pro GitHub. Adicione `.env` ao `.gitignore`

7. **Limite de ordens** — a Polymarket tem rate limits na API. O bot faz poucas chamadas, mas cuidado ao testar muitas vezes

---

## Próximos Passos (Melhorias Futuras)

- [ ] Adicionar logging em arquivo para histórico de apostas
- [ ] Dashboard web para visualizar performance
- [ ] Suporte a mercados de chuva e precipitação
- [ ] Backtesting com dados históricos
- [ ] Notificações via Telegram/Discord
- [ ] Múltiplos modelos meteorológicos (ICON, NAM)
- [ ] Considerar previsões horárias (não só máxima diária)

---

## Troubleshooting

**"Nenhum mercado encontrado"**
→ A Polymarket pode não ter mercados de temperatura ativos no momento. Eles são criados diariamente, normalmente de manhã (UTC).

**"Cliente não configurado"**
→ Verifique se `POLYMARKET_PRIVATE_KEY` e `POLYMARKET_FUNDER_ADDRESS` estão corretos no `.env`.

**Erro de allowances**
→ Se usar carteira EOA (MetaMask), precisa aprovar os contratos da Polymarket primeiro. Faça isso manualmente no site.

**Erro de saldo**
→ Verifique se tem USDC suficiente depositado na Polymarket E um pouco de MATIC para gas.

**"Edge muito pequeno"**
→ O mercado pode já estar eficiente. Tente reduzir `MIN_EDGE` para 0.03 ou monitore mais cidades.
