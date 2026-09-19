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
- [ ] Cadeia íntegra após 1.000 appends
- [ ] Adulteração via SQL direto é detectada por `verify_chain()`
- [ ] `count()` inclui registros com `crashed=1`
- [ ] Segundo `genesis()` levanta exceção
- [ ] Não existe `delete`, `update` nem `reset` no código

---

## M3 — Motor de backtest

**Spec:** `SPEC-fase-2-validacao.md` seções 2.1 a 2.6

**Aceite:**
- [ ] Nenhum fold atravessa o fechamento do pregão
- [ ] Teste de vazamento sintético: rótulo com lookahead deliberado é detectado
- [ ] Modelo de custo reproduz nota real dentro de um centavo
- [ ] Ordem limitada não preenche ao tocar, só ao atravessar
- [ ] Ordem após cutoff é rejeitada e registrada
- [ ] `run()` grava no livro-razão mesmo quando a avaliação lança exceção

---

## M4 — Gate

**Spec:** `SPEC-fase-2-validacao.md` seções 2.7 a 2.12

**Aceite:**
- [ ] `sr_star(0.60, 2400) ≈ 2.71` e `sr_star(0.60, 10) ≈ 1.22`
- [ ] `GateConfig.load()` falha se o hash do pré-registro não bater com o gênesis
- [ ] Bateria de robustez é conjuntiva e conta como uma tentativa
- [ ] **Menos de 5% de 1.000 estratégias sobre ruído recebe `ACCEPTED`**

> **Marco que importa.** Ao fim de M4 existe um sistema útil sem nenhum agente:
> fórmulas escritas à mão, testadas com rigor superior ao de boa parte das mesas.
> Rode uma sessão manual completa aqui antes de seguir.

---

## M5 — Projeção e orquestrador

**Spec:** `SPEC-fase-3-agentes.md` seções 3.1 a 3.5

Ordem interna: **`test_pesquisa_nunca_ve_metricas` primeiro**, depois `projection`,
depois `router`, depois `budgets`, depois `graph`.

**Aceite:**
- [ ] O teste de projeção passa, incluindo a checagem de string no payload final
- [ ] `route_gate` devolve `abandon` em `FAILED_GATE`, nunca `retry`
- [ ] Replay a partir do log de eventos reproduz a mesma AST
- [ ] `BudgetExhausted` dispara ao atingir o teto global

---

## M6 — Catálogo, bandit e agentes

**Spec:** `SPEC-fase-3-agentes.md` seções 3.6 a 3.11

**Aceite:**
- [ ] `ucb` com os valores da spec escolhe `INTERMERCADO_SP500` no cenário de teste
- [ ] Recompensa nunca usa Sharpe nem PnL
- [ ] Schema de tool use contém apenas os operadores da hipótese atual
- [ ] Hipótese com `who_pays` circular é rejeitada sem consumir tentativa global
- [ ] Auditoria de 50 payloads: nenhum número derivado de backtest
- [ ] `count()` não cresce em retry de `INVALID_AST`

---

## M7 — Transpilador e paridade

**Spec:** `SPEC-fase-3-agentes.md` seção 3.12

**Aceite:**
- [ ] `.mq5` gerado compila no MetaEditor sem warning
- [ ] `OnTick` usa índice 1 e sai cedo quando a barra não fechou
- [ ] Guards rejeitam: após cutoff, preço fora da grade, tamanho acima da margem
- [ ] `transpile` recusa AST cuja `kill_condition.metric` não é instrumentável
- [ ] Paridade < 1e-9 em período com virada de dia e rolagem

---

## M8 — Telemetria, coletor e relatórios

**Spec:** `SPEC-fase-4-operacao.md` · `ADR-008` · `ADR-009`

**Aceite:**
- [ ] O robô grava uma linha por barra, inclusive em dias sem operação
- [ ] `transpile` levanta `NonInstrumentableKillCondition` para métrica desconhecida
- [ ] O coletor detecta CSV com contagem de barras diferente da esperada
- [ ] `evaluate_kill` devolve `INSUFFICIENT` enquanto não há janela completa
- [ ] O cruzamento do limiar grava `kind='kill'` no livro-razão
- [ ] O coletor **não** desliga o robô: só marca e alerta
- [ ] Os três alertas são tipos distintos e disparam por gatilhos distintos
- [ ] Teste de paridade semanal roda sobre dados de produção
- [ ] Relatório de sessão e de monitoramento abrem sem servidor, arquivo único

---

## M9 — Verificação formal (opcional)

Corte sem dó se o fôlego acabar. Ordem:

1. As três invariantes de risco provadas **à mão** em Lean, uma vez. Duas tardes e
   80% do benefício
2. Causalidade e idempotência do motor, também manual
3. Só então o agente automatizado, e só para `VEC_EQ_INC`

Se o passo 3 nunca chegar, tudo bem. Pular 1 e 2 não.

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
