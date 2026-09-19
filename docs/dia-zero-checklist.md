# Dia zero — o que precisa existir antes do primeiro agente rodar

Checklist de configuração da plataforma de descoberta de fórmulas para mini índice.
Ordem importa: os blocos estão na sequência em que devem ser feitos.

---

## Antes de tudo: as três decisões que não dão para desfazer

De tudo que está nesta lista, quase tudo pode ser corrigido depois. Três coisas não podem, e por isso merecem uma tarde de reflexão em vez de dez minutos de chute.

| Decisão | Por que é irreversível |
|---|---|
| **A separação do hold-out** | Precisa acontecer antes de qualquer coisa tocar nos dados. Se você separar depois de já ter rodado backtests, a fatia "nunca vista" já foi vista |
| **O teto global de tentativas** | Ele define o limiar de significância de todas as fórmulas futuras. Escolher depois é escolher o número que faz o seu resultado passar |
| **Os limiares do gate** | Mesma coisa. Escritos depois de ver resultados, eles viram justificativa em vez de critério |

As três viram um arquivo hasheado. Mudá-las depois é permitido, mas constitui um projeto novo, com contador zerado e histórico separado.

---

## Bloco 1 — Dados

**O que exportar do MetaTrader 5:**

| Símbolo | Timeframe | Período mínimo | Para quê |
|---|---|---|---|
| WIN (contínuo ajustado) | M1 | 24 meses | o ativo que se opera |
| ES ou o mini S&P disponível | M1 | 24 meses | famílias de intermercado |
| WDO (contínuo ajustado) | M1 | 24 meses | intermercado com dólar |

**A parte que mais dá errado: a série contínua.**

O WIN vence em meses pares. Se você emendar os contratos sem ajustar, a série ganha saltos artificiais na virada e o backtest vai achar que existe um movimento enorme que nunca aconteceu. Duas opções:

- **Ajuste por diferença** (mais simples): no dia da rolagem, calcula a diferença de preço entre o contrato que vence e o próximo, e soma essa diferença a todo o histórico anterior.
- **Ajuste por razão**: multiplica em vez de somar. Preferível se você for calcular retornos percentuais longos.

Para intradiário com horizonte de minutos, o ajuste por diferença basta.

**A ferramenta faz tudo isto de uma vez:**

```bash
uv run python -m src.cli data \
  --win WIN_2024.csv --win WIN_2025.csv \
  --es ES.csv --wdo WDO.csv \
  --session 09:00-18:00 \
  --out data/frame.npz \
  --holdout-out C:/dados/holdout \
  --calendar-out config/calendario.csv
```

Ela soma um minuto aos timestamps (o MT5 grava a abertura), emenda os contratos
por diferença, marca os dias de rolagem, roda as checagens, separa o hold-out em
outro diretório, escreve o calendário de pregão e imprime o `data_hash` — que é o
que entra no `genesis`. Sem `--force`, ela **recusa** gravar se alguma checagem
falhar.

**Checagens obrigatórias antes de seguir:**

```
- contar barras por pregão: deve bater com o horário de negociação
- procurar buracos: nenhuma sequência de mais de 3 minutos sem barra
  dentro do pregão (fora leilão)
- procurar barras com volume zero
- conferir que os dias de rolagem estão marcados
- conferir alinhamento temporal WIN x ES: o timestamp da barra do ES
  precisa ser o de fechamento, não o de abertura
```

O último item é o que mais silenciosamente estraga tudo. Se o timestamp do ES estiver deslocado em um minuto para trás, o backtest vai "prever" o WIN usando dado do futuro e o Sharpe vai explodir.

**Calendário de pregão:** lista de dias úteis da B3 com feriados, mais os horários de leilão de abertura e fechamento, mais o horário de zeragem compulsória da sua corretora. Arquivo simples, uma linha por dia.

---

## Bloco 2 — Modelo de custo

```python
@dataclass(frozen=True)
class CostModel:
    point_value:      float = 0.20   # R$ por ponto do WIN
    tick_size:        int   = 5      # pontos
    slippage_ticks:   int   = 1      # por perna, pessimista
    exchange_fee:     float = 0.25   # taxas B3 por contrato por lado
    brokerage:        float = 0.00   # mini contratos, corretagem zero
    margin_per_contract: float = 155.0
```

