# Plataforma multiagente de descoberta e validação de fórmulas para robô de mini índice (WIN)

**Documento de arquitetura e plano de desenvolvimento**
Versão 0.1 — setembro de 2026
Mercado-alvo: contrato futuro de mini índice Bovespa (WIN), B3
Plataforma de execução: MetaTrader 5 (corretora Clear)

---

## Sumário executivo

Este documento descreve a arquitetura de um sistema multiagente para **gerar, verificar e validar fórmulas de sinal** para um robô de trading operando mini índice na B3, e o plano de desenvolvimento correspondente.

A tese central do projeto é contraintuitiva e precisa ficar explícita desde a primeira linha:

> **O gargalo de um sistema automatizado de descoberta de estratégias não é a geração de candidatos. É a validação.** Um loop de LLM capaz de produzir mil fórmulas por dia, acoplado a um backtest comum, não descobre alfa — ele minera ruído com eficiência industrial. Todo o valor da arquitetura está em tornar a validação rigorosa o suficiente para que a geração em escala seja segura.

Consequentemente, a arquitetura é organizada em torno de três invariantes de projeto, e não em torno dos agentes:

1. **Firewall de informação** — os componentes que geram fórmulas nunca observam métricas de desempenho. Apenas vereditos categóricos atravessam a fronteira.
2. **Livro-razão global** — toda avaliação de backtest é registrada em um log append-only encadeado por hash. O contador desse log é o insumo do teste estatístico final.
3. **Pré-registro** — os limiares de aceitação e a condição de morte de cada hipótese são escritos antes da primeira avaliação e hasheados junto ao experimento.

Sem essas três, o sistema produz resultados impressionantes e falsos. Com elas, produz poucos candidatos e a maior parte deles sobrevive fora da amostra.

---

## 1. Contexto e justificativa

### 1.1 O problema estatístico

O ponto de partida é o trabalho de Bailey e López de Prado sobre overfitting de backtest. A observação empírica que motiva toda a arquitetura: com liberdade suficiente de busca, é trivial encontrar uma configuração de estratégia com qualquer Sharpe desejado sobre uma série histórica, e essa mesma configuração tende a colapsar para Sharpe zero quando aplicada a uma segunda série de comprimento similar.

O número de tentativas é a variável que controla isso. Um pesquisador humano testa talvez algumas centenas de configurações ao longo de meses e já está em terreno perigoso. Um loop de agentes testa esse volume em uma tarde. A inflação de desempenho por seleção cresce com o número de tentativas, e o corte de significância precisa crescer junto.

Harvey, Liu e Zhu, no contexto do "factor zoo" de fatores de equity, argumentam que um fator novo deveria enfrentar um corte de estatística t na ordem de 3,0 em vez do convencional 2,0, precisamente porque centenas de fatores já foram testados antes dele. O mesmo raciocínio se aplica, com força ainda maior, a um sistema que testa milhares.

### 1.2 A assimetria que define o desenho

Existe uma literatura madura de sistemas multiagente para **prova de teoremas** (Lean 4, Mathlib), e ela é frequentemente citada como modelo para descoberta automatizada em outros domínios. A transposição direta é um erro, e entender por quê é o que orienta as decisões de arquitetura deste projeto.

| | Prova de teorema | Descoberta de fórmula de trading |
|---|---|---|
| Verificador | Compilador Lean | Backtest |
| Soundness | Perfeita | Nenhuma |
| Falso positivo | Impossível | Frequente |
| Custo de iterar | Só compute | Compute **+ validade estatística** |
| Feedback seguro | Goal state completo | Apenas veredito categórico |
| Iterações típicas | 50 por teorema | 10 por fórmula, com teto global |

Em prova de teorema, o verificador é *sound*: se ele diz "provado", está provado. Isso permite que o sistema itere agressivamente — o AxProverBase da Axiomatic AI usa 50 iterações por teorema por padrão, devolvendo o goal state completo a cada falha, e isso é irrepreensível.

Em trading, o backtest é um estimador ruidoso com razão sinal-ruído baixíssima. Devolver métricas ao gerador transforma o loop de refinamento em descida de gradiente sobre ruído histórico. **Copiar a arquitetura do provador sem reconhecer essa assimetria é o modo de falha mais provável deste projeto.**

### 1.3 Por que o mini índice em particular

O WIN impõe restrições que tornam o desenho mais estrito, não menos:

- **Tick de 5 pontos, R$0,20 por ponto.** O tick mínimo custa R$1,00 por contrato. O spread na maior parte do pregão é de 1 tick.
- **Custo de round trip é constante em pontos, independente do horizonte.** Com 2 ticks de slippage mais emolumentos, o piso de custo fica na casa de 10 a 15 pontos. Uma estratégia com alvo de 15 pontos entrega 40% a 100% do alfa bruto em custo; uma com alvo de 200 pontos entrega 5% a 8%.
- **Adversário assimétrico por horizonte.** Em escala de segundos, o contraparte é HFT colocado no datacenter da B3. Em escala de minutos, é fluxo discricionário e robôs retail.
- **O MetaTrader 5 não simula book no Strategy Tester.** `MarketBookAdd()` e `OnBookEvent()` funcionam ao vivo, mas não há histórico de book nem replay. Qualquer fórmula que dependa de imbalance de ofertas ou posição na fila é **impossível de backtestar** nessa stack.

**Decisão de escopo**: o sistema mira swing intradiário (holding de 2 a 60 minutos, alvos de 50 a 400 pontos), não scalp. A justificativa é que scalp exige dados que a plataforma não fornece historicamente e latência que a infraestrutura retail não entrega. Essa decisão está registrada como ADR-001 na seção 11.

---

## 2. Princípios de arquitetura

Os cinco princípios abaixo têm precedência sobre qualquer conveniência de implementação. Cada um existe para neutralizar um modo de falha específico e observado.

### P1 — Firewall de informação

**Regra:** componentes da zona de pesquisa (agentes de hipótese e de fórmula) nunca recebem valores numéricos derivados do backtest.

**Mecanismo:** uma função de projeção no orquestrador constrói a visão de cada zona. A visão de pesquisa não contém o campo de métricas — não é uma instrução de prompt pedindo para ignorar, é ausência física do dado no contexto enviado à API.

**O que atravessa:** um enum fechado de vereditos (seção 4.6). Nenhum deles carrega número.

**Exceções autorizadas:** `INVALID_AST` e `PROOF_FAILED` podem devolver detalhe completo (mensagem do parser, goal state do Lean). São verdades sintáticas e lógicas, não estatísticas, e não informam sobre a superfície de desempenho.

**Modo de falha que isso neutraliza:** o agente de fórmula fazendo otimização sobre o histórico, disfarçada de exploração.

### P2 — Determinismo no caminho de verificação

**Regra:** os agentes de backtest e de execução não fazem uma única chamada de LLM.

**Justificativa dupla:** (a) qualquer LLM que observe métricas carrega essa informação e pode reemiti-la depois, furando P1; (b) o mesmo hash de AST, de dados e de configuração precisa produzir bit a bit o mesmo resultado, ou o replay e a auditoria do livro-razão são impossíveis.

**Corolário:** gerar código MQL5 a partir de uma AST tipada é **compilação**, não geração. Um LLM ali introduz erro onde não havia nenhum.

### P3 — O orquestrador não é um agente

**Regra:** o roteamento entre nós do grafo é código Python determinístico, não um LLM supervisor.

**Justificativa:** um supervisor LLM não é reproduzível (rodar duas vezes produz caminhos diferentes), é vetor de vazamento se lê métricas para rotear, e custa caro para fazer o trabalho de um `if`.

### P4 — Contabilidade irrecusável de tentativas

**Regra:** toda avaliação de backtest grava no livro-razão **antes** de retornar, inclusive quando a avaliação lança exceção. Registros são append-only e encadeados por hash do registro anterior.

