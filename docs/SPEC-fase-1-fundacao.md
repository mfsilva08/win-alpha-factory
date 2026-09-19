# Fase 1 — Fundação: motor de DSL e livro-razão

Nenhuma chamada de LLM nesta fase. Tudo aqui é código determinístico e testável
com vetores numéricos.

---

## 1.1 `dsl/ops.py` — o vocabulário

```python
class Op(StrEnum):
    # séries base
    CLOSE = "close"; HIGH = "high"; LOW = "low"
    VOLUME = "volume"; TRADES = "trades"; VWAP = "vwap"
    # referência a outro instrumento
    REF = "ref"
    # temporais
    LAG = "lag"; DELTA = "delta"; RET = "ret"
    ROLLING_MEAN = "rolling_mean"; ROLLING_STD = "rolling_std"
    ROLLING_MAX = "rolling_max"; ROLLING_MIN = "rolling_min"; EMA = "ema"
    # normalização
    ZSCORE = "zscore"; RANK_TS = "rank_ts"; CLIP = "clip"
    # combinação
    ADD = "add"; SUB = "sub"; MUL = "mul"; DIV = "div"
    # lógica
    GT = "gt"; LT = "lt"; AND_ = "and_"; OR_ = "or_"
    # sessão
    IN_WINDOW = "in_window"; MINUTES_SINCE_OPEN = "minutes_since_open"
```

Não acrescente operadores. Cada um a mais amplia o espaço de busca e encarece o
limiar de todas as fórmulas futuras.

**Exceção interna — `CONST = "const"`.** Não é vocabulário de pesquisa: só a
canonicalização o produz (`sub(x,x) -> const(0, unidade de x)`), e `validate`
recusa com `INVALID_AST` qualquer AST de entrada que o contenha. Forma:
`Node(Op.CONST, (valor_inteiro, "Unidade"))`.

**Séries base** (`close`, `high`, `low`, `volume`, `trades`, `vwap`) são sempre
permitidas, sem precisar constar em `allowed_ops`. Só deixam de ser se estiverem
em `forbidden_ops`. `ref` **não** é série base: precisa ser permitido explicitamente.

## 1.2 `dsl/ast.py` — tipos e nós

```python
class Unit(StrEnum):
    PRICE = "Price"      # pontos do WIN
    RETURN = "Return"    # adimensional
    VOLUME = "Volume"    # contratos
    Z = "Z"              # adimensional normalizado
    BOOL = "Bool"        # {0,1}
    WINDOW = "Window"    # inteiro positivo

@dataclass(frozen=True)
class Node:
    op: Op
    args: tuple["Node | int | str", ...] = ()

@dataclass(frozen=True)
class Constraints:
    allowed_ops:   frozenset[Op]
    forbidden_ops: frozenset[Op]
    max_window:    int
    max_depth:     int
    max_nodes:     int = 12
    max_free_params: int = 3
    required_refs: frozenset[str] = frozenset()
    session_mask:  tuple[str, str] | None = None
```

### Tabela de tipagem — implemente exatamente assim

| Operador | Assinatura | Observação |
|---|---|---|
| `close`, `high`, `low`, `vwap` | `() -> Price` | |
| `volume`, `trades` | `() -> Volume` | |
| `ref(sym)` | `(str) -> Price` | `sym ∈ {"ES","WDO"}` |
| `lag(x, w)` | `(T, Window) -> T` | preserva unidade; único operador de janela que aceita `Bool` |
| `delta(x, w)` | `(T, Window) -> T` | preserva unidade |
| `ret(x, w)` | `(Price, Window) -> Return` | muda a unidade |
| `rolling_mean(x, w)` | `(T, Window) -> T` | |
| `rolling_std(x, w)` | `(T, Window) -> T` | |
| `rolling_max/min(x, w)` | `(T, Window) -> T` | |
| `ema(x, w)` | `(T, Window) -> T` | |
| `zscore(x, w)` | `(T, Window) -> Z` | |
| `rank_ts(x, w)` | `(T, Window) -> Z` | |
| `clip(x, lo, hi)` | `(Z, int, int) -> Z` | só aceita Z |
| `add/sub(a, b)` | `(T, T) -> T` | **mesma unidade obrigatória** |
| `mul(a, b)` | `(T, Bool) -> T`, `(Bool, T) -> T` ou `(Z, Z) -> Z` | **aceita as duas ordens**: a canonicalização reordena `mul` |
| `div(a, b)` | `(Price, Price) -> Return`, `(Volume, Volume) -> Return`, `(Z, Z) -> Z` | guard embutido contra zero |
| `gt/lt(a, b)` | `(T, T) -> Bool` | **mesma unidade obrigatória** |
| `and_/or_(a, b)` | `(Bool, Bool) -> Bool` | |
| `in_window(h1, h2)` | `(str, str) -> Bool` | horários "HH:MM" |
| `minutes_since_open()` | `() -> Volume` | tratado como contagem |
| `const(v, u)` | `(int, str) -> u` | interno; nunca aceito na entrada |

