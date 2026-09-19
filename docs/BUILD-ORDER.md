# Ordem de construção

A ordem de construção **não é a ordem do pipeline**. Construir os agentes antes do
rigor produz um sistema empolgante que minera ruído em escala industrial.

Cada marco abaixo tem critério de aceite objetivo. Não avance sem passar.

---

## M0 — Dia zero (antes de qualquer código)

Ver `dia-zero-checklist.md`. O que precisa existir:

- [ ] Barras M1 de WIN, ES e WDO exportadas, 24 meses, série contínua ajustada
- [ ] **Hold-out separado em outro diretório, antes de qualquer outra coisa**
- [ ] Checagens de dados passando (buracos, volume zero, alinhamento WIN×ES)
- [ ] Calendário de pregão com feriados e horário de zeragem
- [ ] `config/costs.yaml` validado contra uma nota de corretagem real
- [ ] `docs/gate-prereg.md` preenchido — **nenhum marcador `<<< DECIDIR >>>` restante** — e hasheado
- [ ] **ADR-004 fechado**: o teto global de tentativas está escrito no prereg
- [ ] Catálogo com três famílias escritas à mão (base em `catalogo-completo.md`)
- [ ] Livro-razão criado com registro gênesis

---

## M1 — Motor de DSL

**Spec:** `SPEC-fase-1-fundacao.md` seções 1.1 a 1.6

Ordem interna: `ops` → `ast` → `parser` → `canonical` → `eval_vectorized` →
`eval_incremental`.

**Aceite:**
- [x] `mypy --strict src/dsl` passa
- [x] Os cinco casos negativos da spec são rejeitados com o veredito correto
- [x] `canonicalize(canonicalize(x)) == canonicalize(x)` em 10.000 ASTs aleatórias
- [x] Assinatura estrutural: 20, 21 e 22 colidem; 55 difere
- [x] Paridade vetorizado/incremental < 1e-9 em 40.000 barras, incluindo virada de
      dia e rolagem (critério `|a−b| / max(1,|a|)`, SPEC-fase-1 §1.6)

---

## M2 — Livro-razão

**Spec:** `SPEC-fase-1-fundacao.md` seção 1.7

**Aceite:**
- [x] Cadeia íntegra após 1.000 appends
- [x] Adulteração via SQL direto é detectada por `verify_chain()`
- [x] `count()` inclui registros com `crashed=1`
- [x] Segundo `genesis()` levanta exceção
- [x] Não existe `delete`, `update` nem `reset` no código

> O registro gênesis **real** só é gravado depois do M0 (dataset e pré-registro
> hasheados). O código do M2 está pronto; o banco do projeto ainda não existe.

---

## M3 — Motor de backtest

**Spec:** `SPEC-fase-2-validacao.md` seções 2.0 a 2.6

O backtest roda numa **API externa** (§2.0). Do lado da fábrica:

- [x] Porta `BacktestEngine`, requisição e contrato de resposta definidos
- [x] `run()` grava no livro-razão mesmo quando a avaliação lança exceção
- [x] Resposta fora do contrato (partições, NaN, dataset, id) vira `crashed`
- [x] `trial_id` repetido recusado antes de chamar o motor
- [x] `CostModel` lido de `config/costs.yaml` e enviado na requisição
- [ ] Adaptador `api_engine.py` implementado — **aguarda o contrato da API**

Do lado da API — viram **testes de contrato**, rodados contra ela quando o
adaptador existir:

- [ ] Nenhum fold atravessa o fechamento do pregão
- [ ] Teste de vazamento sintético: rótulo com lookahead deliberado é detectado
- [ ] Modelo de custo reproduz nota real dentro de um centavo
- [ ] Ordem limitada não preenche ao tocar, só ao atravessar
- [ ] Ordem após cutoff é rejeitada e registrada

---

## M4 — Gate

**Spec:** `SPEC-fase-2-validacao.md` seções 2.7 a 2.12

**Aceite:**
- [x] `sr_star(0.60, 2400) ≈ 2.71` e `sr_star(0.60, 10) ≈ 1.22`
- [x] `GateConfig.load()` falha se o hash do pré-registro não bater com o gênesis
- [x] Bateria de robustez é conjuntiva e conta como uma tentativa
- [x] Colapso com os sete limiares do pré-registro, na ordem da spec
- [x] PBO por CSCV: ~0,5 sobre ruído, ~0 com configuração de fato melhor
- [ ] Decidir quem são as N configurações do PBO (§2.9)
- [ ] Decidir 28 partições × 7 caminhos e implementar a agregação (§2.3)
- [ ] Decidir onde fica o Sharpe de cada tentativa, para o `var_sr` (§2.8)
- [ ] Pré-registro preenchido — hoje o carregador recusa, com razão
- [ ] **Menos de 5% de 1.000 estratégias sobre ruído recebe `ACCEPTED`** —
      bloqueado pelo motor (SPEC-fase-2 §2.0, pergunta 6)