**Confirme os três últimos na tabela vigente antes de rodar qualquer coisa.** Taxas mudam, e um modelo de custo otimista contamina todas as avaliações do projeto.

**Validação:** pegue uma nota de corretagem real de uma operação sua e reproduza o valor com o modelo. Se não bater dentro de um centavo, o modelo está errado.

Se você ainda não operou, faça uma operação de um contrato em conta demo com dados reais e compare. Vale a tarde.

---

## Bloco 3 — O pré-registro

Um arquivo markdown, escrito à mão, hasheado, e que não se edita.

```markdown
# gate-prereg.md — escrito em <data>, hash SHA-256 no livro-razão

## Escopo
Ativo:              WIN (mini índice, B3)
Horizonte-alvo:     2 a 60 minutos de holding
Timeframe:          M1
Capital de estudo:  R$ 5.000
Contratos máximos:  2

## Hold-out sagrado
Fatia:              últimos 6 meses do dataset
Consultas por candidato: 1, irreversível
Registro:           toda consulta entra no livro-razão

## Teto global de tentativas
MAX_TRIALS:         3.000
Observação: escolhido sabendo que o SR* exigido cresce com esse número.

## Triagem de sanidade
MIN_TOTAL_TRADES:         400
MIN_TRADES_PER_PATH:      30
MIN_ACTIVE_DAYS:          120
MAX_TRADE_CONCENTRATION:  0,25

## Cortes estatísticos
PBO máximo:         0,20
DSR mínimo:         0,95
Sinal invertido:    máximo 30% dos caminhos

## Bateria de robustez (conjuntiva, todos precisam passar)
- ruído de ±1 tick, 100 reamostragens
- 3 subperíodos cronológicos, consistência de sinal
- custo a 1,5x e 2,0x
- janelas deslocadas para buckets vizinhos, consistência de sinal

## Validação cruzada
N grupos:           8
k de teste:         2
Caminhos:           28
Embargo:            3x o horizonte do rótulo
Fronteira:          nenhum fold atravessa o fechamento
```

Depois de escrever, rode `sha256sum gate-prereg.md` e guarde o hash. Ele vai no registro zero do livro-razão.

---

## Bloco 4 — Catálogo de famílias

Escrito à mão. É a peça onde o seu conhecimento de mercado entra, e a única onde ele deve entrar.

Para começar você **não precisa das oito**. Duas ou três bem escritas valem mais que oito superficiais. Sugestão de mínimo viável:

```python
FAMILIES = [
  Family(
    name          = "INTERMERCADO_SP500",
    mechanism     = "atraso na transmissão de choque do S&P futuro",
    typical_payer = "market maker que alarga spread e não repassa",
    data_needed   = {"WIN", "ES"},
    horizon_range = (2, 10),
    allowed_ops   = {ZSCORE, RET, LAG, ROLLING_STD, SUB, MUL, IN_WINDOW, REF},
    forbidden_ops = {RANK_TS},
    session_hint  = TimeWindow("10:30", "16:30"),
  ),
  Family(
    name          = "ABERTURA_E_GAP",
    mechanism     = "informação do overnight ainda não precificada",
    typical_payer = "quem precisa executar na abertura",
    data_needed   = {"WIN"},
    horizon_range = (5, 30),
    allowed_ops   = {ZSCORE, RET, LAG, ROLLING_MEAN, ROLLING_STD, SUB, GT, IN_WINDOW,
                     MINUTES_SINCE_OPEN},
    forbidden_ops = {RANK_TS, REF},
    session_hint  = TimeWindow("09:00", "11:00"),
  ),
  Family(
    name          = "REGIME_DE_VOL",
    mechanism     = "mudança de regime altera o comportamento do preço",
    typical_payer = "quem opera com parâmetro fixo",
    data_needed   = {"WIN"},
    horizon_range = (20, 60),
    allowed_ops   = {ZSCORE, ROLLING_STD, EMA, RET, DIV, GT, IN_WINDOW, CLIP},
    forbidden_ops = {RANK_TS, REF},
    session_hint  = None,
  ),
]
```

**Regra ao escrever uma família:** se você não consegue nomear quem paga, ela não entra. Se ela exige dado que você não tem, ela entra marcada como indisponível — serve de lembrete, não de opção.

---

## Bloco 5 — Livro-razão