Nos operadores de janela (exceto `lag`), em `add/sub` e em `gt/lt`, **`T` é
numérico**: `Price`, `Return`, `Volume` ou `Z`. `Bool` só entra em `lag`, `mul`
e `and_/or_`.

**Profundidade** é contada em **arestas**: um nó sem filhos tem profundidade 0.
Argumentos inteiros e de texto (janelas, símbolos, horários) não são nós. A fórmula
da h001 tem profundidade 4 e 9 nós.

**Regra de saída:** a raiz da AST deve ter unidade `Z` ou `Bool`. Qualquer outra
coisa é `INVALID_AST`. Fórmula que sai em `Price` não é comparável entre regimes
de volatilidade.

## 1.3 `dsl/parser.py` — validação

```python
def validate(node: Node, c: Constraints) -> ValidationResult
```

Checa, nesta ordem, parando no primeiro erro:

1. Todo `op` está em `c.allowed_ops` (ou é série base) e nenhum em
   `c.forbidden_ops`; nenhum é `const`
2. Aridade correta para cada operador
3. Tipagem por unidade conforme a tabela acima
4. Unidade da raiz é `Z` ou `Bool`
5. Toda janela é inteiro em `[1, c.max_window]`
6. `required_refs ⊆ refs(node)`
7. `depth(node) <= c.max_depth`
8. `node_count(node) <= c.max_nodes`
9. `free_params(node) <= c.max_free_params`

`free_params` conta **janelas distintas**, não ocorrências.
`zscore(ret(x,2),55) - zscore(ret(y,2),55)` tem 2 janelas livres (2 e 55), não 4.

Erros 1-6 → `INVALID_AST`, com `detail`. Erros 7-9 → `TOO_COMPLEX`, **sem**
`detail` (R1: só `INVALID_AST` e `PROOF_FAILED` carregam detalhe).

### Vetores de teste obrigatórios

Restrições dos vetores: as da h001 (`allowed_ops` = ZSCORE, RET, LAG, ROLLING_STD,
SUB, MUL, IN_WINDOW, REF; `forbidden_ops` = RANK_TS; `max_window` 120;
`max_depth` 4; `max_nodes` 12; `max_free_params` 3), acrescidas de ADD, GT,
ROLLING_MEAN e EMA — para que cada caso negativo falhe pelo motivo que pretende
testar, e não por operador fora do permitido.

```python
# devem ser rejeitados
sub(close(), ref("ES"))                         # INVALID_AST: raiz em Price
add(close(), volume())                          # INVALID_AST: unidades diferentes
gt(close(), volume())                           # INVALID_AST: comparação de unidades diferentes
zscore(ret(close(),2), 500)                     # INVALID_AST: janela > max_window
mul(gt(volume(), rolling_mean(volume(),21)),
    sub(zscore(ema(ret(lag(close(),1),2),8),21),
        zscore(ema(ret(lag(ref("ES"),1),2),8),21)))   # TOO_COMPLEX: profundidade 6

# deve ser aceito
mul(in_window("10:30","16:30"),
    sub(zscore(ret(ref("ES"),2),55),
        zscore(ret(close(),2),55)))             # profundidade 4, 2 janelas, saída Z
```

## 1.4 `dsl/canonical.py` — forma normal e assinaturas

A forma canônica serve para **identificar** fórmulas (assinaturas, `REDUNDANT`),
**nunca para avaliá-las**. Algumas reescritas não preservam o valor numérico:
`div(mul(a,b),b) -> a` ignora o guard quando `b = 0`, e `zscore(zscore(x,w),w)`
não é igual a `zscore(x,w)`. O avaliador sempre roda a AST original.
`canonicalize` pressupõe uma AST que passou por `validate`.

### Reescritas, aplicadas até ponto fixo

```
add(b, a)             -> add(a, b)        se hash(a) > hash(b)
mul(b, a)             -> mul(a, b)        idem
and_(b, a), or_(b, a) -> ordenados        idem
lt(a, b)              -> gt(b, a)
lag(x, 0)             -> x
sub(x, x)             -> const(0, unidade de x)
zscore(zscore(x,w),w) -> zscore(x,w)
div(mul(a,b), b)      -> a                (e div(mul(a,b), a) -> b)
constantes            -> dobradas
```

