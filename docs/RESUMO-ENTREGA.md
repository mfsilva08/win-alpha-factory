# Resumo da entrega — win-alpha-factory

**Data:** 19/09/2026 · **Branch:** `main` · **Commits:** 11 (`07663ff` → `242df79`)
**Estado:** 319 testes passando · `mypy --strict` limpo · `ruff` limpo · ~10 mil linhas

Este documento é para revisão. Ele diz o que foi construído, o que mudou em
relação às specs originais (com o motivo) e o que ainda depende de você.

---

## 1. O que existe hoje, em uma frase por marco

| Marco | Entrega | Situação |
|---|---|---|
| **M0** Dia zero | — | **Seu.** Dados, hold-out, custos, pré-registro, gênesis real |
| **M1** Motor de DSL | tipos, validação, forma canônica, assinaturas, avaliadores vetorizado e incremental | ✅ completo |
| **M2** Livro-razão | SQLite append-only, hash sobre todas as colunas, gatilhos contra UPDATE/DELETE | ✅ completo |
| **M3** Backtest | porta para a sua API, selo com write-ahead, validação da resposta, custos | ✅ do lado da fábrica · ⏳ adaptador da API |
| **M4** Gate | SR*/DSR, pré-registro hasheado, PBO, robustez, colapso | ✅ peças · ⏳ 3 decisões + calibração |
| **M5** Orquestrador | firewall de métricas, roteamento puro, orçamentos, grafo, replay | ✅ completo |
| **M6** Agentes | catálogo, bandit, agentes de hipótese e fórmula, cliente Anthropic | ✅ completo · ⏳ rodada real |
| **M7** Codegen | transpilador AST → MQL5, guards, telemetria, paridade | ✅ completo · ⏳ compilar no MetaEditor |
| **M8** Operação | coletor diário, 3 alertas, relatórios HTML, CLI | ✅ completo |
| **M9** Verificação | invariantes por teste de propriedade | ✅ propriedades · ⏳ provas em Lean |

---

## 2. Arquitetura

### 2.1 Os dois sistemas (ADR-009)

```
┌──────────────── FÁBRICA DE PESQUISA (lote, offline) ─────────────────┐
│                                                                       │
│  catálogo ─► bandit ─► agente de hipótese ─► agente de fórmula        │
│  (humano)    (UCB)       (Claude, via             (Claude, via        │
│                           projeção)                projeção)          │
│                                   │                                   │
│                     ┌─────────────┴─────── FIREWALL (R1) ───────────┐ │
│                     ▼                                               │ │
│    validação DSL ─► runner ──► API de backtest (sua) ─► gate ───────┘ │
│    (tipos, forma    │ write-ahead           ▲             │ veredito  │
│     canônica)       ▼                       │             ▼ categórico│
│               LIVRO-RAZÃO  ◄────────────────┴──── bandit lê vereditos │
│               (SQLite, hash encadeado — único meio de coordenação)    │
│                     │                                                 │
│     ACCEPTED ─► transpilador ─► robo.mq5 + manifest.json              │
└─────────────────────┼─────────────────────────────────────────────────┘
                      │ arquivo
┌─────────────────────▼──── ROBÔ (MT5, congelado) ──────────────────────┐
│  OnTick: índice 1 · estado incremental · guards · 1 linha por barra   │
└─────────────────────┬─────────────────────────────────────────────────┘
                      │ win_AAAAMMDD.csv
┌─────────────────────▼──── COLETOR (cron diário) ──────────────────────┐
│  resumo diário ─► kill_condition ─► alertas ─► relatório monitor.html │
│  grava daily/kill no livro-razão · NUNCA desliga o robô               │
└───────────────────────────────────────────────────────────────────────┘
```

### 2.2 As duas zonas

| Zona | Vê métricas? | Quem está nela |
|---|---|---|
| **Pesquisa** | **Nunca** | agentes de hipótese e fórmula |
| **Verificação** | Sim | runner, API de backtest, gate, relatórios, coletor |

O que atravessa da verificação para a pesquisa é **só o veredito categórico**. O
detalhe completo atravessa só em `INVALID_AST` e `PROOF_FAILED`. O firewall tem
três camadas independentes: a estrutura (`ResearchView` sem campo de métrica), a
serialização (lista fechada de campos) e uma varredura do texto final, que
procura nomes e **números** das métricas. Há uma auditoria automática de 50
payloads.

