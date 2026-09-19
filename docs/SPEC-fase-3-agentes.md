# Fase 3 — Orquestrador e agentes

Primeira fase com chamadas de LLM. A ordem interna importa: o projetor de visão e o
teste dele vêm **antes** de qualquer agente existir.

---

## 3.1 `orchestrator/state.py`

```python
class Verdict(StrEnum):
    ACCEPTED = "ACCEPTED"
    INVALID_AST = "INVALID_AST"
    TOO_COMPLEX = "TOO_COMPLEX"
    REDUNDANT = "REDUNDANT"
    PROOF_FAILED = "PROOF_FAILED"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
    UNSTABLE_ACROSS_FOLDS = "UNSTABLE_ACROSS_FOLDS"
    COST_DOMINATED = "COST_DOMINATED"
    FAILED_GATE = "FAILED_GATE"

@dataclass
class TrialState:
    trial_id:     str
    parent_id:    str | None
    hypothesis:   Hypothesis
    formula_ast:  Node | None
    proof_status: ProofStatus
    verdict:      Verdict | None
    metrics:      Metrics | None     # PRIVADO — jamais projetado à pesquisa
    attempt:      int
    budget:       Budget
    detail:       str | None         # só para INVALID_AST e PROOF_FAILED
    events:       list[Event]

class Zone(StrEnum):
    RESEARCH = "research"
    VERIFICATION = "verification"
```

## 3.2 `orchestrator/projection.py` — o componente de maior risco

```python
@dataclass(frozen=True)
class ResearchView:
    hypothesis:   Hypothesis
    formula_ast:  Node | None
    verdict:      Verdict | None
    detail:       str | None
    attempt:      int
    blocked_sigs: frozenset[str]
    constraints:  Constraints
    # NÃO existe campo de métricas nesta classe

@dataclass(frozen=True)
class VerificationView:
    formula_ast: Node
    metrics:     Metrics | None
    n_trials:    int
    cfg:         GateConfig

def project(st: TrialState, z: Zone) -> ResearchView | VerificationView
```

`detail` só é preenchido quando `verdict ∈ {INVALID_AST, PROOF_FAILED}`. Em qualquer
outro caso vale `None`.

### O primeiro teste do projeto

```python
def test_pesquisa_nunca_ve_metricas():
    st = TrialState(metrics=Metrics(sharpe=2.3141, net_points=88128.0, ...), ...)
    payload = serialize_for_api(project(st, Zone.RESEARCH))
    assert "sharpe" not in payload
    assert "2.3141" not in payload
    assert "88128" not in payload
```

O segundo e o terceiro `assert` pegam o caso difícil: métrica vazando embutida em
texto livre. O teste roda sobre o **payload final enviado à API**, não sobre o
objeto de estado.

Escreva este teste antes de escrever `project()`.

### Implementação — três camadas, cada uma suficiente sozinha

1. **Estrutura:** `ResearchView` não tem campo de métricas (há teste que confere
   a lista exata de campos)
2. **Serialização:** `serialize_for_api` aceita só `ResearchView` (`TypeError`
   para qualquer outra coisa) e escreve JSON determinístico com lista fechada de
   campos. `TrialState` nunca é serializado
3. **Varredura:** `assert_no_metrics(payload, metrics)` procura no payload final
   os nomes de métrica e os **números** das métricas privadas da tentativa
   (decimais em várias formatações; inteiros só a partir de 1.000, para não
   colidir com janelas e horários). Levanta `AssertionError` explicitamente —
   `assert` sumiria com `python -O`

`research_payload(st)` é o único caminho de `TrialState` até a API de pesquisa:
projeta, serializa e varre.

## 3.3 `orchestrator/router.py` — funções puras