Dobra de constantes: `add/sub/mul/gt/and_/or_` entre duas constantes viram
constante; `add(x, 0)`, `sub(x, 0)` e `mul(x, const(1, Bool))` viram `x`;
`mul(x, 0)` vira `const(0)`; `lag/rolling_mean/rolling_max/rolling_min/ema` de
constante viram a própria constante; `delta/ret/rolling_std/zscore/rank_ts` de
constante viram `const(0)`; `clip` de constante é dobrado. `div` entre
constantes não é dobrado.

### Serialização

S-expression determinística, sem espaços supérfluos:
`(mul (in_window "10:30" "16:30") (sub (zscore (ret (ref "ES") 2) 55) ...))`

### Duas assinaturas

```python
WINDOW_BUCKETS = (1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144)

def exact_signature(n: Node) -> str:
    return sha256(serialize(canonicalize(n)))

def structural_signature(n: Node) -> str:
    return sha256(serialize(canonicalize(snap_windows(canonicalize(n)))))

def snap_to_bucket(w: int) -> int:
    """Retorna o bucket mais próximo. Empate resolve para o menor."""
```

A recanonicalização depois do agrupamento é proposital:
`sub(zscore(x,20), zscore(x,21))` só vira `sub(y,y)` — e colapsa para constante —
depois que as janelas caem no mesmo bucket.

**Teste obrigatório:** `structural_signature` de janela 20, 21 e 22 deve ser
idêntica. De janela 55 deve diferir.

**Teste obrigatório:** `canonicalize(canonicalize(x)) == canonicalize(x)` para
10.000 ASTs geradas aleatoriamente dentro de um `Constraints` válido.

## 1.5 `dsl/eval_vectorized.py`

```python
def eval_vec(n: Node, data: MarketFrame) -> np.ndarray
```

`MarketFrame` carrega arrays alinhados de WIN, ES e WDO, mais o índice temporal e
uma máscara de pregão. Retorna array do mesmo tamanho, com `NaN` no período de
aquecimento.

`div` usa guard: denominador com `|x| < 1e-12` produz `0.0`, nunca `inf` ou `NaN`.

`MarketFrame` e `Bar` vivem em `dsl/market.py`; `backtest/data.py` (M3) apenas os
constrói a partir do Parquet.

### Semântica — compartilhada, operador por operador, com o incremental

| Tema | Regra |
|---|---|
| Timestamp | `ts` é o **fechamento** da barra, hora local da B3, sem timezone. O MT5 grava a abertura: o carregador soma 1 minuto |
| Dados | nenhum `NaN`/`inf` aceito no frame. O aquecimento é o único lugar com `NaN` |
| Continuidade | janelas atravessam a virada de dia e a rolagem: a série é contínua ajustada. Filtro de sessão é trabalho de `in_window` e do backtest |
| Propagação | `NaN` em qualquer entrada produz `NaN` (inclusive em `gt/lt/and_/or_` e em `div`, antes do guard). Janela com algum `NaN` produz `NaN` |
| `lag(x,k)` | `x[t-k]` |
| `delta(x,w)` | `x[t] - x[t-w]` |
| `ret(x,w)` | `x[t]/x[t-w] - 1`; `0.0` se o módulo de `x[t-w]` for menor que `1e-12` |
| `rolling_std` | amostral (`n-1`); `0.0` com `w = 1` ou janela constante (máx == mín), **exatamente** |
| `zscore(x,w)` | `(x[t] - média) / desvio`; `0.0` se desvio `< 1e-12` |
| `rank_ts(x,w)` | `2·(#menores + 0,5·#iguais sem contar a atual)/(w-1) - 1`, em `[-1, 1]`; `0.0` com `w = 1` |
| `ema(x,w)` | `α = 2/(w+1)`, semente no primeiro valor válido, `NaN` até acumular `w` valores |
| `in_window(h1,h2)` | `1.0` se `h1 < fechamento <= h2` (a barra cabe inteira na janela) |
| `minutes_since_open` | minutos desde o primeiro fechamento do dia no frame, que vale 1 |
| Bool | `1.0`/`0.0`; em `and_/or_` qualquer valor diferente de zero é verdadeiro |

## 1.6 `dsl/eval_incremental.py`

```python
class IncrementalState:
    def update(self, bar: Bar) -> None
    def signal(self) -> float
    def warmup_bars(self) -> int     # max_window * 3
```