Criar o banco vazio e gravar o registro zero. Esse registro é a certidão de nascimento do projeto.

> O schema abaixo é o esboço original. O definitivo está em `src/ledger/schema.sql`
> e na SPEC-fase-1 §1.7 (inclui `crashed`, `payload`, `kind` de `kill` e `daily`,
> e hash sobre todas as colunas). O gênesis é gravado por
> `Ledger(path).genesis(data_hash, prereg_sha256, catalog_sha256)`, com o banco
> **fora** do OneDrive.

```sql
CREATE TABLE ledger (
  seq           INTEGER PRIMARY KEY AUTOINCREMENT,
  trial_id      TEXT NOT NULL,
  kind          TEXT NOT NULL,   -- 'genesis' | 'backtest' | 'holdout'
  ast_hash      TEXT,
  structural_sig TEXT,
  data_hash     TEXT NOT NULL,
  config_hash   TEXT NOT NULL,
  verdict       TEXT,
  prev_hash     TEXT NOT NULL,
  row_hash      TEXT NOT NULL,
  ts            TEXT NOT NULL
);
```

Pela linha de comando:

```bash
uv run python -m src.cli genesis --ledger C:/dados/ledger.sqlite \
  --data-hash <o data_hash impresso pelo comando data> \
  --families INTERMERCADO_SP500,ABERTURA_E_GAP,REGIME_DE_VOL
uv run python -m src.cli verify --ledger C:/dados/ledger.sqlite
```

O registro zero carrega:

```
kind        = 'genesis'
data_hash   = sha256 do dataset inteiro
config_hash = sha256 do gate-prereg.md + sha256 do catálogo
prev_hash   = '0' * 64
```

Se algum dia você mudar o dataset ou o pré-registro, o hash não bate mais e você sabe que está em outro projeto.

---

## Bloco 6 — Orçamentos e chave de API

```python
BUDGETS = dict(
    max_attempts_per_formula = 10,   # refinamentos da mesma fórmula
    max_formulas_per_hypothesis = 25,
    max_trials_global = 3_000,       # bate com o pré-registro
    max_proof_iterations = 15,
)
```

Na chave de API, configure um **limite de gasto mensal** no painel do provedor. Não por medo da conta — por medo de um loop com bug rodando a noite inteira e queimando trezentas tentativas no livro-razão sem você saber.

---

## O que NÃO precisa existir para começar

Vale dizer para você não travar:

- **Lean e o agente de prova.** Corta sem dó. As três invariantes de risco provadas à mão valem 80% do benefício, e podem esperar até a Fase 4.
- **As oito famílias.** Três bastam.
- **O gerador de MQL5.** Só faz sentido quando algo passar no gate.
- **O coletor e a telemetria.** Só existem depois que tiver robô operando.
- **O agente de hipótese.** Nas primeiras sessões você pode escrever as hipóteses à mão. Isso inclusive é recomendável: você aprende o formato antes de automatizá-lo.

---

## A ordem de execução no dia zero

1. Exportar e limpar os dados. Rodar as checagens.
2. **Separar o hold-out e guardar em outro diretório.** Antes de qualquer outra coisa.
3. Calibrar e validar o modelo de custo contra uma nota real.
4. Escrever o `gate-prereg.md`. Dormir. Reler no dia seguinte. Hashear.
5. Escrever o catálogo com duas ou três famílias.
6. Criar o livro-razão e gravar o registro zero.
7. Escrever uma hipótese à mão, no formato do schema.
8. Escrever uma fórmula à mão para ela.
9. Rodar o backtest e o gate nessa fórmula única.

O passo 9 é o teste de fumaça do sistema inteiro. Se ele funciona de ponta a ponta com uma fórmula escrita à mão, os agentes só vão acelerar a geração. Se não funciona, nenhum agente vai consertar.

---

## O teste que vale mais que todos os outros

Antes de confiar no gate, calibre-o contra ruído:

```
1. Gere 1.000 séries de preço aleatórias com a mesma volatilidade do WIN
2. Rode a mesma fórmula sobre cada uma
3. Passe os resultados pelo gate
4. Conte quantas recebem ACCEPTED
```

Deve ser menos de 5%. Se for mais, o gate está frouxo e você vai aprovar ruído o projeto inteiro. É o único teste que verifica a coisa que todo o resto depende.