```python
def route_formula(st) -> Literal["proof","retry","abandon"]:
    if st.verdict in (INVALID_AST, TOO_COMPLEX, REDUNDANT):
        return "abandon" if st.attempt >= st.budget.max_attempts else "retry"
    return "proof"                      # fórmula válida segue, mesmo na última tentativa

def route_proof(st) -> Literal["backtest","retry","abandon"]:
    if st.proof_status is ProofStatus.FAILED:
        return "abandon" if st.attempt >= st.budget.max_attempts else "retry"
    return "backtest"                   # PASSED ou SKIPPED

def route_gate(st) -> Literal["codegen","retry","abandon"]:
    if st.verdict is Verdict.ACCEPTED:     return "codegen"
    if st.verdict is Verdict.FAILED_GATE:  return "abandon"
    return "abandon" if st.attempt >= st.budget.max_attempts else "retry"
```

**Correção em relação ao esboço:** toda rota de `retry` confere o orçamento. No
esboço, `INVALID_AST`/`TOO_COMPLEX`/`REDUNDANT` devolviam `retry` antes de olhar
`attempt`, e `route_proof` nunca olhava — fórmula válida seguida de prova falha
repetia para sempre. Qualquer veredito ou estado fora do previsto levanta
`RoutingError`: é bug, não caso de negócio. `ProofStatus.SKIPPED` cobre a
verificação formal fora do escopo (ADR-006).

`FAILED_GATE` encerra a hipótese, não gera retry. Não "melhore" isso.

## 3.4 `orchestrator/budgets.py`

```python
@dataclass(frozen=True)
class Budget:
    max_attempts: int = 10          # refinamentos da mesma fórmula
    max_formulas_per_hypothesis: int = 25
    max_trials_global: int = 3_000  # bate com gate-prereg.md
    max_proof_iterations: int = 15

def check(ledger: Ledger, b: Budget) -> None:
    """Levanta BudgetExhausted se o teto global foi atingido."""
```

Retries por `INVALID_AST` e `PROOF_FAILED` não consomem tentativa global, mas
consomem `max_attempts`.

## 3.5 `orchestrator/graph.py`

```python
g = StateGraph(TrialState)
for name, fn in NODES.items():
    g.add_node(name, fn)
g.set_entry_point("hypothesis")
g.add_edge("hypothesis", "formula")
g.add_edge("backtest", "gate")
g.add_conditional_edges("formula", route_formula,
    {"proof": "proof", "retry": "formula", "abandon": END})
g.add_conditional_edges("proof", route_proof,
    {"backtest": "backtest", "retry": "formula", "abandon": END})
g.add_conditional_edges("gate", route_gate,
    {"codegen": "codegen", "retry": "formula", "abandon": END})
app = g.compile()
```

Cada nó registra um `Event` com semente aleatória, hash do payload enviado e hash da
resposta. Isso é o que permite replay determinístico.

Implementação: os nós são injetados (`Nodes`), e o grafo só conhece a topologia.
Toda chamada a modelo passa por `graph.call_agent`, que monta o payload por
`research_payload`, deriva a semente de `(trial_id, nó, tentativa)` e grava o
`Event` com a resposta. O log vai para JSONL (nunca sobrescrito). Em replay, o
`ReplayClient` serve as respostas gravadas e levanta `ReplayDiverged` se o payload
montado agora diferir do gravado — há teste com um modelo não determinístico de
propósito. `recursion_limit = 4 × max_attempts + 8`.

`Hypothesis`, `KillSpec` e `Direction` já existem em `agents/schemas.py`, com a
leitura do YAML (`load_hypothesis`), porque o estado precisa deles no M5.

---

## 3.6 `catalog/families.py`

```python
@dataclass(frozen=True)
class Family:
    name:          str
    mechanism:     str
    typical_payer: str
    data_needed:   frozenset[str]
    horizon_range: tuple[int, int]      # minutos
    allowed_ops:   frozenset[Op]
    forbidden_ops: frozenset[Op]
    session_hint:  tuple[str, str] | None
    available:     bool = True          # False quando falta o dado
```

Escrito à mão. O sistema nunca cria uma família. Comece com três; as oito completas,
com os conjuntos de operadores já definidos, estão em `catalogo-completo.md`.

Implementação: `FAMILIES` transcreve as oito; `STARTER` são as três recomendadas;
`select(nomes)` escolhe o catálogo da sessão; `catalog_sha256(famílias)` é o hash
canônico que entra no `config_hash` do gênesis. `session_hint` é `("HH:MM", "HH:MM")`.

