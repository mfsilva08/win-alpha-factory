# Fase 4 — Operação: telemetria, coletor e alertas

Esta fase não existia nas specs anteriores. Ela cobre o que acontece **depois**
que um robô está operando, e é onde o laço se fecha de volta na fábrica.

Nenhuma chamada de LLM.

---

## 4.0 A separação que governa tudo aqui

São **dois sistemas**, com ciclos de vida diferentes, que nunca se falam
diretamente. Ver `ADR-INDEX.md`, ADR-009.

```
┌── FÁBRICA DE PESQUISA ──┐              ┌── ROBÔ EM PRODUÇÃO ──┐
│ roda em lotes, offline  │              │ roda no pregão        │
│ quando você manda       │              │ função congelada      │
│                         │              │                       │
│  motor de DSL           │   robo.mq5   │  OnTick               │
│  backtest · gate        │ ───────────► │  GuardOrder           │
│  agentes (API)          │              │  LogDecision          │
│  livro-razão            │ ◄─────────── │                       │
└─────────────────────────┘ telemetria   └───────────────────────┘
              ▲              .csv              │
              │                                │
              └──────── coletor.py ◄───────────┘
                        cron diário
```

O robô **não avalia** a `kill_condition`. Ele não pode: a condição é uma
agregação de 90 dias e ele só conhece o dia de hoje. Ele mede e grava.

---

## 4.1 `codegen/telemetry.py` — o schema da telemetria

O robô grava **uma linha por barra**, tenha havido ordem ou não. Sem exceção:
dias sem operação são exatamente os que permitem medir decaimento.

```python
TELEMETRY_COLUMNS = (
    "ts",                 # ISO 8601 com timezone, momento da gravação
    "bar_time",           # timestamp de fechamento da barra avaliada
    "signal",             # valor do sinal calculado
    "threshold",          # limiar em vigor
    "decision",           # NONE | OPEN_LONG | OPEN_SHORT | CLOSE | REJECTED
    "contracts",          # tamanho decidido, 0 se não operou
    "ref_price",          # último negócio no momento da decisão
    "fill_price",         # preço efetivo, vazio se não houve ordem
    "slippage_ticks",     # (fill - ref) / tick_size, com sinal
    "order_status",       # vazio | FILLED | PARTIAL | REJECTED_<motivo>
    "position",           # posição após a barra
    "daily_pnl_points",   # acumulado do dia, em pontos
    "guard_blocked",      # vazio | CUTOFF | MARGIN | GRID | DAILY_LOSS | BREAKER | DATA_GAP
    "kill_metric_name",   # nome da métrica declarada na hipótese
    "kill_metric_value",  # valor medido nesta barra, vazio se não aplicável
)
```

Formato: CSV com cabeçalho, um arquivo por pregão, nome `win_YYYYMMDD.csv`,
escrito em `MQL5/Files/telemetry/`. Append durante o dia, fechado no `OnDeinit`.

## 4.2 `catalog/metrics.py` — o que o robô sabe medir

```python
INSTRUMENTABLE_METRICS = {
    "lag_ms":          "atraso entre o movimento da referência e o do WIN, em ms",
    "spread_ticks":    "spread médio observado na barra, em ticks",
    "realized_vol":    "volatilidade realizada na janela de normalização",
    "gap_overnight":   "diferença entre abertura e fechamento anterior, em pontos",
    "minutes_to_open": "distância em minutos da abertura do pregão",
    "ref_corr":        "correlação móvel com o instrumento de referência",
    "fill_ratio":      "fração das ordens enviadas que foram preenchidas",
}

UNAVAILABLE_METRICS = {
    "book_imbalance":  "exige histórico de book; o MT5 não fornece",
    "agg_ratio":       "exige fluxo de agressão; o MT5 não fornece",
}
```

**Regra de aceite, e ela é bloqueante:**

```python
def transpile(ast, h: Hypothesis, ...) -> str:
    if h.kill_condition.metric not in INSTRUMENTABLE_METRICS:
        raise NonInstrumentableKillCondition(h.kill_condition.metric)
```

Uma condição de morte que o robô não sabe medir é uma condição de morte que
nunca vai disparar. O erro tem que aparecer na bancada, não seis meses depois.

O agente de hipótese recebe `INSTRUMENTABLE_METRICS` no prompt e a validação
determinística confere de novo na saída.

## 4.3 `ops/coletor.py` — o job diário

Roda por cron, de madrugada. Umas 60 linhas.

```python
def collect(day: date, telemetry_dir: Path, ledger: Ledger) -> CollectResult:
    """1. lê o CSV do pregão
       2. valida o schema e a contagem de barras esperada
       3. agrega as métricas do dia
       4. grava o resumo diário no livro-razão
       5. recalcula a kill_condition na janela declarada
       6. compara com o limiar e decide o estado da hipótese
       7. emite alertas"""
```

### Agregações do dia

```python
@dataclass(frozen=True)
class DailySummary:
    day:                date
    strategy_id:        str
    hypothesis_id:      str
    bars_expected:      int      # do calendário de pregão
    bars_recorded:      int      # do CSV
    trades:             int
    net_pnl_points:     float
    gross_pnl_points:   float
    slippage_ticks_avg: float    # realizado, para recalibrar o CostModel
    rejections:         int
    guard_blocks:       dict[str, int]
    kill_metric_daily:  float    # a agregação do dia, ainda não a móvel
```

### A avaliação da condição de morte