**Justificativa:** o contador global é o insumo do Deflated Sharpe Ratio. Um contador subestimado infla o DSR na direção errada, produzindo mais confiança do que os dados suportam. O encadeamento por hash não protege contra fraude — protege contra autoengano, que é o risco real.

**Corolário:** varredura de parâmetros dentro do agente de backtest é proibida. Um `for window in range(10, 100)` interno registra 90 tentativas como uma.

### P5 — Pré-registro

**Regra:** os limiares do gate e a condição de morte de cada hipótese são escritos antes da primeira fórmula rodar, e o hash da configuração do gate vai no livro-razão.

**Justificativa:** o padrão de falha é previsível — seis meses sem nada passar, e o corte de 0,95 começa a parecer excessivo. Alterar limiares em andamento converte o gate em decoração. Se for necessário alterá-los, isso constitui um projeto novo, com contador zerado e histórico separado.

---

## 3. Arquitetura (modelo C4)

### 3.1 Nível 1 — Contexto

| Elemento | Tipo | Relação |
|---|---|---|
| Pesquisador quant | Pessoa | Aprova teses, opera o hold-out, decide implantação |
| **Plataforma de descoberta** | Sistema | Gera e valida fórmulas |
| MetaTrader 5 / B3 | Sistema externo | Dados históricos (pesquisa) e execução (produção) |
| Provedores de LLM | Sistema externo | Geração de hipóteses, fórmulas e provas |
| Lean 4 + Mathlib | Sistema externo | Verificação formal |

**Nota de projeto:** o MT5 aparece uma vez, mas cumpre dois papéis com requisitos de confiabilidade completamente diferentes (fonte de dados na pesquisa, destino de ordens na produção). Tratar como dois sistemas externos distintos no registro de decisões.

### 3.2 Nível 2 — Containers

| Container | Tecnologia | Responsabilidade | Estado |
|---|---|---|---|
| Orquestrador | Python + LangGraph | Grafo de estados, projeção, orçamentos, gravação | Sem estado persistente |
| Serviço de agentes | Python + API de LLM | Chamadas de modelo com schema restrito | Sem estado |
| Motor de DSL | Python puro | AST, tipos, canonicalização, assinatura, avaliadores | Biblioteca, sem estado |
| Motor de backtest | Python + numpy | CPCV, simulação de execução, métricas por caminho | Sem estado |
| Serviço de prova | ax-prover + Lean 4 | Verificação formal de obrigações | Cache de provas |
| Livro-razão | SQLite | Registro append-only de tentativas | **Estado crítico** |
| Armazém de dados | Parquet | Ticks e barras do WIN, WDO, ES; série contínua ajustada | Imutável |
| Gerador de código | Python (transpilador) | AST → MQL5 | Sem estado |

**Relações que importam e não aparecem no diagrama:**

- O **motor de DSL é o vocabulário compartilhado**. O serviço de agentes emite AST, o backtest avalia AST, o gerador traduz AST, o livro-razão indexa hash de AST. Por isso ele é o primeiro a ser construído e o mais estável.
- O **motor de backtest é o único candidato a sair do Python**. Se o loop rodar milhares de avaliações sobre ticks, o gargalo está ali. Medir antes de reescrever.
- O **serviço de prova roda fora do caminho crítico**. Fórmula reprovada na prova não chega ao backtest, o que economiza a parte cara.
- O **livro-razão em SQLite** é escolha deliberada: arquivo único, transacional, versionável e difícil de resetar por acidente. Postgres apenas se o loop virar distribuído.

### 3.3 Nível 3 — Componentes do orquestrador

| Componente | Função |
|---|---|
| Roteador | Decide o próximo nó a partir do estado; função pura |
| Projetor de visão | Constrói `ResearchView` e `VerificationView`; implementa P1 |
| Controle de orçamento | Aplica tetos por tentativa, por hipótese e global |
| Gravador | Escreve no livro-razão, encadeia por hash |
| Log de eventos | Registra tudo com sementes, para replay determinístico |

O **projetor de visão** é o componente de maior risco do sistema inteiro e o primeiro a receber teste, antes de qualquer outra linha de código:

```python
def test_pesquisa_nunca_ve_metricas():
    state = TrialState(metrics=Metrics(sharpe=2.3, ...), ...)
    payload = serialize(project(state, Zone.RESEARCH))
    assert "sharpe" not in payload
    assert "2.3" not in payload
```

O segundo `assert` pega o caso difícil: métrica vazando embutida em texto livre escrito por algum agente. O teste deve rodar sobre o payload final enviado à API, não sobre o objeto de estado.

---

## 4. Os cinco agentes

Padrão comum: **no máximo uma chamada de LLM por agente**; código determinístico no resto. Dois dos cinco não fazem nenhuma.

### 4.1 Agente de hipótese

Produz um contrato que restringe o espaço de busca antes que ele exista.

**Pipeline interno:** seletor de família (bandit) → gerador de tese (LLM) → teste de falseabilidade → detector de duplicata → tradutor de restrições.

**Seletor de família.** Catálogo fechado, escolhido por bandit UCB. Deixar o LLM escolher o tema livremente faz com que ele gravite para as mesmas três ideias óbvias. Catálogo inicial para o WIN:

```
ABERTURA_E_GAP · FLUXO_DE_AGRESSAO · INTERMERCADO_SP500 ·
INTERMERCADO_DOLAR · REVERSAO_HORARIA · REGIME_DE_VOL ·
LIQUIDEZ_E_HORARIO · ROLAGEM_DE_VENCIMENTO
```

**A recompensa do bandit não é Sharpe** — isso seria vazamento pela porta dos fundos. É a taxa de vereditos não-triviais: famílias que só produzem `REDUNDANT` e `INVALID_AST` perdem prioridade; famílias que chegam ao gate ganham. Mede produtividade do processo, não lucro.

**Schema de saída:**

```python
@dataclass(frozen=True)
class Hypothesis:
    family: Family
    claim: str              # o que se afirma, uma frase
    who_pays: str           # quem está do outro lado perdendo
    observable: Observable  # o que medir nos dados
    direction: Direction
    horizon: timedelta
    session_window: TimeWindow
    regime_filter: Regime | None
    kill_condition: str     # o que faria abandonar a tese
    dsl_constraints: Constraints
```

**O campo `who_pays` é o filtro mais barato e mais eficaz do sistema.** Todo alfa é dinheiro saindo do bolso de alguém. Respostas legítimas no WIN existem: o hedger institucional que executa independente de preço, o retail alavancado liquidado em cascata, o arbitrador com custo de carrego. "Porque o mercado tende a subir depois disso" não é resposta, é observação sem mecanismo.

**O campo `kill_condition` é pré-registro clínico aplicado a trading.** Declara antecipadamente o que faria abandonar a tese. Sem isso, toda estratégia que perde vira "só uma fase ruim". Idealmente é uma métrica de **microestrutura**, não de PnL — assim dispara antes do prejuízo.

**Teste de falseabilidade.** Rejeita se `kill_condition` está vazia ou é circular, se `observable` não é computável com os dados disponíveis, ou se `horizon` é incompatível com `session_window`.

**Tradutor de restrições.** Converte a tese em contrato executável:

```python
Constraints(
    allowed_ops   = {ZSCORE, ROLLING_STD, LAG, CORR_WITH},
    forbidden_ops = {RANK_CS},          # sem sentido com um ativo só
    max_window    = 120,                # barras M1
    max_depth     = 4,
    required_refs = {"WDO", "ES"},
    session_mask  = TimeWindow("10:30", "16:30"),
)
```

**Exemplo completo de hipótese válida:**

```
family:         INTERMERCADO_SP500
claim:          Após deslocamento abrupto do S&P futuro, o WIN
                reprecifica com atraso de 1 a 3 minutos
who_pays:       Market makers do WIN que alargam spread na incerteza
                e não repassam o movimento imediatamente
observable:     Retorno do ES em janela de 2 min vs retorno
                contemporâneo do WIN, normalizado por volatilidade
direction:      LONG quando ES sobe e WIN ainda não seguiu
horizon:        3 min
session_window: 10:30 – 16:30
kill_condition: Lag médio de reprecificação abaixo de 20 s em
                janela móvel de 3 meses
```