## 3.7 `catalog/reward.py` e `bandit.py`

```python
REWARD: dict[Verdict, float] = {
    Verdict.INVALID_AST:           0.0,
    Verdict.REDUNDANT:             0.0,
    Verdict.TOO_COMPLEX:           0.1,
    Verdict.PROOF_FAILED:          0.3,
    Verdict.INSUFFICIENT_SAMPLE:   0.4,
    Verdict.COST_DOMINATED:        0.5,
    Verdict.UNSTABLE_ACROSS_FOLDS: 0.6,
    Verdict.FAILED_GATE:           0.8,
    Verdict.ACCEPTED:              1.0,
}

C_EXPLORE = 0.8

def ucb(mean_reward: float, pulls: int, total: int) -> float:
    return mean_reward + C_EXPLORE * math.sqrt(math.log(total) / pulls)

def pick(families: list[Family], ledger: Ledger) -> Family:
    """Maior UCB entre as disponíveis. Família nunca escolhida tem prioridade."""
```

`FAILED_GATE` valer 0,8 não é erro. A recompensa mede produtividade do processo, não
lucro — é a única medida que pode atravessar o firewall sem contaminá-lo. Chegar ao
gate significa que a família produziu hipóteses falseáveis, fórmulas bem-tipadas,
originais e economicamente viáveis.

Um *pull* do bandit é um registro `kind='verdict'` do livro-razão (o veredito de
cada tentativa de fórmula, com a família no payload). Empate de UCB fica com a
família que vem primeiro no catálogo.

**Nunca use Sharpe nem PnL como recompensa.** Isso derruba o firewall por dentro: a
escolha da família passaria a carregar informação de desempenho.

---

## 3.8 `agents/schemas.py` — a jaula da saída

O modelo emite via tool use com schema restrito, montado **por tentativa** a partir
das `Constraints`. O campo `op` é um `enum` contendo apenas os operadores permitidos
naquela hipótese. Emitir algo fora vira erro de schema antes de sair do modelo.

```python
@dataclass(frozen=True)
class KillSpec:
    metric:      str      # coluna que o robô sabe gravar
    aggregation: Literal["mediana","media"]
    window_days: int
    operator:    Literal["<",">"]
    threshold:   float
    unit:        str

@dataclass(frozen=True)
class Hypothesis:
    id:              str
    family:          str
    claim:           str
    who_pays:        str
    observable:      str
    direction:       Direction
    horizon:         timedelta
    session_window:  tuple[str, str]
    regime_filter:   str | None
    kill_condition:  KillSpec
    dsl_constraints: Constraints
```

`kill_condition` é objeto tipado, nunca prosa. O agente de execução levanta
`NonInstrumentableKillCondition` se `metric` não estiver em
`INSTRUMENTABLE_METRICS`. A lista está em SPEC-fase-4 §4.2, e o agente de
hipótese a recebe no prompt.

## 3.9 `agents/hypothesis.py`

Entrada: família escolhida pelo bandit, teses já exploradas na família, dados
disponíveis, exigências.

O prompt **não contém**: nenhum Sharpe, nenhuma métrica, nenhuma menção a qual
família funciona melhor, nenhum exemplo de hipótese aprovada, nenhuma instrução para
"ser criativo".

Validações determinísticas após a emissão, nesta ordem:

1. `who_pays` não vazio e não circular (rejeita se contiver apenas "o mercado",
   "os traders", "reversão", "tendência" e variações)
2. `observable` computável com `data_needed` da família
3. `kill_condition.metric` em `INSTRUMENTABLE_METRICS` — ver SPEC-fase-4 §4.2
4. `horizon` dentro de `family.horizon_range`
5. assinatura semântica não duplicada: chave composta
   `(family, observable, direction, horizon_bucket)`

Falha em qualquer uma → nova tentativa, sem consumir tentativa global.