Traduções obrigatórias, uma por operador de janela:

| Operador | Estado | Custo |
|---|---|---|
| `lag(x,k)` | ring buffer de `k+1` | O(1) |
| `rolling_mean` | soma corrente + ring buffer | O(1) |
| `rolling_std` | **Welford** (mean + M2) | O(1) |
| `rolling_max/min` | deque monotônica | O(1) amortizado |
| `ema` | recorrência de um valor | O(1) |
| `zscore` | mean + std acima | O(1) |

**Welford é obrigatório.** `E[x²] − E[x]²` acumula erro de arredondamento e pode
produzir variância negativa após dezenas de milhares de barras.

**Ressincronização.** A soma corrente e o Welford com remoção são recalculados a
partir do ring buffer a cada `w` barras — custo amortizado O(1) — para que o erro
não se acumule por dezenas de milhares de barras. Janela constante é detectada
pelas deques monotônicas de máximo e mínimo e tem desvio exatamente 0, igual ao
vetorizado. `rank_ts` custa O(w) por barra.

```
delta = x - mean
mean += delta / n
M2   += delta * (x - mean)
var   = M2 / (n - 1)
```

**Teste de paridade obrigatório:**
`max(|a - b| / max(1, |a|)) < 1e-9`, com `a = eval_vec(n, d)` e
`b = [inc.update(bar) or inc.signal() for bar in d]`, sobre no mínimo 40.000 barras,
incluindo virada de dia e dia de rolagem. As posições de `NaN` precisam coincidir
exatamente, e depois de `warmup_bars()` não pode haver `NaN`.

O critério é absoluto para `|sinal| <= 1` e relativo acima disso. Motivo, medido:
em `div(zscore(..), zscore(..))` o denominador passa perto de zero, o sinal chega
a −9.419, e as duas implementações — que concordam em 3e-15 nas entradas —
diferem 5e-9 em valor absoluto (5e-13 relativo). Exigir 1e-9 absoluto ali pediria
mais precisão do que o float64 tem.

---

## 1.7 `ledger/` — o livro-razão

### Schema

```sql
CREATE TABLE ledger (
  seq            INTEGER PRIMARY KEY AUTOINCREMENT,
  trial_id       TEXT NOT NULL,
  kind           TEXT NOT NULL CHECK(kind IN ('genesis','backtest','holdout')),
  hypothesis_id  TEXT,
  ast_hash       TEXT,
  structural_sig TEXT,
  data_hash      TEXT NOT NULL,
  config_hash    TEXT NOT NULL,
  verdict        TEXT,
  crashed        INTEGER NOT NULL DEFAULT 0,
  prev_hash      TEXT NOT NULL,
  row_hash       TEXT NOT NULL,
  ts             TEXT NOT NULL
);
CREATE INDEX idx_struct ON ledger(structural_sig);
CREATE INDEX idx_hyp    ON ledger(hypothesis_id);
```

### API

```python
class Ledger:
    def genesis(self, data_hash: str, config_hash: str) -> None
    def append(self, rec: LedgerRecord) -> str      # devolve row_hash
    def count(self, kind: str = "backtest") -> int
    def head(self) -> str
    def verify_chain(self) -> bool
    def seen_structural(self, sig: str) -> bool
    def verdicts_for(self, hypothesis_id: str) -> list[Verdict]
```

`row_hash = sha256(prev_hash + trial_id + ast_hash + data_hash + config_hash + ts)`

### Regras

- `count()` conta apenas `kind='backtest'` e `kind='holdout'`, incluindo os que têm
  `crashed=1`
- Não existe `delete`, `update` nem `reset`. Se essas funções aparecerem no código,
  é bug
- Escrita serializada: uma única conexão, `PRAGMA journal_mode=WAL`, transação por
  append

### Testes obrigatórios

```
- append de 1.000 registros mantém verify_chain() == True
- adulterar uma linha via SQL direto faz verify_chain() == False
- count() após 5 appends sendo 2 crashed retorna 5
- genesis duas vezes levanta exceção
```

---

## Critérios de aceite da Fase 1

1. `mypy --strict src/dsl src/ledger` passa (`src/dsl` já passa desde o M1)
2. Os cinco casos negativos do 1.3 são rejeitados com o veredito correto
3. Idempotência da canonicalização sobre 10.000 ASTs aleatórias
4. Buckets: 20, 21, 22 colidem; 55 não
5. Paridade vetorizado/incremental abaixo de 1e-9 em 40.000 barras (critério do §1.6)
6. Cadeia do livro-razão íntegra após 1.000 appends, e detectando adulteração