### 4.2 Agente de fórmula

**Pipeline interno:** gramática restrita → gerador de AST (LLM) → validador de tipos → canonicalizador → filtro de memória.

**Gramática restrita.** Montada por tentativa a partir das `Constraints`. Entregue como schema de tool use com `enum` no campo `op`: o que não está na gramática não pode ser emitido, e emitir algo fora dela vira erro de schema antes de sair do modelo.

**Sistema de tipos por unidade.** A peça que mais rende e que quase ninguém implementa:

```
Price     pontos do WIN
Return    adimensional
Volume    contratos
Z         adimensional normalizado
Bool      {0, 1}
Window    inteiro positivo, em barras
```

```
add(a: T, b: T) -> T                      mesma unidade, sempre
div(a: Price, b: Price) -> Return         divisão muda a unidade
zscore(x: Series[T], w: Window) -> Z
corr(a: Series[T], b: Series[U], w) -> Z
gt(a: T, b: T) -> Bool                    comparação exige mesma unidade
```

`add(close, volume)` é rejeitado antes de tocar em dado. Em espaço de busca automatizado, esse tipo de lixo é a maioria das gerações inválidas — eliminá-lo por tipagem é gratuito. **O sinal final deve ser `Z` ou `Bool`**; fórmula que sai em `Price` não é comparável entre regimes de volatilidade.

**Catálogo de operadores (18 no total):**

```
Séries base:      close, high, low, volume, trades, vwap
Referências:      ref("WDO"), ref("ES")
Temporais:        lag, delta, ret, rolling_mean, rolling_std,
                  rolling_max, rolling_min, ema
Normalização:     zscore, rank_ts, clip
Combinação:       add, sub, mul, div
Lógica:           gt, lt, and_, or_
Sessão:           in_window(h1, h2), minutes_since_open
```

O que está ausente é tão importante quanto o presente: sem `if` aninhado, sem constante livre que não seja janela, sem operador com mais de dois argumentos. Cada um seria porta de entrada para overfitting.

**Forma canônica.** Sem ela o dedupe não funciona e o orçamento é gasto testando a mesma fórmula escrita de formas diferentes:

```
add(b, a)          → add(a, b)        ordena filhos comutativos
lag(x, 0)          → x                 remove identidades
sub(x, x)          → 0
zscore(zscore(x))  → zscore(x)         idempotência
div(mul(a,b), b)   → a                 cancelamento
constantes         → dobradas
```

Serializa em S-expression determinística e hasheia: essa é a **assinatura exata**.

**Assinatura estrutural — o mecanismo antituning.** A assinatura exata não basta: emitir `zscore(ret(close), 20)` e depois `zscore(ret(close), 21)` gera hashes diferentes e constitui tuning de parâmetro disfarçado de exploração, exatamente o que P1 deveria impedir.

```python
WINDOW_BUCKETS = [1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144]  # fibonacci

def structural_signature(ast):
    canon = canonicalize(ast)
    bucketed = map_params(canon, snap_to_bucket)
    return sha256(serialize(bucketed))
```

Janela 20 e 21 caem no mesmo bucket, mesma assinatura, rejeição como `REDUNDANT`. Para explorar aquela região novamente, o agente é obrigado a mudar a **estrutura**, não o número. O espaçamento fibonacci é deliberado: a diferença entre 5 e 8 barras é significativa, entre 89 e 92 não é.

Este mecanismo é a contribuição de desenho mais importante do projeto e tem precedente direto na literatura (ver seção 8.2, AlphaAgent).

**Limites de complexidade:**

```python
depth(ast)        <= constraints.max_depth      # tipicamente 4
node_count(ast)   <= 12
free_params(ast)  <= 3    # janelas distintas
```

`free_params` é o mais importante: entra direto no cálculo de graus de liberdade.

**Exemplo de AST válida** (a hipótese de intermercado da seção 4.1):

```json
{"op": "mul", "args": [
  {"op": "in_window", "args": [{"h1": "10:30"}, {"h2": "16:30"}]},
  {"op": "sub", "args": [
    {"op": "zscore", "args": [
      {"op": "ret", "args": [{"op": "ref", "args": ["ES"]}, {"w": 2}]},
      {"w": 55}]},
    {"op": "zscore", "args": [
      {"op": "ret", "args": [{"op": "close"}, {"w": 2}]},
      {"w": 55}]}
  ]}
]}
```

Profundidade 4, 2 janelas livres, saída `Z`, usa `ref("ES")` conforme exigido pela constraint.

### 4.3 Agente de prova

**Pipeline interno:** tradutor para Lean → seletor de obrigações → provador iterativo (LLM) → revisor → caderno de falhas.

A distinção que define o custo deste agente:

**Classe A — provadas uma vez, sobre o motor.** Não dependem de qual fórmula é:

```lean
theorem eval_causal (f : Formula) (d : MarketData) (t : Nat) :
    eval f d t = eval f (truncate d t) t
```

Toda fórmula bem-tipada no DSL é causal. Uma prova, validade universal — infinitamente melhor que checar lookahead fórmula por fórmula. Na mesma classe: idempotência do canonicalizador, totalidade da divisão, e as invariantes de risco:

```lean
theorem sizing_respects_margin (eq : Equity) (m : Margin) :
    contracts eq m ≤ maxContracts eq m

theorem stop_is_valid_tick (p : Price) :
    (stopPrice p) % 5 = 0

theorem no_entry_after_cutoff (t : Time) :
    t ≥ cutoff → action t ≠ OpenPosition
```

Essas três são o retorno real do investimento. São falhas que o backtest não captura (não simula rejeição de ordem pela B3 nem margem intradiária) e que aparecem em produção no pior dia possível.

**Classe B — por fórmula.** Sobra pouco, e a que importa é a equivalência:

```lean
theorem vec_eq_inc (f : Formula) (d : MarketData) (t : Nat) :
    evalVectorized f d t = evalIncremental (foldState f d t)
```

Divergência entre a implementação vetorizada do backtest e a incremental do `OnTick()` é a causa número um de robô que ganha no teste e perde no real — mais frequente que overfitting, porque overfitting ao menos se suspeita.

**Seleção de obrigações** é tabela, não LLM:

```python
def obligations(ast: Node) -> list[Obligation]:
    obs = [VEC_EQ_INC]                          # sempre
    if has_op(ast, DIV):   obs.append(NO_DIV_BY_ZERO)
    if has_op(ast, EMA):   obs.append(STATE_CONVERGES)
    if has_op(ast, REF):   obs.append(TIMESTAMP_ALIGNED)
    return obs
```

`TIMESTAMP_ALIGNED` é crítico em intermercado: garante que o alinhamento entre WIN e ES no instante *t* não usa a barra do ES que ainda não fechou. É lookahead sutil que o backtest engole sem reclamar.

**Teto de iterações: 10 a 15, não 50.** Se uma obrigação dessas não fecha em 15 tentativas, provavelmente é falsa — e essa já é a informação desejada.

**Revisor** bloqueia: `sorry` remanescente, `axiom` novo introduzido, enunciado modificado, e táticas que terceirizam confiança ao compilador. O ax-prover já implementa verificação de preservação de enunciado e de táticas trapaceiras.

**Dimensionamento honesto.** Este é o primeiro agente a cortar do MVP. Ordem recomendada: (1) as três invariantes de risco provadas à mão, uma vez, sem agente — duas tardes de trabalho e 80% do valor; (2) causalidade e idempotência do motor, também manual; (3) só então o agente automatizado, e apenas para `VEC_EQ_INC`. Se o passo 3 nunca chegar, tudo bem. Pular 1 e 2 não.

### 4.4 Agente de backtest

**Pipeline interno:** montador de folds → avaliador de sinal → simulador de execução → métricas por fold → selador. **Nenhuma chamada de LLM.**