Implementação (`agents/hypothesis.py`): o modelo propõe só texto, direção,
horizonte, janela e `kill_condition`. **Família, id e `dsl_constraints` vêm do
código** (`constraints_for`: operadores da família, `max_window` 120,
`max_depth` 4, `max_nodes` 12, `max_free_params` 3, referências exigidas = dados
da família). "Quem paga" é circular se, sem artigos e preposições, só sobram
palavras genéricas (mercado, traders, reversão, tendência, investidores…). Até 5
propostas por hipótese; a aceita vira `kind='hypothesis'` com a `semantic_key`.

## 3.10 `agents/formula.py`

Entrada: `ResearchView`. Nada além dela.

Saída: AST validada + assinatura exata + assinatura estrutural.

Efeito do veredito recebido na tentativa anterior:

| Veredito | Efeito na próxima tentativa |
|---|---|
| `INVALID_AST` | erro de schema completo volta; retry; não conta tentativa |
| `TOO_COMPLEX` | `max_depth` cai em 1 |
| `REDUNDANT` | assinatura entra em `blocked_sigs`; operador raiz banido |
| `UNSTABLE_ACROSS_FOLDS` | `max_window` cortado pela metade |
| `COST_DOMINATED` | `min_horizon` sobe um bucket |
| `PROOF_FAILED` | goal state completo volta; retry |
| `FAILED_GATE` | hipótese encerrada; não há próxima tentativa |

Implementação (`agents/formula.py`): `COST_DOMINATED` sobe o horizonte da
hipótese para o próximo bucket de Fibonacci em minutos, limitado ao máximo da
família. `REDUNDANT` vale para assinatura bloqueada na tentativa **ou** já vista
no livro-razão. Todo veredito de fórmula é gravado como `kind='verdict'`.

## 3.11 `agents/client.py`

- Uma chamada por nó, por iteração. Nunca duas
- Timeout e retry por erro de rede contam como a mesma chamada
- Todo payload enviado passa por `assert_no_metrics(payload)` antes do envio.
  Falha ali é `AssertionError`, não warning

Implementação (`agents/client.py`): SDK oficial `anthropic`, modelo padrão
`claude-opus-5`, `client.beta.messages.create` com tool use forçado
(`tool_choice: {type: "tool"}`) e a ferramenta da tentativa, `effort: high`.
**Fallback de recusa do servidor ligado por padrão** (`fallbacks="default"`,
beta `server-side-fallback-2026-07-01`): numa recusa por política, a API refaz a
requisição em outro modelo. Desligável em `ClientConfig(server_fallback=False)`.
Credenciais são do SDK (`ANTHROPIC_API_KEY` ou `ant auth login`); o código nunca
lê chave. A API não aceita semente: o replay usa as respostas gravadas.

A sessão (`orchestrator/session.py`) monta os nós reais: hipótese pronta antes do
grafo, fórmula pelo agente, prova `SKIPPED` (ADR-006), backtest pelo `runner`,
gate por `collapse` com `aggregate` e `var_sr` **injetados** — sem eles a sessão
recusa começar (`GatePending`), porque são decisões pendentes do M4.

---

## 3.12 `codegen/` — o transpilador

```python
def transpile(ast: Node, h: Hypothesis, rules: TradeRules, costs: CostModel) -> str
```

Determinístico, sem LLM. Emite `.mq5` a partir do template Jinja com:

- estado incremental derivado da AST (mesma lógica de `eval_incremental`)
- guards obrigatórios: `cutoff`, margem (`lots ≤ equity / margin_per_contract`),
  grade de 5 pontos com pós-condição, limite de perda diária, circuit breaker por
  rejeições em série
- `OnTick` sai na primeira linha quando a barra não fechou
- `g_state.Update(iClose(_Symbol, TF, 1))` — **índice 1, nunca 0**
- `LogDecision` grava por barra, tenha havido ordem ou não, incluindo
  `kill_condition.metric`

```python
def parity_check(ast: Node, mq5_log: Path, frame: MarketFrame) -> ParityResult
```

Roda a AST vetorizada sobre as mesmas barras que o EA logou e exige
`max(|A − B|) < 1e-9`. Período de teste deve incluir virada de dia e rolagem.

