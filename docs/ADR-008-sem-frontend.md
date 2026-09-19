# ADR-008 — Não haverá frontend

**Status:** decidido
**Contexto:** a plataforma precisa de visibilidade sobre sessões, métricas por
caminho, contador do livro-razão e trajetória da `kill_condition`.

## Decisão

Não construir aplicação web. A visibilidade é entregue por **relatórios HTML
estáticos**, arquivo único, gerados pelo CLI ao fim de cada sessão e pelo coletor ao
fim de cada dia.

## Justificativa

**A fábrica é batch, não interativa.** Ela roda quando você manda, produz um
resultado e termina. Não existe estado vivo a observar em tempo real, nem
concorrência de usuários, nem formulário a preencher. O caso de uso é "abrir o
relatório da sessão de ontem", que um arquivo no disco resolve.

**Um frontend não adiciona validade.** Todo o valor do sistema está no rigor da
validação. Semanas gastas em React são semanas não gastas no motor de DSL, no
modelo de custo ou no teste de calibração do gate — que são as peças de que o
resultado realmente depende.

**Um frontend cria superfície de tentação.** Uma interface com botões convida a
"rodar mais uma vez", "ajustar esse limiar e ver", "testar essa variação". Cada
clique desses é uma tentativa no livro-razão. O atrito do CLI é uma proteção, não
um defeito.

**Relatório estático é auditável e versionável.** O HTML de uma sessão de seis meses
atrás abre igual hoje, sem servidor, sem dependência, sem migração de banco.

## Consequências

- `reports/` gera HTML com CSS embutido, sem build, sem JS além do mínimo para um
  gráfico de linha
- O CLI é a única interface de comando
- Se algum dia a visibilidade interativa virar necessidade real, a resposta é um
  script Streamlit de trinta linhas sobre o SQLite — não uma aplicação

## Quando revisar

Se o projeto passar a ter mais de um operador, ou se as sessões passarem a ser
disparadas por terceiros. Nenhuma das duas está no horizonte.