**Montador de folds.** CPCV com N=8 grupos e k=2 de teste, gerando 28 combinações. Parâmetros críticos:

- **Purga**: remove do treino toda amostra cujo rótulo se sobrepõe temporalmente ao período de teste.
- **Embargo**: buffer adicional após a fronteira, porque autocorrelação serial atravessa a purga. Regra prática: 2× a 5× o horizonte do rótulo. Rótulo de 3 min → embargo de 10 min.
- **Fronteira de pregão**: os grupos devem quebrar no fechamento. Um fold que atravessa o overnight incorpora informação de gap que não existia no momento da decisão.

**Simulador de execução.** Itens não negociáveis:

- **Grade de preço.** Tudo arredondado para múltiplo de 5 pontos na direção conservadora. Preço fracionário é lucro fantasma.
- **Modelo de fill pessimista.** Ordem a mercado preenche no melhor preço do lado oposto mais 1 tick. Ordem limitada só preenche se o preço **atravessar** o nível, não se apenas tocar.
- **Custos parametrizados**, nunca hardcoded:

```python
@dataclass(frozen=True)
class CostModel:
    point_value: float = 0.20      # R$ por ponto
    tick_size: int = 5             # pontos
    slippage_ticks: int = 1        # por lado
    exchange_fee: float = ...      # emolumentos B3, por contrato/lado
    registration_fee: float = ...  # taxa de registro
    brokerage: float = ...         # tabela da corretora
```

Preencher `exchange_fee` e `registration_fee` a partir da tabela vigente da B3 e da tabela da corretora. **Não assumir zero porque a corretagem é zero** — emolumento e registro não são. Com os defaults acima, o round trip custa no mínimo 2 ticks de slippage (10 pontos, R$2,00 por contrato) mais taxas.

- **Regras de rejeição.** Ordem fora do túnel de negociação ou após o horário de zeragem é rejeitada. Backtest que aceita essas ordens testa uma estratégia que não existe.

**Métricas por fold, nunca agregadas.** O agente devolve um vetor, uma entrada por caminho do CPCV:

```python
@dataclass(frozen=True)
class FoldMetrics:
    path_id: int
    n_trades: int
    gross_pnl_points: float
    net_pnl_points: float
    sharpe: float
    max_dd_points: float
    hit_rate: float
    avg_holding: timedelta
```

A distribuição é o insumo do gate: o DSR precisa da variância entre caminhos e o veredito `UNSTABLE_ACROSS_FOLDS` só existe se houver análise caminho a caminho. Uma fórmula com Sharpe médio 1,5 composta de metade dos caminhos em 3,0 e metade em zero é lixo, e a média esconde isso. **O backtest mede; o gate julga.**

**Selador — a ordem das operações importa:**

```python
def seal(result: BacktestResult) -> SealedResult:
    ledger.append(                     # PRIMEIRO grava
        trial_id=result.trial_id,
        ast_hash=result.ast_hash,
        structural_sig=result.structural_sig,
        data_hash=result.data_hash,
        config_hash=result.config_hash,
        prev_hash=ledger.head(),
        timestamp=now(),
    )
    return SealedResult(result)        # DEPOIS devolve
```

Se o processo morrer entre avaliar e gravar, a tentativa é perdida e o contador subestimado infla o DSR. Write-ahead resolve. **Backtest que crashou também é tentativa.**

**Proibições explícitas (escrever como teste):**

- Não escolhe período de dados — vem do config, hasheado.
- Não reexecuta com parâmetro diferente — uma AST, uma avaliação, um registro.
- Não devolve nada além de `SealedResult` — sem log, sem print, sem campo extra.
- Não expõe cache consultável de fora. Se a zona de pesquisa consegue perguntar "essa já foi avaliada e deu quanto", o firewall caiu.
- Não faz otimização de parâmetro. Varrer janelas significa uma tentativa por janela no livro-razão.

### 4.5 Gate estatístico

**Pipeline interno:** triagem de sanidade → agregador de caminhos → deflator de Sharpe → bateria de robustez → colapsador. Único componente do sistema que lê o contador global.

**Triagem de sanidade** (cortes baratos antes de conta cara):

```python
MIN_TRADES_PER_PATH     = 30
MIN_TOTAL_TRADES        = 400
MIN_ACTIVE_DAYS         = 120     # tem que atravessar regimes
MAX_TRADE_CONCENTRATION = 0.25    # nenhum dia responde por >25% do PnL
```

O último é o mais revelador. Resultado inteiro vindo de três dias não é estratégia, é aposta que deu certo — e no WIN isso acontece com frequência (circuit breaker, dia de Copom).

**Agregador de caminhos — PBO.** A probabilidade de overfitting de backtest, calculada a partir dos caminhos do CPCV: para cada partição, seleciona-se a melhor configuração dentro da amostra e observa-se em que percentil ela cai fora da amostra. PBO acima de 0,5 significa que o processo de seleção é pior que acaso. **Corte recomendado: 0,20.** Guardar também mediana, intervalo interquartil e fração de caminhos com sinal invertido.

**Deflator de Sharpe.** Limiar esperado sob a nula, dadas N tentativas:

```
SR* = √V[SR] · [ (1−γ)·Z⁻¹(1 − 1/N) + γ·Z⁻¹(1 − 1/(N·e)) ]
```

γ = constante de Euler-Mascheroni (≈0,5772), V[SR] = variância dos Sharpes entre tentativas, Z⁻¹ = inversa da normal padrão. SR* é o Sharpe esperado **por puro acaso** após N tentativas, e cresce sem limite com N.

```
DSR = Z[ (SR − SR*)·√(T−1) / √(1 − γ₃·SR + (γ₄−1)/4·SR²) ]
```

γ₃ e γ₄ = assimetria e curtose dos retornos. O denominador corrige não-normalidade, e isso importa muito no WIN: retorno intradiário de índice é leptocúrtico com cauda esquerda pesada, o que infla o Sharpe ingênuo. **DSR é probabilidade; corte em 0,95.**

> **Conferir as fórmulas contra o paper original (Bailey & López de Prado, 2014) antes de codificar.** Errar um sinal aqui compromete todo o resto. A biblioteca `purgedcv` (seção 9) implementa e pode servir de referência cruzada.

O N vem de uma única chamada:

```python
n_trials = ledger.count(project_id=cfg.project_id)
```

Não é o número de fórmulas boas nem de fórmulas daquela hipótese. É tudo que já foi avaliado no projeto desde o primeiro dia. A documentação do `purgedcv` é explícita no mesmo sentido: o DSR deflaciona pelo número de configurações pesquisadas, não pelo número de caminhos do CPCV.

**Bateria de robustez — e a armadilha dentro dela:**

```python
RUIDO        # perturba preços em ~1 tick, 100 reamostragens
SUBPERIODOS  # 3 fatias cronológicas, consistência de sinal
CUSTOS       # reroda com 1,5× e 2,0× o modelo de custo
PARAMETROS   # desloca janelas para buckets fibonacci vizinhos
```

**Todos são conjuntivos.** A fórmula precisa passar em todos. Rodar quatro variações e aceitar se *alguma* passar adicionaria quatro tentativas ao N; exigir aprovação em todas torna o teste mais **rígido**, e conta como uma tentativa. Mesma lógica no teste de parâmetro: desloca-se a janela e exige-se consistência de **sinal**, nunca se escolhe a melhor. Escolher a melhor é otimização e custa tentativa; exigir consistência é robustez e não custa.

**Colapsador — a ordem importa:**

```python
def collapse(m: PathMetrics, n_trials: int) -> Verdict:
    if m.total_trades < MIN_TOTAL_TRADES:   return INSUFFICIENT_SAMPLE
    if m.gross_points <= m.cost_points:     return COST_DOMINATED
    if m.sign_flip_frac > 0.30:             return UNSTABLE_ACROSS_FOLDS
    if m.pbo > 0.20:                        return FAILED_GATE
    if dsr(m, n_trials) < 0.95:             return FAILED_GATE
    if not m.robustness_all_passed:         return FAILED_GATE
    return ACCEPTED
```