O pacote de implantação carrega: `.mq5`, hash da AST, hash do config do gate,
certificado de prova (se houver) e o resultado da paridade.

### Implementação (M7)

- `codegen/transpiler.py` achata a AST em pós-ordem e preenche
  `templates/robo.mq5.j2` com Jinja (`StrictUndefined`). Saída determinística,
  byte a byte. `package()` grava o `.mq5` e um `manifest.json`
- **O template espelha `eval_incremental` classe por classe** (`CRing`,
  `CMonoDeque`, `CWelford` com ressincronização, `CLag`, `CZScore`...). Para
  espelhar exatamente, a ressincronização da soma no Python passou a ser soma
  sequencial, como no MQL5
- **Guards espelham `codegen/guards.py`** — a especificação executável, usada nos
  testes de propriedade do M9
- **Índice 1, sempre.** `LoadBar` recusa `shift < 1`; referências (`ES`, `WDO`)
  precisam de barra **fechada** no mesmo minuto (`iBarShift(..., exact)` ≥ 1)
- **Referência sem barra no minuto → `DATA_GAP`:** a linha é logada, o estado não
  avança e não há ordem. A paridade ignora essas linhas e reporta a barra faltante
- **Aquecimento no `OnInit`** a partir do histórico (sem ordem e sem log). Barras
  perdidas entre ticks são processadas e logadas, mas só a última pode operar
- **Mapeamento de campos:** `volume` → `real_volume`, `trades` → `tick_volume`.
  O exportador de dados do M0 precisa usar os mesmos campos
- **`vwap` é recusado** (`NonTranspilable`): as barras M1 do MT5 não trazem VWAP
- Métrica da `kill_condition` medida no próprio robô, só a declarada:
  `spread_ticks` e `lag_ms` por tick; `realized_vol` e `ref_corr` sobre 60 barras
  fechadas; `gap_overnight`, `minutes_to_open` e `fill_ratio` por contadores
- NaN depois do aquecimento acende `g_fault`: o robô para de abrir posição e
  continua logando

**Não verificado aqui:** a compilação no MetaEditor. Os testes conferem o texto
gerado (índice 1, saída antecipada, guards, cabeçalho da telemetria, todos os
operadores, chaves e parênteses balanceados, ausência de rede). A primeira
compilação real pode revelar ajustes de sintaxe MQL5.

---

## 3.13 `reports/` — o que substitui o front

Ver `ADR-008`. Não existe frontend. A parte de monitoramento diário está em
`SPEC-fase-4-operacao.md` §4.6.

```python
def session_report(session_id: str, ledger: Ledger, out: Path) -> Path
def monitor_report(telemetry: Path, ledger: Ledger, out: Path) -> Path
```

HTML estático, arquivo único, sem servidor e sem build. Conteúdo do relatório de
sessão:

- funil: hipóteses geradas, fórmulas emitidas, avaliações gastas, aprovadas
- tabela de tentativas com veredito e assinatura estrutural
- contador do livro-razão antes e depois, e o `SR*` resultante
- para cada fórmula que chegou ao backtest: distribuição dos 28 caminhos
- scores do bandit antes e depois

Relatório de monitoramento: série da `kill_condition` contra o limiar, slippage
realizado contra o modelado, PnL acumulado contra o intervalo do CPCV.

---

## Critérios de aceite da Fase 3

1. `test_pesquisa_nunca_ve_metricas` passa, incluindo a checagem de string no payload
2. Auditoria manual de 50 payloads: nenhum contém número derivado de backtest
3. Replay determinístico: reexecutar uma tentativa a partir do log de eventos
   produz a mesma AST
4. `count()` cresce exatamente uma unidade por avaliação, e zero em retries de
   `INVALID_AST`
5. Paridade abaixo de 1e-9 em período com virada de dia e rolagem
6. Guards rejeitam: ordem após cutoff, preço fora da grade, tamanho acima da margem
7. `transpile` recusa AST cuja `kill_condition.metric` não é instrumentável
