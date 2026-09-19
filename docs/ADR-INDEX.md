# Registros de decisão de arquitetura

Decisões que não devem ser revisitadas sem motivo explícito. Quando uma
instrução nas specs parecer ineficiente, provavelmente uma delas está por trás.

| ADR | Decisão | Status |
|---|---|---|
| [001](#adr-001) | Horizonte-alvo: swing intradiário, não scalp | decidido |
| [002](#adr-002) | Plataforma: MetaTrader 5, não Profit/NTSL | decidido |
| [003](#adr-003) | Motor de backtest em Python até medir | decidido |
| [004](#adr-004) | Teto global de tentativas | **bloqueante — decidir antes da Fase 2** |
| [005](#adr-005) | `purgedcv` como dependência ou reimplementação | aberto |
| [006](#adr-006) | Verificação formal no escopo v1 | aberto |
| [007](#adr-007) | Combinação de múltiplas fórmulas em portfólio | fora do escopo v1 |
| [008](ADR-008-sem-frontend.md) | Não haverá frontend | decidido |
| [009](#adr-009) | Fábrica e robô são dois sistemas separados | decidido |
| [010](#adr-010) | Coordenação por estado compartilhado, sem mensageria | decidido |
| [011](#adr-011) | MCP é protocolo de ferramenta, nunca entre agentes | decidido |

---

## ADR-001 — Horizonte-alvo: swing intradiário

**Decisão:** holding de 2 a 60 minutos, alvos de 50 a 400 pontos. Não scalp.

**Motivo:** o custo de round trip é constante em pontos (~10 pts) independente do
alvo. Com alvo de 15 pontos, a fricção come 40% a 100% do alfa bruto; com 200,
come 5% a 8%. Além disso, as fórmulas que sustentariam scalp dependem de book, e
o Strategy Tester do MT5 não simula book nem guarda histórico dele — seriam
intestáveis.

---

## ADR-002 — Plataforma: MetaTrader 5

**Decisão:** MQL5 sobre MT5, não NTSL sobre Profit.

**Motivo:** linguagem completa, integração com Python pela biblioteca oficial,
Strategy Tester para validar execução. O NTSL é limitado e dificulta o teste de
paridade entre a versão vetorizada e a incremental.

---

## ADR-003 — Motor de backtest em Python

**Decisão:** começar em Python com numpy vetorizado. Reescrever em Rust só se o
gargalo for medido.

**Motivo:** o backtest é o único candidato legítimo a sair do Python, mas
otimizar antes de medir é gastar tempo que deveria ir para o modelo de custo e
para o teste de calibração do gate — que são as peças de que o resultado depende.

---

## ADR-004 — Teto global de tentativas

**Status: aberto e bloqueante.** Precisa ser fixado antes da Fase 2 e escrito em
`gate-prereg.md`.

**Por que bloqueia:** este número entra no cálculo do `SR*` de todas as fórmulas
do projeto. Escolhê-lo depois de ver resultados é escolher o número que faz o
resultado passar.

---

## ADR-005 — `purgedcv` como dependência

**Status: aberto.** Avaliar cobertura de testes e se o ajuste à fronteira de
pregão é possível sem fork.

**Contexto:** `mlfinlab` virou produto pago e fechado. `timeseriescv` não tem
release desde 2018 e tem problemas conhecidos de correção. `purgedcv` é
compatível com scikit-learn, tipada e implementa CPCV, purga, embargo, PSR e DSR.

---

## ADR-006 — Verificação formal no v1

**Status: aberto.** Depende de fôlego após a Fase 3.

**Recomendação:** as três invariantes de risco provadas à mão valem 80% do
benefício em duas tardes. O agente automatizado pode nunca chegar.

---

## ADR-007 — Portfólio de fórmulas

**Status: fora do escopo v1.** Combinar múltiplas fórmulas aprovadas introduz um
segundo nível de seleção, com sua própria contabilidade de tentativas. Uma coisa
de cada vez.

---

## ADR-009 — Fábrica e robô são dois sistemas separados

**Decisão:** a fábrica de pesquisa roda em lotes, offline, quando disparada, e
termina. O robô é um artefato congelado que roda no pregão e não se modifica.
Eles se comunicam apenas por arquivos, lidos em momentos diferentes.

**Motivo:** um robô que se reescreve é um robô cujo comportamento de ontem não se
consegue reconstruir. A separação é o que torna o sistema auditável.

**Consequências:**

- Não existe conexão viva entre os dois. Nem socket, nem API, nem fila.
- O robô não faz chamada de rede nem tem LLM dentro.
- A retroalimentação é assíncrona: robô grava CSV → coletor lê de madrugada →
  livro-razão → próxima sessão da fábrica lê o livro-razão.
- Mudar a estratégia significa gerar um `.mq5` novo e substituir.

**O que isso proíbe explicitamente:** qualquer desenho em que o resultado de uma
operação realimente o agente em tempo real. Isso seria uma máquina de reagir a
ruído.

---

## ADR-010 — Coordenação por estado compartilhado

**Decisão:** o livro-razão em SQLite é o único meio de coordenação entre
componentes. Não há fila de mensagens, barramento de eventos, protocolo
agente-para-agente ou orquestração de contêiner.

**Motivo:** é o padrão que aparece em todos os sistemas comparáveis cujo código é
público — OpenEvolve usa um objeto Python persistido, GigaEvo usa Redis com
controle otimista, RD-Agent encadeia estágios. O padrão tem nome e é dos anos
setenta: *blackboard*. Trabalhadores independentes leem e escrevem num quadro
comum.

**O que isso proíbe explicitamente:** Kafka, RabbitMQ, SQS, Celery, A2A,
Kubernetes, service mesh, e qualquer desenho em que um componente envie uma
mensagem endereçada a outro.

**Escrita serializada, de propósito:** uma única conexão, `PRAGMA
journal_mode=WAL`, uma transação por append. O livro-razão é o único ponto de
contenção do sistema, e ele é intencional — contadores por thread multiplicariam
as falsas descobertas.

---

## ADR-011 — MCP é protocolo de ferramenta

**Decisão:** se MCP for usado, é para expor ferramentas a um agente — Lean,
navegador, banco. Nunca como camada de conversa entre agentes.

**Motivo:** é assim que ele é usado nos sistemas examinados. A relação é
vertical: o agente pede, a ferramenta responde. A confusão é comum e leva a
desenhos que não funcionam.