`COST_DOMINATED` vem cedo porque é o veredito mais acionável: diz "vá para horizonte maior" sem dizer nada sobre lucro. PBO, DSR e robustez colapsam para o mesmo `FAILED_GATE` — granularidade aqui seria vazamento, pois saber *qual* falhou é informação quantitativa sobre a superfície.

**`ACCEPTED` não é sinal verde.** Significa que a fórmula ganhou o direito de tocar no **hold-out sagrado** — fatia de dados que nenhum agente jamais viu, guardada no dia zero, consultável uma única vez por candidato. Cada uso do hold-out entra no livro-razão com contador próprio. Um hold-out consultado 50 vezes deixou de ser hold-out.

### 4.6 Vocabulário de vereditos

```python
class Verdict(Enum):
    ACCEPTED                  # passou no gate; segue para hold-out
    INVALID_AST               # não compila no DSL
    TOO_COMPLEX               # profundidade acima do limite
    REDUNDANT                 # assinatura estrutural já vista
    PROOF_FAILED              # violou invariante formal
    INSUFFICIENT_SAMPLE       # amostra insuficiente
    UNSTABLE_ACROSS_FOLDS     # sinal inverteu entre caminhos
    COST_DOMINATED            # alfa bruto < custo de transação
    FAILED_GATE               # PBO, DSR ou robustez
```

Nenhum carrega número. O agente de fórmula pode aprender que uma família tende a ser dominada por custo e mudar de direção, mas não consegue fazer subida de gradiente no backtest. É a diferença entre orientação e otimização.

**Mapeamento veredito → ação:**

| Veredito | Efeito na próxima tentativa |
|---|---|
| `INVALID_AST` | Erro de schema completo volta; retry barato; não conta tentativa |
| `TOO_COMPLEX` | `max_depth` cai em 1 para essa hipótese |
| `REDUNDANT` | Assinatura entra no bloqueio; operador raiz é banido |
| `PROOF_FAILED` | Goal state do Lean volta inteiro; retry |
| `UNSTABLE_ACROSS_FOLDS` | `max_window` é cortado pela metade |
| `COST_DOMINATED` | `min_horizon` sobe um bucket |
| `FAILED_GATE` | **Hipótese inteira encerrada**; volta ao agente de hipótese |

`FAILED_GATE` não gera retry. Passar perto do gate e tentar de novo é a definição de data snooping.

### 4.7 Agente de execução

**Pipeline interno:** emissor incremental → injetor de guards → montador do `.mq5` → verificador de paridade → instrumentador. **Nenhuma chamada de LLM.**

**Emissor incremental.** Cada operador de janela tem tradução incremental conhecida:

| Operador | Estado mantido | Custo/tick |
|---|---|---|
| `lag(x, k)` | ring buffer de k+1 | O(1) |
| `rolling_mean` | soma corrente + ring buffer | O(1) |
| `rolling_std` | Welford (média + M2) | O(1) |
| `rolling_max/min` | deque monotônica | O(1) amortizado |
| `ema` | recorrência de um valor | O(1) |
| `zscore` | mean + std acima | O(1) |
| `corr` | Welford bivariado | O(1) |

**Welford é obrigatório, soma-de-quadrados ingênua não serve.** Rodando meses em ponto flutuante, `E[x²] − E[x]²` acumula erro e pode produzir variância negativa. A versão vetorizada não sofre porque recalcula do zero; a incremental sofre. É a fonte mais comum de divergência silenciosa.

**Injetor de guards.** As invariantes provadas em Lean viram código executável, com pós-condição verificada em runtime:

```mql5
bool GuardOrder(double &price, int &lots) {
   if(TimeCurrent() >= g_cutoff)              return false;
   if(g_daily_loss <= -MAX_DAILY_LOSS)        return false;
   lots  = MathMin(lots, MaxContracts(AccountEquity()));
   if(lots <= 0)                              return false;
   price = MathRound(price / 5.0) * 5.0;
   if(MathMod(price, 5.0) != 0.0)             return false;  // pós-condição
   return true;
}
```

A última linha parece redundante após o arredondamento; não é. É a pós-condição do teorema, custa nada, e captura o dia em que alguém alterar o arredondamento.

Guards obrigatórios: horário de zeragem, limite de perda diária, teto de contratos pela margem, grade de 5 pontos, e circuit breaker após N rejeições consecutivas (rejeição em série indica premissa quebrada).

**Esqueleto MQL5:**

```mql5
int OnInit() {
   g_state.Reset();
   g_cutoff = ...;
   if(!LoadWarmup(WARMUP_BARS)) return INIT_FAILED;
   return INIT_SUCCEEDED;
}

void OnTick() {
   datetime bt = iTime(_Symbol, TF, 0);
   if(bt == g_last_bar) { ManageOpen(); return; }   // caminho quente
   g_last_bar = bt;

   g_state.Update(iClose(_Symbol, TF, 1), ...);     // barra FECHADA
   double sig = g_state.Signal();
   LogDecision(bt, sig, g_state.KillMetric());
   Decide(sig);
}
```

Dois pontos críticos: (a) na maioria dos ticks o handler retorna na terceira linha, e toda a matemática roda uma vez por barra; (b) o `Update` usa o índice **1**, a barra fechada, **nunca a 0** — a barra 0 está em formação e muda a cada tick, e o backtest só viu barras fechadas.

`WARMUP_BARS` = maior `max_window` da AST × 3, especialmente com `ema` (que tecnicamente nunca converge). Antes disso, o robô não opera.

**Verificador de paridade** — o teste a rodar mesmo pulando o Lean inteiro:

```
1. Exporta N barras históricas do MT5
2. Roda a AST vetorizada em Python              → vetor A
3. Roda a EA em modo "só log" no Strategy Tester → vetor B
4. max(|A − B|) < 1e-9 em todos os pontos
```

Rodar também com estado quente após o warmup, e em período que inclua virada de dia e rolagem de vencimento — são os pontos onde o estado incremental desalinha.

**Instrumentador.** `LogDecision` grava por barra: timestamp, valor do sinal, e a **métrica da condição de morte** declarada pelo agente de hipótese. No exemplo de intermercado, o robô mede e loga o lag de reprecificação todo dia, independente de estar ganhando.

Quando o monitor dispara, ele **encerra a hipótese no livro-razão** e o bandit do agente de hipótese aprende que a família esgotou. É esse laço que transforma o sistema de um gerador de estratégias em um processo de pesquisa — ele aprende com o que morreu, não apenas com o que passou. E dispara por microestrutura, antes de o PnL virar.

**Implantação:**

1. **Conta demo**, 2 a 4 semanas, comparando PnL realizado com o previsto pelo backtest no mesmo período. Divergência aqui é bug de execução, não decaimento.
2. **Real com 1 contrato**, 4 a 8 semanas. Mede slippage verdadeiro e testa o modelo de custo.
3. **Escala** gradual, depois.

O pacote de implantação carrega o `.mq5`, o hash da AST, o hash do config do gate, o certificado de prova e o resultado do teste de paridade.

---

## 5. Fluxo de dados e contratos

### 5.1 Objeto de estado

```python
@dataclass
class TrialState:
    trial_id: str              # UUID atribuído pelo orquestrador
    parent_id: str | None
    hypothesis: Hypothesis
    formula_ast: Node | None
    proof_status: ProofStatus  # PROVED | FAILED | SKIPPED
    verdict: Verdict | None
    metrics: Metrics | None    # PRIVADO — nunca projetado à pesquisa
    attempt: int
    events: list[Event]
```

### 5.2 Roteamento

```python
def route(state: TrialState) -> str:
    if state.formula_ast is None:        return "formula_agent"
    if state.proof_status == FAILED:     return "formula_agent"
    if state.verdict is None:            return "verification"
    if state.verdict == ACCEPTED:        return "codegen"
    if state.attempt >= MAX_ATTEMPTS:    return "abandon"
    return "formula_agent"
```

