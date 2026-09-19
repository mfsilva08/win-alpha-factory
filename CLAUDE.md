# CLAUDE.md — contexto permanente do projeto

Leia este arquivo antes de qualquer tarefa. Ele descreve regras que não podem ser
otimizadas, simplificadas ou "melhoradas". Quando uma instrução aqui parecer
ineficiente, ela provavelmente está protegendo validade estatística, não desempenho.

---

## O que é este projeto

Uma plataforma que gera e valida fórmulas de sinal para um robô de trading de
mini índice (WIN) na B3, operando intradiário via MetaTrader 5.

O gargalo do problema **não é gerar candidatos** — é validá-los. Um loop capaz de
produzir mil fórmulas por dia acoplado a um backtest comum não descobre alfa: ele
minera ruído com eficiência industrial. Toda a arquitetura existe para tornar a
validação rigorosa o bastante para que a geração em escala seja segura.

## Stack

- Python 3.11+, `uv` para dependências
- `numpy` para avaliação vetorizada, `polars` ou `pyarrow` para o armazém Parquet
- `sqlite3` (stdlib) para o livro-razão
- `langgraph` para o grafo de estados do orquestrador
- `pytest` para testes
- Sem frontend. Ver ADR-008.
- Type hints obrigatórios em toda função pública. `mypy --strict` deve passar.

## Estrutura

```
src/
  dsl/          ast.py parser.py canonical.py eval_vectorized.py eval_incremental.py ops.py
                errors.py verdict.py market.py
  ledger/       schema.sql ledger.py
  backtest/     engine.py api_engine.py runner.py metrics.py costs.py errors.py
                (folds, execução e dados rodam na API externa — SPEC-fase-2 §2.0)
  gate/         dsr.py pbo.py robustness.py collapse.py config.py errors.py
  orchestrator/ state.py projection.py router.py graph.py budgets.py events.py
  agents/       hypothesis.py formula.py schemas.py client.py
  codegen/      transpiler.py parity.py telemetry.py templates/robo.mq5.j2
  ops/          coletor.py alerts.py
  reports/      session.py monitor.py templates/
  catalog/      families.py reward.py bandit.py metrics.py
tests/
docs/
data/           (gitignored)
```

---

## As nove regras invioláveis

### R1 — A zona de pesquisa nunca vê métricas

Os agentes de hipótese e de fórmula recebem uma `ResearchView` que **não possui o
campo de métricas**. Não é uma instrução de prompt pedindo para ignorar: o dado não
existe no objeto que é serializado para a API.

O que atravessa é um enum de `Verdict`. Nenhum veredito carrega número.

**Exceções autorizadas, e só estas duas:** `INVALID_AST` e `PROOF_FAILED` podem
devolver detalhe completo (mensagem do parser, goal state do Lean). São verdades
sintáticas e lógicas, não estatísticas.

### R2 — Backtest e codegen não fazem chamada de LLM

Nenhuma, em hipótese alguma. Dois motivos: um modelo que veja métricas pode
reemiti-las depois, furando R1; e o mesmo hash de AST + dados + config precisa
produzir bit a bit o mesmo resultado, ou o replay e a auditoria são impossíveis.

Gerar MQL5 a partir de uma AST tipada é **compilação**, não geração.

### R3 — O roteamento é código, não um agente

As funções em `orchestrator/router.py` são puras e determinísticas. Nunca substitua
por um "agente supervisor". Rodar duas vezes deve dar o mesmo caminho.

### R4 — Write-ahead no livro-razão

Toda avaliação de backtest grava **antes** de retornar, inclusive quando a avaliação
lança exceção. Registros são append-only e encadeados por hash do registro anterior.

Um contador subestimado infla o DSR na direção errada, produzindo mais confiança do
que os dados suportam.

### R5 — Nunca varrer parâmetros dentro do backtest

`for window in range(10, 100)` dentro de uma avaliação registra 90 tentativas como
uma. Cada valor de parâmetro é uma tentativa separada no livro-razão, sem exceção.

### R6 — Limiares do gate vêm de arquivo hasheado

`gate/config.py` lê `docs/gate-prereg.md`, valida o hash contra o registro gênesis do
livro-razão, e falha se não bater. Limiares não são parametrizáveis em tempo de
execução nem por variável de ambiente.

### R7 — Coordenação por estado compartilhado, nunca por mensageria

O livro-razão em SQLite é o único meio de coordenação entre componentes. Não
existe fila, barramento de eventos, protocolo agente-para-agente ou orquestração
de contêiner. Ver `ADR-010`.

Se aparecer Kafka, RabbitMQ, Celery, A2A ou Kubernetes no projeto, é bug de
arquitetura, não melhoria.

### R8 — Fábrica e robô são dois sistemas separados

A fábrica roda em lotes e termina. O robô é congelado e não se modifica. Eles se
comunicam apenas por arquivos, lidos em momentos diferentes. Ver `ADR-009`.

Nenhum desenho em que o resultado de uma operação realimente o agente em tempo
real é aceitável.

### R9 — Assinatura estrutural com parâmetros agrupados

Janelas são mapeadas para buckets de Fibonacci **antes** do hash estrutural. Janela
20 e janela 21 produzem a mesma assinatura e a segunda é rejeitada como `REDUNDANT`.
Sem isso, o agente faz tuning de parâmetro disfarçado de exploração.

