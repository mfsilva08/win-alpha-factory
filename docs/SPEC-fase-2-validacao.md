# Fase 2 — Validação: backtest e gate

Nenhuma chamada de LLM. Ao fim desta fase existe um sistema útil sozinho: fórmulas
escritas à mão podem ser testadas com rigor superior ao de boa parte das mesas.

---

## 2.1 `backtest/costs.py`

```python
@dataclass(frozen=True)
class CostModel:
    point_value:         float = 0.20    # R$ por ponto do WIN
    tick_size:           int   = 5       # pontos
    slippage_ticks:      int   = 1       # por perna
    exchange_fee:        float = 0.25    # taxas B3, por contrato por lado
    brokerage:           float = 0.00    # mini contratos
    margin_per_contract: float = 155.0

    def round_trip_points(self) -> int:
        return 2 * self.slippage_ticks * self.tick_size      # 10

    def fees_brl(self, contracts: int) -> float:
        return (self.exchange_fee + self.brokerage) * contracts * 2
```

Os valores vêm de `config/costs.yaml`, não hardcoded. Template em `exemplos/costs.yaml`. Valide contra uma nota de
corretagem real antes de qualquer sessão.

## 2.2 `backtest/data.py`

```python
def load(period: Period, symbols: set[str]) -> MarketFrame
def data_hash(frame: MarketFrame) -> str
def holdout_split(frame: MarketFrame, months: int = 6) -> tuple[MarketFrame, MarketFrame]
```

- Série contínua ajustada por diferença na rolagem; dias de rolagem marcados
- `MarketFrame` é imutável e carrega `session_mask` (bool por barra)
- Alinhamento entre WIN e ES: o timestamp é o de **fechamento** da barra. Escreva
  um teste que falha se o ES estiver deslocado um minuto para trás
- `holdout_split` separa os últimos N meses. O hold-out vive em outro diretório e
  nunca é lido por `load()`

## 2.3 `backtest/folds.py` — CPCV

```python
def make_paths(frame: MarketFrame, n_groups: int, k_test: int,
               label_horizon: timedelta, embargo_mult: float) -> list[Path]
```

Regras:

1. Os grupos são contíguos no tempo e quebram **sempre no fechamento do pregão**.
   Nenhum grupo atravessa o overnight
2. Combinações: `C(n_groups, k_test)`. Com N=8, k=2 são 28 caminhos
3. **Purga:** remove do treino toda amostra cujo rótulo se sobrepõe ao período de
   teste. Janela = `label_horizon`
4. **Embargo:** remove do treino `embargo_mult × label_horizon` após a fronteira
   direita de cada bloco de teste. Padrão `embargo_mult = 3.0`

**Teste de vazamento sintético:** construa um rótulo que é literalmente o preço
futuro. Um modelo trivial deve alcançar acerto perfeito **sem** purga e cair para o
acaso **com** purga. Se não cair, a purga está errada.

## 2.4 `backtest/execution.py` — simulador

```python
def simulate(signal: np.ndarray, frame: MarketFrame,
             rules: TradeRules, costs: CostModel) -> list[Trade]
```

```python
@dataclass(frozen=True)
class TradeRules:
    threshold:      float          # limiar do sinal, ex. 1.5
    contracts:      int            # 2
    max_holding:    timedelta      # fecha por tempo
    stop_points:    int | None
    target_points:  int | None
    cutoff:         time           # zeragem compulsória
    session:        tuple[time, time]
```

Modelo de preenchimento, pessimista e não negociável:

| Situação | Regra |
|---|---|
| Ordem a mercado | preenche a `referência ± 1 tick`, contra você |
| Ordem limitada | só preenche se o preço **atravessar** o nível, nunca ao tocar |
| Arredondamento | todo preço para múltiplo de `tick_size`, na direção conservadora |
| Após `cutoff` | ordem de abertura rejeitada; posição aberta é zerada a mercado |
| Fora de `session` | nenhuma ordem |
| Dia de rolagem | nenhuma ordem nova |