### 5.3 Memória compartilhada — e por que ela é canal de vazamento

A zona de pesquisa precisa de memória para não repetir erros, mas um caderno que registre "janela de 20 deu pior que 50" reconstrói o gradiente por trás do firewall.

**Regra:** a memória da zona de pesquisa guarda apenas pares `(assinatura estrutural, veredito categórico)`, com teto de tamanho. A zona de verificação tem memória própria, separada, completa — essa serve ao analista humano, não aos agentes geradores.

### 5.4 Orçamentos

| Nível | Teto sugerido | Observação |
|---|---|---|
| Por tentativa | 10 iterações de refinamento | O ax-prover usa 50 por teorema porque o verificador é sound; aqui não é |
| Por hipótese | 20 a 30 fórmulas distintas | |
| **Global** | Escolhido antes de começar | É o N que entra no DSR |

O teto global deve ser escolhido conscientemente no dia zero. Planejar 5.000 avaliações significa nascer com um corte de significância brutal.

### 5.5 Paralelismo

Modelo de ilhas: várias hipóteses em paralelo, ilhas que não se comunicam (preserva diversidade). Mas o **livro-razão é único e global, com escrita serializada**. É o único ponto de contenção do sistema, e é intencional: contadores por ilha multiplicariam as falsas descobertas pelo número de ilhas.

### 5.6 Pontos de intervenção humana

Dois, e apenas dois:

- **Antes:** aprovar a tese do agente de hipótese. Se a tese não faz sentido econômico, mata-se ali, antes de gastar orçamento. É o filtro mais barato do sistema.
- **Depois:** `ACCEPTED` não implanta automaticamente. Produz um candidato para o hold-out e, depois, paper trading. O sistema não fecha o laço até dinheiro real sozinho — decisão de arquitetura, não de cautela.

---

## 6. Benchmarks e trabalhos de referência

Esta seção documenta os sistemas de mercado e de pesquisa que aplicam a estrutura proposta, o que cada um demonstrou empiricamente, e o que especificamente se toma emprestado de cada um.

### 6.1 RD-Agent(Q) — Microsoft Research

**O que é.** Descrito pelos autores como o primeiro framework multiagente data-centric para automatizar o ciclo completo de pesquisa e desenvolvimento de estratégias quantitativas, via co-otimização coordenada de fatores e modelos.

**Arquitetura.** Decompõe o processo em um estágio de **Pesquisa** (formula hipóteses a partir de priors de domínio e as mapeia em tarefas concretas) e um estágio de **Desenvolvimento** (o agente Co-STEER gera o código, executado em backtest de mercado real). Os dois são ligados por um estágio de feedback com escalonador multi-armed bandit para seleção adaptativa de direção.

**Resultados reportados.** A um custo inferior a US$10, aproximadamente 2× o retorno anualizado de bibliotecas de fatores de referência, usando mais de 70% menos fatores; supera modelos profundos de séries temporais do estado da arte com orçamento menor de recursos.

**O que tomamos emprestado:** a separação Pesquisa/Desenvolvimento (nossa zona de pesquisa e zona de verificação), o bandit para seleção de direção (nosso seletor de família), e a evidência de que o custo de LLM não é o gargalo.

**O que fazemos diferente:** o feedback deles é rico; o nosso é categórico. Eles operam equities com cross-section (centenas de ativos, o que dilui overfitting por diversificação); nós operamos um ativo só, o que torna o firewall indispensável.

- Código: <https://github.com/microsoft/RD-Agent> (MIT)
- Paper: arXiv 2505.15155

### 6.2 AlphaAgent — KDD 2025

**O que é.** Framework autônomo que integra agentes de LLM com regularização ad hoc para minerar fatores alfa **resistentes a decaimento**. Três agentes especializados: Idea Agent (propõe hipóteses de mercado a partir de teoria financeira ou tendências emergentes), Factor Agent (constrói fatores incorporando mecanismos de regularização contra duplicação e overfitting) e Eval Agent (valida praticidade, roda backtest e refina iterativamente via feedback).

**Os três mecanismos de regularização** — e esta é a referência mais próxima do nosso desenho:

1. **Imposição de originalidade** por medida de similaridade baseada em **árvores sintáticas abstratas (AST)** contra alfas existentes.
2. **Alinhamento hipótese–fator** por consistência semântica avaliada por LLM.
3. **Controle de complexidade** por restrições estruturais sobre a AST, prevenindo construções sobre-engenheiradas propensas a overfitting.

**Resultados reportados.** Nos mercados CSI 500 (China) e S&P 500 (EUA), de janeiro de 2021 a dezembro de 2024: retorno excedente anual médio de 11,0% (IR 1,5) e 8,74% (IR 1,05) respectivamente, já líquido de custos de transação, com desempenho robusto em diferentes regimes. Razão de fatores efetivos 81% maior consumindo 30% menos tokens.

**O que tomamos emprestado:** praticamente o esqueleto conceitual. Similaridade por AST é exatamente a nossa assinatura estrutural; controle de complexidade por restrição estrutural é o nosso limite de profundidade e `free_params`; alinhamento hipótese–fator é o nosso tradutor de restrições, implementado de forma mais estrita (contrato de tipos em vez de avaliação semântica por LLM).

**O que fazemos diferente:** eles usam LLM para avaliar alinhamento; nós usamos tipos e gramática restrita, o que é mais barato e não falha de forma silenciosa. Adicionamos o bucketing fibonacci de parâmetros, que a similaridade por AST pura não captura.

- Código: <https://github.com/RndmVariableQ/AlphaAgent> (segue a implementação do RD-Agent)
- Paper: arXiv 2502.16789 / DOI 10.1145/3711896.3736838

### 6.3 AlphaEvolve e a linhagem de agentes evolutivos

**AlphaEvolve (Google DeepMind, fechado).** Representa candidatos — objetos matemáticos novos ou heurísticas práticas — como algoritmos, e usa um conjunto de LLMs para gerar, criticar e evoluir uma população deles. A evolução é ancorada em execução de código e avaliação automática, o que impede que sugestões incorretas do modelo base se propaguem. Requer duas coisas: uma função de avaliação com métricas a otimizar, e um algoritmo inicial.

Essa é literalmente a descrição de uma fábrica de fórmulas de trading, e é também onde mora o perigo: **a função de avaliação do AlphaEvolve é sound** (o código roda ou não roda, o objeto matemático satisfaz ou não a propriedade). A nossa não é.

**CodeEvolve (aberto).** Algoritmo genético baseado em ilhas para manter diversidade populacional, com mecanismo de crossover por "inspiração" que usa a janela de contexto do LLM para combinar características de soluções bem-sucedidas, mais meta-prompting para exploração dinâmica. Supera o AlphaEvolve em um subconjunto dos benchmarks matemáticos usados para avaliá-lo.

**OpenEvolve (aberto).** Implementação de referência do AlphaEvolve. Pipeline de amostrador de prompt, ensemble de LLMs, pool de avaliadores e banco de programas, orquestrados de forma assíncrona para maximizar throughput. Versões recentes incorporam MAP-Elites, arquitetura de ilhas e seeding abrangente para reprodutibilidade.

**ShinkaEvolve.** Refinamentos de eficiência amostral: ensemble de LLMs baseado em bandit e **filtragem por rejeição baseada em novidade**. O segundo é conceitualmente idêntico ao nosso filtro de memória por assinatura estrutural.

**O que tomamos emprestado:** o modelo de ilhas para paralelismo, o seeding integral para reprodutibilidade (OpenEvolve), o bandit de seleção e a rejeição por novidade (ShinkaEvolve).

**O que fazemos diferente — e é o ponto central deste projeto:** nenhum desses sistemas precisa de firewall, porque o avaliador deles é confiável. Acoplar um loop evolutivo de milhares de iterações a um backtest sem contabilidade de tentativas produz uma máquina de mineração de ruído de altíssima qualidade.