---

## O que NUNCA fazer

Esta lista existe porque cada item parece uma melhoria óbvia e destrói o sistema.

| Tentação | Por que destrói |
|---|---|
| Passar o Sharpe ao agente de fórmula "para ajudar" | É exatamente o vazamento que R1 impede. O loop vira descida de gradiente sobre ruído |
| Cachear resultados de backtest por AST, consultável de fora | A zona de pesquisa consegue perguntar "essa já foi avaliada e deu quanto" |
| Tornar `FAILED_GATE` um retry | Passar perto do gate e insistir é a definição de data snooping. `FAILED_GATE` encerra a hipótese |
| Granularizar `FAILED_GATE` em qual teste falhou | Saber qual falhou é informação quantitativa sobre a superfície |
| Paralelizar a escrita no livro-razão | Contadores por thread multiplicam as falsas descobertas |
| Reiniciar o contador "porque teve um bug" | É o erro mais caro possível. Bugs também consumiram tentativas |
| Usar `E[x²] − E[x]²` no avaliador incremental | Acumula erro de arredondamento; use Welford |
| Ler a barra de índice 0 no MQL5 | Barra em formação. Sempre índice 1 |
| Adicionar retry automático que não conta tentativa | Só `INVALID_AST` e `PROOF_FAILED` não contam |
| Preencher ordem limitada quando o preço toca o nível | Só quando atravessa. O modelo é pessimista de propósito |
| Sugerir um dashboard web interativo | Ver ADR-008 |
| Criar fila, broker ou barramento entre componentes | Ver ADR-010. A coordenação é o banco |
| Usar MCP como camada de conversa entre agentes | MCP é protocolo de ferramenta, vertical. Ver ADR-011 |
| Fazer o coletor desligar o robô automaticamente | Ele marca e alerta. Um bug no coletor não pode derrubar estratégia viva |
| Usar prejuízo como gatilho para desligar | O gatilho é a `kill_condition`. Prejuízo é ruído esperado |
| Gerar `.mq5` com `kill_condition` não instrumentável | `transpile` levanta exceção. Ver SPEC-fase-4 §4.2 |
| Omitir linhas da telemetria em dias sem operação | São exatamente os dias que permitem medir decaimento |

---

## Convenções de código

- Dataclasses `frozen=True` para tudo que é dado. Mutabilidade só no `TrialState`.
- Enums para todo vocabulário fechado. Nunca strings soltas.
- Toda função que pode falhar por dado inválido levanta exceção tipada de
  `dsl/errors.py` ou `backtest/errors.py`, nunca retorna `None` silenciosamente.
- Testes com vetores numéricos explícitos, não com mocks. Este projeto é sobre
  números estarem certos.
- Nomes de domínio em português quando são termos de mercado (`rolagem`, `pregão`,
  `zeragem`), em inglês quando são termos técnicos (`ledger`, `verdict`, `fold`).
  Não misture dentro da mesma palavra.

## Como rodar

```bash
uv run pytest                       # tudo
uv run mypy --strict src tests      # tipagem
uv run ruff check src tests         # lint
uv run pytest tests/test_projection.py -v   # o teste mais importante do projeto
uv run python -m src.cli session --hypothesis docs/exemplos/h001.yaml
uv run python -m src.cli report --session <id>
uv run python -m src.cli collect --day 2026-09-15     # o job diário
```

## Onde está o quê

| Preciso de | Está em |
|---|---|
| As regras que não podem ser quebradas | este arquivo |
| A ordem de construção e os critérios de aceite | `BUILD-ORDER.md` |
| DSL e livro-razão | `SPEC-fase-1-fundacao.md` |
| Backtest e gate | `SPEC-fase-2-validacao.md` |
| Orquestrador, agentes e codegen | `SPEC-fase-3-agentes.md` |
| Telemetria, coletor e alertas | `SPEC-fase-4-operacao.md` |
| Por que cada decisão foi tomada | `ADR-INDEX.md` |
| As oito famílias, completas | `catalogo-completo.md` |
| Os limiares, hasheados | `gate-prereg.md` |
| A hipótese do teste de fumaça | `exemplos/h001.yaml` |
| O que fazer antes da primeira linha de código | `dia-zero-checklist.md` |

## Glossário mínimo

| Termo | Significado |
|---|---|
| AST | árvore sintática abstrata; a representação estruturada de uma fórmula |
| assinatura estrutural | hash da AST canônica com janelas em buckets de Fibonacci |
| CPCV | validação cruzada combinatória com purga e embargo |
| DSR | Sharpe deflacionado pelo número de tentativas e pela não-normalidade |
| PBO | probabilidade de overfitting do backtest |
| veredito | enum categórico; única informação que atravessa o firewall |
| tick | menor salto de preço na B3; no WIN vale 5 pontos = R$ 1,00 |
| ponto | unidade de cotação do WIN; vale R$ 0,20 por contrato |
| `kill_condition` | objeto tipado que declara quando a hipótese morre; avaliado pelo coletor, nunca pelo robô |
| `INSTRUMENTABLE_METRICS` | as métricas que o robô sabe gravar; `transpile` recusa qualquer outra |
| blackboard | padrão de coordenação por estado compartilhado, sem mensagens endereçadas |
| coletor | job diário que lê a telemetria, agrega e avalia a `kill_condition` |