Registre a rejeição em `Trade.rejected` em vez de silenciar — o gate usa isso.

## 2.5 `backtest/metrics.py`

```python
@dataclass(frozen=True)
class FoldMetrics:
    path_id:          int
    n_trades:         int
    gross_pnl_points: float
    cost_points:      float
    net_pnl_points:   float
    sharpe:           float          # anualizado
    max_dd_points:    float
    hit_rate:         float
    avg_holding:      timedelta
    max_day_share:    float          # maior dia / PnL total
    skew:             float
    kurtosis:         float
```

**Devolva o vetor, nunca a média.** O agregador do gate é quem resume. Uma fórmula
com Sharpe médio 1,5 composta de metade dos caminhos em 3,0 e metade em zero é lixo,
e a média esconde isso.

Sharpe anualizado a partir de retornos por operação: use o número de pregões ativos
no caminho, não 252 fixo.

## 2.6 `backtest/runner.py` — o selo

```python
def run(ast: Node, frame: MarketFrame, cfg: BacktestConfig,
        ledger: Ledger) -> SealedResult
```

Ordem obrigatória, e não pode ser alterada:

```python
try:
    metrics = _evaluate(ast, frame, cfg)
    crashed = False
except Exception:
    metrics, crashed = None, True
finally:
    ledger.append(LedgerRecord(kind="backtest", crashed=crashed, ...))   # PRIMEIRO
return SealedResult(metrics)                                             # DEPOIS
```

Se o processo morrer entre avaliar e gravar, a tentativa se perde e o contador fica
menor que a verdade — inflando o limiar do gate na direção errada.

`SealedResult` não expõe nada além de `metrics`. Sem log, sem print, sem campo extra.

---

## 2.7 `gate/config.py`

```python
@dataclass(frozen=True)
class GateConfig:
    min_total_trades:        int   = 400
    min_trades_per_path:     int   = 30
    min_active_days:         int   = 120
    max_trade_concentration: float = 0.25
    max_sign_flip_frac:      float = 0.30
    max_pbo:                 float = 0.20
    min_dsr:                 float = 0.95

def load() -> GateConfig:
    """Lê docs/gate-prereg.md, confere o sha256 contra o registro gênesis do
    livro-razão, e levanta PreregMismatch se não bater.

    Levanta PreregIncomplete se sobrar qualquer marcador '<<< DECIDIR >>>'
    no arquivo — o template não pode ser hasheado pela metade."""
```

Sem override por variável de ambiente, sem parâmetro de função, sem exceção.

## 2.8 `gate/dsr.py`

```python
GAMMA = 0.5772156649015329   # Euler-Mascheroni

def sr_star(var_sr: float, n_trials: int) -> float:
    z1 = norm.ppf(1 - 1/n_trials)
    z2 = norm.ppf(1 - 1/(n_trials * math.e))
    return math.sqrt(var_sr) * ((1 - GAMMA) * z1 + GAMMA * z2)

def deflated_sharpe(sr: float, sr_star_: float, T: int,
                    skew: float, kurt: float) -> float:
    num = (sr - sr_star_) * math.sqrt(T - 1)
    den = math.sqrt(1 - skew * sr + (kurt - 1) / 4 * sr**2)
    return norm.cdf(num / den)
```

`n_trials` vem de `ledger.count()`. Nunca de um contador local, nunca do número de
caminhos do CPCV, nunca do número de fórmulas da hipótese atual.

`var_sr` é a variância dos Sharpes observados entre as tentativas do projeto, lida
do livro-razão.

**Confira as fórmulas contra Bailey & López de Prado (2014) antes de fechar a
implementação.** Errar um sinal aqui compromete todo o resto. A biblioteca
`purgedcv` implementa e serve de referência cruzada.