> **Marco que importa.** Ao fim de M4 existe um sistema útil sem nenhum agente:
> fórmulas escritas à mão, testadas com rigor superior ao de boa parte das mesas.
> Rode uma sessão manual completa aqui antes de seguir.

---

## M5 — Projeção e orquestrador

**Spec:** `SPEC-fase-3-agentes.md` seções 3.1 a 3.5

Ordem interna: **`test_pesquisa_nunca_ve_metricas` primeiro**, depois `projection`,
depois `router`, depois `budgets`, depois `graph`.

**Aceite:**
- [x] O teste de projeção passa, incluindo a checagem de string no payload final
- [x] `route_gate` devolve `abandon` em `FAILED_GATE`, nunca `retry`
- [x] Replay a partir do log de eventos reproduz a mesma AST
- [x] `BudgetExhausted` dispara ao atingir o teto global

---

## M6 — Catálogo, bandit e agentes

**Spec:** `SPEC-fase-3-agentes.md` seções 3.6 a 3.11

**Aceite:**
- [x] `ucb` com os valores da spec escolhe `INTERMERCADO_SP500` no cenário de teste
- [x] Recompensa nunca usa Sharpe nem PnL
- [x] Schema de tool use contém apenas os operadores da hipótese atual
- [x] Hipótese com `who_pays` circular é rejeitada sem consumir tentativa global
- [x] Auditoria de 50 payloads: nenhum número derivado de backtest (automatizada)
- [x] `count()` não cresce em retry de `INVALID_AST`
- [ ] Rodada real contra a API da Anthropic — exige credencial configurada

---

## M7 — Transpilador e paridade

**Spec:** `SPEC-fase-3-agentes.md` seção 3.12

**Aceite:**
- [ ] `.mq5` gerado compila no MetaEditor sem warning — **não verificável aqui**
- [x] `OnTick` usa índice 1 e sai cedo quando a barra não fechou
- [x] Guards rejeitam: após cutoff, preço fora da grade, tamanho acima da margem
- [x] `transpile` recusa AST cuja `kill_condition.metric` não é instrumentável
- [x] Paridade < 1e-9 em período com virada de dia e rolagem — contra o robô
      simulado em Python; contra o Strategy Tester, pendente da compilação

---

## M8 — Telemetria, coletor e relatórios

**Spec:** `SPEC-fase-4-operacao.md` · `ADR-008` · `ADR-009`

**Aceite:**
- [x] O robô grava uma linha por barra, inclusive em dias sem operação (template
      + robô simulado; confirmar no Strategy Tester)
- [x] `transpile` levanta `NonInstrumentableKillCondition` para métrica desconhecida
- [x] O coletor detecta CSV com contagem de barras diferente da esperada
- [x] `evaluate_kill` devolve `INSUFFICIENT` enquanto não há janela completa
- [x] O cruzamento do limiar grava `kind='kill'` no livro-razão
- [x] O coletor **não** desliga o robô: só marca e alerta
- [x] Os três alertas são tipos distintos e disparam por gatilhos distintos
- [x] Teste de paridade semanal roda sobre dados de produção (`weekly_parity`;
      testado com telemetria simulada)
- [x] Relatório de sessão e de monitoramento abrem sem servidor, arquivo único

---

## M9 — Verificação formal (opcional)

Corte sem dó se o fôlego acabar. Ordem:

1. As três invariantes de risco provadas **à mão** em Lean, uma vez. Duas tardes e
   80% do benefício
2. Causalidade e idempotência do motor, também manual
3. Só então o agente automatizado, e só para `VEC_EQ_INC`

Se o passo 3 nunca chegar, tudo bem. Pular 1 e 2 não.

**Estado (19/09/2026):** os enunciados dos passos 1 e 2 estão verificados por teste
de propriedade (`tests/test_guards.py`, milhares de entradas geradas com
`hypothesis`), não por prova:

- [x] Nenhuma ordem de abertura após o cutoff ou fora da sessão (propriedade)
- [x] Nenhuma ordem acima da margem disponível (propriedade)
- [x] Todo preço na grade, arredondado contra nós, a menos de um tick (propriedade)
- [x] Causalidade do motor: o sinal em t não muda ao remover as barras após t
      (200 ASTs aleatórias)
- [x] Idempotência da canonicalização (10.000 ASTs, M1)
- [x] O template MQL5 aplica os guards na mesma ordem de `codegen/guards.py`
- [ ] Provas em Lean dos três enunciados de risco — pendente
- [ ] Agente automatizado de prova (`VEC_EQ_INC`) — opcional

---

## Sequência do primeiro uso real

Depois de M4, antes de M5:

1. Usar a hipótese de `exemplos/h001.yaml`, já escrita no formato do schema
2. Usar a fórmula que está comentada no fim daquele arquivo
3. Rodar backtest e gate sobre essa fórmula única
4. Ler o relatório

Isso é o teste de fumaça do sistema inteiro. Se funciona de ponta a ponta com uma
fórmula escrita à mão, os agentes só vão acelerar a geração. Se não funciona, nenhum
agente vai consertar.