### 2.3 Módulos

| Pacote | Arquivos | Papel |
|---|---|---|
| `dsl/` | ops, ast, parser, canonical, eval_vectorized, eval_incremental, market, errors, verdict | a linguagem das fórmulas |
| `ledger/` | schema.sql, ledger | o livro-razão |
| `backtest/` | engine, api_engine, runner, metrics, costs, errors | porta da API e selo |
| `gate/` | config, dsr, pbo, robustness, collapse, errors | os cortes estatísticos |
| `orchestrator/` | state, projection, router, budgets, graph, events, session | fluxo de uma tentativa |
| `agents/` | schemas, hypothesis, formula, client | a zona de pesquisa |
| `catalog/` | families, metrics, reward, bandit | artefato humano + escolha de família |
| `codegen/` | transpiler, guards, telemetry, parity, templates/robo.mq5.j2 | AST → MQL5 |
| `ops/` | coletor, alerts | o job diário |
| `reports/` | session, monitor, charts, templates/ | HTML estático |
| `cli.py` | — | genesis · verify · session · report · collect · monitor |

---

## 3. Decisões que tomei e que mudam as specs — **para revisar**

Todas estão escritas nas specs. Nenhuma foi escondida no código.

### Na DSL (M1)
1. **Séries base sempre permitidas** (`close`, `high`...); `ref` continua explícito. Sem isso a h001 era rejeitada pelas próprias regras
2. **`mul` aceita `Bool` dos dois lados**
3. **Profundidade conta arestas** (a h001 tem profundidade 4)
4. **Nó `CONST` interno**, só produzido pela canonicalização
5. **A forma canônica serve só para identificar**, nunca para avaliar
6. **Assinatura estrutural recanonicaliza depois dos buckets**, porque `zscore(x,20)−zscore(x,21)` precisa colapsar
7. **Critério de paridade `|a−b|/max(1,|a|) < 1e-9`**, absoluto até 1 e relativo acima. Motivo medido: `div(Z,Z)` chega a −9.419, e 1e-9 absoluto exigiria mais precisão do que o float64 tem
8. **Semântica de cada operador fixada**: `NaN`, `ema`, `rank_ts`, `in_window` semiaberto, janelas que atravessam dias

### No livro-razão (M2, M6)
9. **`row_hash` sobre todas as colunas**, com gatilhos que recusam UPDATE/DELETE
10. **Novos tipos de registro que não contam tentativa**: `kill`, `daily`, `hypothesis` e `verdict`. O `verdict` resolve uma lacuna: o gate não tinha onde gravar o veredito
11. **O gênesis guarda os hashes do pré-registro e do catálogo separadamente**
12. **`trial_id` único**, porque é o id com que a sua API é chamada
13. **`.gitattributes` fixa LF.** Um CRLF no checkout mudaria o hash do pré-registro

### No backtest (M3) — ADR-012
14. **O backtest roda na sua API.** As regras de simulação viraram contrato da API
15. **A falha levanta `BacktestCrashed` depois de gravar**, em vez de devolver vazio. Uma API fora do ar não pode queimar tentativas em silêncio
16. **AST malformada e id repetido são barrados antes de chamar a API**

### No gate (M4)
17. **DSR em Sharpe por observação**, não anualizado. A spec misturava as duas unidades, o que infla o DSR
18. **Os três limiares esquecidos do pré-registro** viram `INSUFFICIENT_SAMPLE`
19. **Valores de SR* corrigidos no pré-registro** (2,36 / 2,75 / 2,86)

### No orquestrador (M5)
20. **Toda rota de retry confere o orçamento.** O esboço tinha um laço infinito (fórmula válida seguida de prova falha)

### Nos agentes (M6)
21. **Família, id e restrições da DSL vêm do código**, não do modelo
22. **Modelo `claude-opus-5`, com fallback de recusa do servidor ligado** (`fallbacks="default"`). Desligável em `ClientConfig`

### No robô (M7)
23. **`vwap` é recusado**: o MT5 não tem VWAP por barra
24. **`volume` → `real_volume` e `trades` → `tick_volume`**. O exportador de dados precisa usar os mesmos campos
25. **Referência sem barra no minuto → `DATA_GAP`**: o estado não avança e não há ordem

---

## 4. Pendências