- OpenEvolve: <https://github.com/codelion/openevolve> (também mantido em `algorithmicsuperintelligence/openevolve`)
- CodeEvolve: arXiv 2510.14150

### 6.4 Ax-Prover / AxProverBase — Axiomatic AI

**O que é.** Agente mínimo e modular que prova teoremas em Lean 4 por refinamento iterativo, usando LLMs prontos (sem fine-tuning), com loop de feedback, sistema de memória e ferramentas de busca em biblioteca.

**Arquitetura interna** — o template que copiamos:

1. **Proposer** — LLM escreve o código Lean 4, opcionalmente usando LeanSearch e busca web para achar lemas do Mathlib.
2. **Compiler** — builda com `lake`, extrai os goal states nos pontos de `sorry` para dar feedback estruturado.
3. **Reviewer** — verifica preservação do enunciado e validade da prova (sem `sorry`, sem táticas trapaceiras).
4. **Memory** — resume lições das tentativas falhas em um "caderno de laboratório" conciso.

Teto padrão de 50 iterações.

**Resultados reportados** (Claude Opus 4.5, 50 iterações, pass@1): 54,7% no PutnamBench contra 13,0% do Goedel V2 em pass@184; 98,0% no FATE-M contra 62,7% do DeepSeek V2 em pass@64; 66,0% no FATE-H; 24,0% no FATE-X, onde os demais ficaram em 0,0%; 59,0% no LeanCat contra 14,0% do Gemini 3 Pro.

**O que tomamos emprestado:** o loop de quatro peças é o template de todos os nossos agentes; o caderno de falhas categorizadas; a verificação de preservação de enunciado no revisor.

**O que fazemos diferente:** teto de 10 a 15 iterações em vez de 50, e feedback categórico em vez de goal state — **exceto** nos vereditos `INVALID_AST` e `PROOF_FAILED`, onde somos tão generosos quanto eles.

**Nota de licenciamento:** AGPL-3.0. A cláusula de rede obriga abertura de código derivado se o serviço for acessível por rede. Para uso interno e pesquisa não há problema; **avaliar antes de acoplar ao núcleo de um sistema proprietário**.

- Código: <https://github.com/Axiomatic-AI/ax-prover-base> (AGPL-3.0, PyPI `ax-prover`)
- Paper: arXiv 2602.24273
- MCP: <https://github.com/Axiomatic-AI/ax-prover-base-mcp> — **atenção:** envia código a serviços de nuvem externos para compilação e processamento de IA. Não usar com lógica proprietária.

### 6.5 Outros sistemas mapeados

| Sistema | Contribuição | Aplicabilidade ao projeto |
|---|---|---|
| **QuantAgent** | Arquitetura de loop interno (escritor + juiz) e externo (mercado real realimenta o juiz) | Precursor conceitual; o loop externo é o nosso paper trading |
| **Alpha Jungle** | LLM + MCTS para gerar e refinar fórmulas simbólicas, com feedback quantitativo rico do backtest guiando a árvore | **Não adotar o feedback rico.** A ideia de MCTS sobre o espaço de AST é interessante, mas exige repensar a contabilidade de tentativas |
| **AlphaForge** | Pipeline de descoberta e combinação dinâmica de alfas formulaicos | Relevante na fase de combinação de múltiplas fórmulas aprovadas (fora do escopo v1) |
| **WorldQuant 101 Alphas** | Prior estruturado de 101 alfas formulaicos | Estreita muito o espaço de busca e reduz geração inválida. Precisa de tradução para ativo único |

---

## 7. Repositórios de referência

### 7.1 Para estudar e adaptar

| Repositório | Licença | O que usar | O que **não** usar |
|---|---|---|---|
| `microsoft/RD-Agent` | MIT | Estrutura do grafo, escalonador bandit, padrão de loop Research/Development | O acoplamento ao Qlib (equities, cross-section) |
| `microsoft/qlib` | MIT | Convenções de expressão de fatores, estrutura de dados | Todo o pipeline de execução — é equity, não futuro intradiário |
| `RndmVariableQ/AlphaAgent` | ver repo | Similaridade por AST, controle de complexidade estrutural | Alinhamento semântico por LLM (substituído por tipos) |
| `Axiomatic-AI/ax-prover-base` | **AGPL-3.0** | Padrão Proposer/Compiler/Reviewer/Memory | Acoplamento direto ao núcleo, se o produto for fechado |
| `codelion/openevolve` | Apache-2.0 | Modelo de ilhas, MAP-Elites, seeding para reprodutibilidade | O loop evolutivo direto contra backtest, sem contabilidade |
| `Sasha-Cui/Awesome-Applied-Agents-for-Investment` | — | Mapa curado da literatura de agentes aplicados a investimento | — |

### 7.2 Dependências diretas candidatas

| Pacote | Papel no projeto | Observação |
|---|---|---|
| **`purgedcv`** (`eslazarev/purged-cross-validation`) | CPCV, purga, embargo, PSR, DSR, comprimento mínimo de track record | **Candidata principal.** Compatível com scikit-learn, tipada, testada, pip-installable. Implementa reconstrução de caminhos de backtest |
| `langgraph` | Grafo de estados do orquestrador | |
| `pyarrow` / `polars` | Armazém Parquet | |
| `numpy` | Avaliador vetorizado do DSL | |
| `MetaTrader5` (Python) | Exportação de dados e integração com o terminal | Windows apenas |
| `ax-prover` | Serviço de prova | AGPL — isolar por processo/serviço |

**Nota sobre alternativas de CPCV que devem ser evitadas:** `mlfinlab`, a implementação canônica histórica, foi relicenciada como produto pago e fechado. `timeseriescv` é a principal implementação combinatória gratuita, mas não tem release desde 2018 e tem problemas conhecidos de correção. `RiskLabAI` é código de referência de pesquisa, não um drop-in tipado e testado.

### 7.3 Estrutura de repositório proposta

```
win-alpha-factory/
├── pyproject.toml
├── README.md
├── docs/
│   ├── adr/                     # registros de decisão de arquitetura
│   ├── architecture.md
│   └── gate-prereg.md  # hasheado, imutável após o dia zero
├── src/
│   ├── dsl/                     # FASE 1
│   │   ├── ast.py               # nós, tipos, unidades
│   │   ├── parser.py
│   │   ├── canonical.py         # forma normal + assinaturas
│   │   ├── eval_vectorized.py
│   │   └── eval_incremental.py
│   ├── ledger/                  # FASE 1
│   │   ├── schema.sql
│   │   └── ledger.py            # append-only, encadeado
│   ├── backtest/                # FASE 2
│   │   ├── folds.py             # CPCV com fronteira de pregão
│   │   ├── execution.py         # fills, custos, rejeições
│   │   └── metrics.py
│   ├── gate/                    # FASE 2
│   │   ├── dsr.py
│   │   ├── pbo.py
│   │   ├── robustness.py
│   │   └── collapse.py
│   ├── orchestrator/            # FASE 3
│   │   ├── graph.py
│   │   ├── projection.py        # ← primeiro teste do projeto
│   │   ├── budgets.py
│   │   └── router.py
│   ├── agents/                  # FASE 3
│   │   ├── hypothesis.py
│   │   └── formula.py
│   ├── codegen/                 # FASE 4
│   │   ├── mql5_template.mq5
│   │   ├── transpiler.py
│   │   └── parity.py
│   └── proof/                   # FASE 5
│       ├── obligations.py
│       └── lean/
│           ├── Engine.lean      # teoremas de classe A
│           └── Risk.lean
├── data/                        # gitignored
└── tests/
    ├── test_projection.py       # o mais importante
    ├── test_canonical.py
    ├── test_ledger_chain.py
    └── test_parity.py
```

---

## 8. Plano de desenvolvimento

**Princípio ordenador: a ordem de construção não é a ordem do pipeline.** Construir os agentes antes do rigor produz um sistema empolgante que minera ruído em escala industrial.