```python
def evaluate_kill(spec: KillSpec, series: list[float]) -> KillState:
    window = series[-spec.window_days:]
    if len(window) < spec.window_days:
        return KillState.INSUFFICIENT      # ainda não dá para julgar
    agg = median(window) if spec.aggregation == "mediana" else mean(window)
    crossed = agg < spec.threshold if spec.operator == "<" else agg > spec.threshold
    return KillState.DEAD if crossed else KillState.ALIVE
```

Ao cruzar, o coletor grava um registro `kind='kill'` no livro-razão com o valor
agregado e a data. Esse registro é o que o bandit lê para aprender que a família
esgotou.

**O coletor não desliga o robô sozinho.** Ele marca e alerta. O desligamento é
uma ação registrada, feita por uma pessoa. A razão é simples: um bug no coletor
não pode derrubar uma estratégia viva.

### Implementação (M8)

- `collect(day, telemetry_dir, ledger, hipótese, strategy_id, bars_expected, costs)`.
  `bars_expected` vem do calendário de pregão (`load_calendar`: CSV
  `day,bars_expected`) ou é passado direto
- O registro `kind='daily'` usa como `data_hash` o sha256 do próprio CSV do dia
- A janela da `kill_condition` conta **dias com medição**; dia sem valor da
  métrica não entra nem conta
- Coletar o mesmo dia duas vezes levanta `AlreadyCollected`: duplicaria a série
- `kind='kill'` é gravado uma vez por hipótese; os dias seguintes continuam
  alertando `DECAIMENTO` até alguém desligar
- Telemetria ausente ou fora do schema: alerta `OPERACIONAL`, nada gravado
- Guard "esperado" é só `CUTOFF`; qualquer outro bloqueio (`MARGIN`, `GRID`,
  `DAILY_LOSS`, `BREAKER`, `DATA_GAP`) é alerta `OPERACIONAL`
- Há teste que confere que o coletor não altera nenhum arquivo de telemetria e
  não contém chamada que feche posição, remova o EA ou apague arquivo

## 4.4 Os três alertas, e por que não podem ser confundidos

| Alerta | Gatilho | O que fazer |
|---|---|---|
| **Operacional** | `bars_recorded != bars_expected`, erro no log do terminal, `rejections > 0`, `guard_blocks` com valor inesperado | Consertar o código ou a infraestrutura. **Não mexer na estratégia** |
| **Divergência** | `slippage_ticks_avg` fora de ±30% do modelado; ou o sinal recalculado offline diverge do logado em mais de 1e-9 | Recalibrar o `CostModel`, ou corrigir bug de implementação. **A estratégia continua** |
| **Decaimento** | `evaluate_kill` retorna `DEAD` | **Desligar.** É o único alerta que manda parar |

Confundir os três é o erro operacional mais caro. Prejuízo **não está na lista**:
uma estratégia com Sharpe 2 passa semanas no vermelho por acaso, e isso já está
previsto na distribuição dos 28 caminhos do CPCV.

```python
class Alert(StrEnum):
    OPERACIONAL = "operacional"
    DIVERGENCIA = "divergencia"
    DECAIMENTO  = "decaimento"
```

## 4.5 O teste de paridade contínuo

Semanal, não só na implantação. O mesmo mecanismo da Fase 3, agora sobre dados
de produção:

```python
def weekly_parity(day_range, telemetry: Path, ast: Node, frame: MarketFrame):
    logged = read_signal_column(telemetry, day_range)
    recomputed = eval_vec(ast, frame.slice(day_range))
    assert max(abs(logged - recomputed)) < 1e-9
```

Divergência aqui significa que o robô que está operando **não é** o robô que foi
testado. É alerta de divergência, e é sério.

## 4.6 `reports/monitor.py`

HTML estático, um arquivo, gerado pelo coletor. Ver `ADR-008-sem-frontend.md`.
Gráficos em SVG inline (`reports/charts.py`), sem JS, CSS embutido com modo
escuro. O intervalo do CPCV entra como `cpcv_band[h.id] = (q1, q3, n_dias)` do
PnL total dos caminhos — o livro-razão não guarda métricas de backtest, então
quem implanta informa a faixa (pendência ligada ao `var_sr`, SPEC-fase-2 §2.8).

Conteúdo mínimo:

- série da `kill_metric` agregada contra o limiar declarado, ao longo do tempo
- slippage realizado contra o modelado, por semana
- PnL acumulado contra o intervalo interquartil dos 28 caminhos do CPCV
- contagem de rejeições e bloqueios de guard, por tipo
- estado atual de cada hipótese viva: `ALIVE` · `INSUFFICIENT` · `DEAD`

---

## Critérios de aceite da Fase 4

1. [ ] O robô grava uma linha por barra, inclusive em dias sem operação
2. [ ] `transpile` levanta `NonInstrumentableKillCondition` para métrica desconhecida
3. [ ] O coletor detecta CSV com contagem de barras diferente da esperada
4. [ ] `evaluate_kill` devolve `INSUFFICIENT` enquanto não há janela completa
5. [ ] O cruzamento do limiar grava `kind='kill'` no livro-razão
6. [ ] O coletor **não** desliga o robô: só marca e alerta
7. [ ] Os três alertas são tipos distintos e disparam por gatilhos distintos
8. [ ] O teste de paridade semanal roda sobre dados de produção
9. [ ] O relatório de monitoramento abre sem servidor, arquivo único