**Teste numérico:** com `var_sr=0.60` e `n_trials=2400`, `sr_star` deve dar
aproximadamente `2.71`. Com `n_trials=10`, aproximadamente `1.22`.

## 2.9 `gate/pbo.py`

Probabilidade de overfitting do backtest, a partir dos caminhos do CPCV: para cada
partição, seleciona a melhor configuração dentro da amostra e observa em que
percentil ela cai fora dela. PBO é a fração de vezes em que ela cai na metade de
baixo.

## 2.10 `gate/robustness.py`

```python
def battery(ast: Node, frame: MarketFrame, cfg) -> RobustnessResult
```

Quatro testes, **conjuntivos** — todos precisam passar:

| Teste | O que faz |
|---|---|
| `RUIDO` | perturba preços com ±1 tick, 100 reamostragens, exige mediana positiva |
| `SUBPERIODOS` | 3 fatias cronológicas, exige consistência de **sinal** |
| `CUSTOS` | reroda com custo a 1,5× e 2,0×, exige líquido positivo |
| `PARAMETROS` | desloca janelas para buckets vizinhos, exige consistência de **sinal** |

**Nunca escolha o melhor.** Escolher o melhor é otimização e custaria quatro
tentativas no livro-razão. Exigir consistência é robustez e conta como uma.

Toda a bateria conta como **uma única** tentativa, já registrada pelo backtest
original.

## 2.11 `gate/collapse.py`

```python
def collapse(m: PathMetrics, n_trials: int, cfg: GateConfig) -> Verdict:
    if m.total_trades < cfg.min_total_trades:      return Verdict.INSUFFICIENT_SAMPLE
    if m.gross_points <= m.cost_points:            return Verdict.COST_DOMINATED
    if m.sign_flip_frac > cfg.max_sign_flip_frac:  return Verdict.UNSTABLE_ACROSS_FOLDS
    if m.pbo > cfg.max_pbo:                        return Verdict.FAILED_GATE
    if dsr(m, n_trials) < cfg.min_dsr:             return Verdict.FAILED_GATE
    if not m.robustness_all_passed:                return Verdict.FAILED_GATE
    return Verdict.ACCEPTED
```

A ordem é do mais barato e mais acionável ao mais caro. `COST_DOMINATED` vem cedo
porque diz "vá para horizonte maior" sem dizer nada sobre lucro.

PBO, DSR e robustez colapsam para o mesmo `FAILED_GATE` de propósito. Granularizar
seria vazamento: saber qual falhou é informação quantitativa sobre a superfície.

---

## 2.12 O teste de calibração — o mais importante da fase

```python
def test_gate_rejects_noise():
    """Alimenta o gate com 1.000 estratégias sobre séries aleatórias.
    Menos de 5% pode receber ACCEPTED."""
    accepted = 0
    for seed in range(1000):
        frame = synthetic_frame(seed, vol=WIN_REALIZED_VOL)
        result = run(REFERENCE_AST, frame, cfg, throwaway_ledger())
        if collapse(result.metrics, n_trials=1000, cfg) is Verdict.ACCEPTED:
            accepted += 1
    assert accepted < 50
```

Se passar mais de 5%, o gate está frouxo e o projeto inteiro vai aprovar ruído sem
que ninguém perceba, porque ruído aprovado parece alfa até o dinheiro acabar.

---

## Critérios de aceite da Fase 2

1. Nenhum fold atravessa o fechamento do pregão
2. Teste de vazamento sintético detecta lookahead deliberado
3. Modelo de custo reproduz uma nota de corretagem real dentro de um centavo
4. `sr_star(0.60, 2400) ≈ 2.71` e `sr_star(0.60, 10) ≈ 1.22`
5. O gate aprova menos de 5% de 1.000 estratégias sobre ruído
6. `docs/gate-prereg.md` escrito, hasheado, e registrado no gênesis
7. `count()` cresce exatamente uma unidade por avaliação, inclusive nas que quebram