### Fase 1 — Fundação (sem LLM)

**Entregáveis:** motor de DSL completo; livro-razão operacional.

**Critérios de aceite:**
- Parser e validador de tipos rejeitam 100% de um conjunto de casos negativos escritos à mão (`add(close, volume)`, saída em `Price`, aridade errada).
- `canonicalize(canonicalize(x)) == canonicalize(x)` para 10.000 ASTs geradas aleatoriamente.
- Assinaturas estruturais colidem para janelas do mesmo bucket e divergem entre buckets.
- Livro-razão: inserção concorrente mantém a cadeia de hash íntegra; tentativa de reescrita é detectada.

### Fase 2 — Validação (sem LLM)

**Entregáveis:** motor de backtest com CPCV e modelo de custo do WIN; gate com limiares pré-registrados.

**Critérios de aceite:**
- Folds respeitam fronteira de pregão; nenhum fold atravessa overnight.
- Purga e embargo verificados com teste de vazamento sintético (rótulo com lookahead deliberado é detectado).
- Modelo de custo reproduz, dentro de 1 ponto, o custo real de um round trip observado em extrato.
- **Teste de calibração do gate:** alimentar o gate com 1.000 estratégias geradas sobre séries aleatórias. Menos de 5% devem receber `ACCEPTED`. Se mais passarem, o gate está frouxo.
- `docs/gate-prereg.md` escrito, hasheado e registrado.

**Marco:** ao final da Fase 2 já existe um sistema útil — fórmulas escritas à mão podem ser testadas com rigor superior ao de boa parte das mesas.

### Fase 3 — Geração (primeiros LLMs)

**Entregáveis:** orquestrador; agente de fórmula; agente de hipótese.

**Ordem interna:** projetor de visão e seu teste → roteador → orçamentos → agente de fórmula → agente de hipótese.

**Critérios de aceite:**
- `test_projection.py` passa, incluindo a checagem de string sobre o payload final.
- Auditoria manual de 50 payloads enviados à API: nenhum contém número derivado de backtest.
- Replay determinístico: reexecutar uma tentativa a partir do log de eventos produz a mesma AST.
- O contador do livro-razão cresce exatamente uma unidade por avaliação, inclusive em avaliações que lançam exceção.

### Fase 4 — Execução

**Entregáveis:** transpilador AST → MQL5; verificador de paridade; instrumentação.

**Critérios de aceite:**
- Paridade `max(|A − B|) < 1e-9` em período que inclua virada de dia e rolagem de vencimento.
- Guards rejeitam: ordem após cutoff, preço fora da grade de 5 pontos, tamanho acima do teto de margem.
- Telemetria da condição de morte emitida por barra e persistida.

### Fase 5 — Verificação formal (opcional)

**Entregáveis:** teoremas de classe A provados à mão; depois, agente automatizado para `VEC_EQ_INC`.

**Critérios de aceite:** `lake build` limpo; revisor bloqueia `sorry` e `axiom` introduzidos.

### Fase 6 — Operação

Hold-out → demo → 1 contrato → escala. Ver seção 4.7.

---

## 9. Riscos e mitigações

| # | Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|---|
| R1 | Vazamento de métrica por canal não previsto (texto livre, memória, cache, log) | **Alta** | Crítico | Teste de string sobre payload final; memória restrita a pares assinatura/veredito; auditoria periódica de payloads |
| R2 | Contador de tentativas subestimado | Média | Crítico | Write-ahead no selador; encadeamento por hash; teste de que exceção também grava |
| R3 | Divergência vetorizado/incremental | **Alta** | Alto | Welford obrigatório; teste de paridade na Fase 4; teorema `vec_eq_inc` se a Fase 5 acontecer |
| R4 | Modelo de custo otimista | Média | Alto | Fill pessimista; validação contra extrato real; bateria de robustez com 1,5× e 2,0× |
| R5 | Afrouxamento de limiares sob frustração | **Alta** | Crítico | Pré-registro hasheado; alterar limiares constitui projeto novo com contador zerado |
| R6 | Escopo inflado — book, scalp, múltiplos ativos | Média | Médio | ADR-001 congela o escopo; revisões exigem ADR novo |
| R7 | Restrição AGPL do ax-prover contaminando o produto | Baixa | Alto | Isolar como serviço separado; avaliar alternativa antes de acoplar |
| R8 | Dependência de dados curta (tick do MT5 cobre poucos meses) | Média | Alto | Operar em M1/M5, onde o histórico é maior; exigir `MIN_ACTIVE_DAYS` |

---

## 10. Decisões de arquitetura pendentes

| ADR | Questão | Status |
|---|---|---|
| ADR-001 | Horizonte-alvo: swing intradiário, não scalp | **Decidido** (seção 1.3) |
| ADR-002 | Plataforma: MT5 sobre Profit/NTSL | **Decidido** — linguagem completa, integração Python, Strategy Tester |
| ADR-003 | Linguagem do motor de backtest: Python vs Rust | Aberto — medir na Fase 2 antes de decidir |
| ADR-004 | Teto global de tentativas do projeto | **Aberto e bloqueante** — precisa ser fixado antes da Fase 2 |
| ADR-005 | Usar `purgedcv` como dependência ou reimplementar CPCV/DSR | Aberto — avaliar cobertura de testes e ajuste à fronteira de pregão |
| ADR-006 | Fase 5 (verificação formal) entra no escopo v1 | Aberto — depende de fôlego após a Fase 4 |
| ADR-007 | Combinação de múltiplas fórmulas aprovadas em portfólio | Fora do escopo v1 |

---

## 11. Glossário

| Termo | Definição |
|---|---|
| **AST** | Árvore sintática abstrata; a representação estruturada de uma fórmula |
| **Assinatura estrutural** | Hash da AST canônica com parâmetros agrupados em faixas; identifica fórmulas estruturalmente equivalentes |
| **CPCV** | Combinatorial Purged Cross-Validation; validação cruzada combinatória com purga |
| **DSR** | Deflated Sharpe Ratio; Sharpe corrigido por seleção múltipla e não-normalidade |
| **Embargo** | Buffer temporal após a fronteira de teste, para conter vazamento por autocorrelação |
| **Firewall de informação** | Barreira que impede componentes geradores de observar métricas |
| **Hold-out sagrado** | Fatia de dados nunca vista por nenhum agente, consultável uma vez por candidato |
| **PBO** | Probability of Backtest Overfitting |
| **Purga** | Remoção de amostras de treino cujos rótulos se sobrepõem ao período de teste |
| **Veredito** | Enum categórico; única informação que atravessa o firewall |
| **WIN** | Contrato futuro de mini índice Bovespa; tick de 5 pontos, R$0,20 por ponto |

---

## 12. Referências

**Estatística e metodologia**
- Bailey, D. H. & López de Prado, M. (2014). *The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality*. Journal of Portfolio Management, 40(5).
- Bailey, D. H. & López de Prado, M. (2012). *The Sharpe Ratio Efficient Frontier*. Journal of Risk, 15(2).
- López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley. Capítulos 7 (purga/embargo) e 12 (CPCV).
- Harvey, C., Liu, Y. & Zhu, H. *…and the Cross-Section of Expected Returns*.

**Sistemas multiagente**
- R&D-Agent-Quant — arXiv 2505.15155
- AlphaAgent — arXiv 2502.16789 / KDD '25, DOI 10.1145/3711896.3736838
- AxProverBase — arXiv 2602.24273
- CodeEvolve — arXiv 2510.14150

**Software**
- `purgedcv` — <https://github.com/eslazarev/purged-cross-validation>
- RD-Agent — <https://github.com/microsoft/RD-Agent>
- Qlib — <https://github.com/microsoft/qlib>
- ax-prover — <https://github.com/Axiomatic-AI/ax-prover-base>
- OpenEvolve — <https://github.com/codelion/openevolve>

---

*Documento vivo. Alterações em limiares do gate exigem ADR e implicam reinício da contabilidade de tentativas.*