### 4.1 Decisões suas — sem elas o gate não fecha

| # | Decisão | Onde | Por que importa |
|---|---|---|---|
| D1 | Preencher os 8 `<<< DECIDIR >>>` do `gate-prereg.md`, principalmente **`MAX_TRIALS`** (ADR-004) | `docs/gate-prereg.md` | Hoje o gate recusa, com razão |
| D2 | **28 partições ou 7 caminhos** do CPCV | SPEC-fase-2 §2.3 | Define `min_trades_per_path`, a inversão de sinal e o PBO |
| D3 | **Onde gravar o Sharpe de cada tentativa** (para o `var_sr`) | SPEC-fase-2 §2.8 | Sem isso não há SR* |
| D4 | **Quem são as N configurações do PBO** | SPEC-fase-2 §2.9 | PBO de uma fórmula sozinha não existe |
| D5 | Qual catálogo entra no gênesis: as 3 iniciais ou as 8 | `catalog/families.py` | Muda o `config_hash` |

Com D2, D3 e D4 decididas, falta implementar a função `aggregate` (partições → `GateInputs`) e injetá-la na sessão. A sessão hoje recusa começar sem ela (`GatePending`), de propósito.

### 4.2 Contrato da API de backtest (M3)
Quando você trouxer o contrato, só `src/backtest/api_engine.py` muda. Perguntas listadas lá e na SPEC-fase-2 §2.0:

1. A fórmula vai na requisição ou o teste já existe na API?
2. Síncrona ou com consulta de status?
3. Métricas **por partição**, com skew, curtose (não excedente), pregões ativos e **Sharpe por operação**?
4. A resposta informa o hash do dataset usado?
5. Aceita custos e regras de execução por requisição?
6. **Aceita dados sintéticos?** O teste de calibração do gate depende disso
7. Guarda a lista dos ids executados (para reconciliar com o livro-razão)?

### 4.3 Dia zero (M0)
- [ ] Exportar WIN, ES e WDO em M1, 24 meses, com série contínua ajustada
- [ ] **Separar o hold-out antes de qualquer outra coisa**
- [ ] Converter o timestamp do MT5 (abertura) para fechamento (+1 min)
- [ ] Calendário de pregão em CSV `day,bars_expected`
- [ ] Conferir `config/costs.yaml` contra uma nota de corretagem real
- [ ] Gravar o gênesis com `python -m src.cli genesis`, com o banco **fora do OneDrive**

### 4.4 Verificações que não dá para fazer aqui
- [ ] **Compilar o `.mq5` no MetaEditor.** Nunca foi compilado; os testes conferem o texto. Espere ajustes de sintaxe na primeira compilação
- [ ] **Paridade contra o Strategy Tester** (`parity_check` com o log do tester)
- [ ] **Rodada real com a API da Anthropic.** Exige credencial (`ANTHROPIC_API_KEY` ou `ant auth login`) e um limite de gasto mensal no painel
- [ ] **Teste de calibração do gate** (menos de 5% de 1.000 estratégias sobre ruído). É o mais importante do projeto e depende da pergunta 6 da API
- [ ] **Provas em Lean** das três invariantes de risco (hoje verificadas por teste de propriedade)

### 4.5 Ambiente
- [ ] O projeto e a `.venv` estão no OneDrive. O banco SQLite e os dados **não podem** ficar lá. Recomendo mover o repositório inteiro para fora
- [ ] Os commits não foram enviados a nenhum remoto; só existem na sua máquina

---

## 5. Como rodar

```bash
python -m uv sync --extra dev --extra agents
python -m uv run pytest                        # 319 testes, ~1 min
python -m uv run mypy --strict src tests
python -m uv run ruff check src tests
python -m uv run pytest tests/test_projection.py -v   # o firewall
python -m uv run python -m src.cli --help
```

O `uv` foi instalado como módulo Python, por isso aparece como `python -m uv`.

---

## 6. Onde olhar primeiro na revisão

1. **`docs/BUILD-ORDER.md`**: cada critério de aceite marcado, com os que faltam
2. **`src/orchestrator/projection.py`** + **`tests/test_projection.py`**: o firewall
3. **`src/backtest/runner.py`**: o write-ahead
4. **`src/codegen/templates/robo.mq5.j2`**: o robô, antes de compilar
5. **Seção 3 deste arquivo**: as decisões que mudaram as specs
