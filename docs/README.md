# Documentação do projeto

## Para quem vai implementar

Leia nesta ordem:

1. **`CLAUDE.md`** — contexto permanente, as nove regras invioláveis e a lista do
   que nunca fazer. Releia antes de cada tarefa.
2. **`BUILD-ORDER.md`** — marcos e critérios de aceite. Não avance sem passar.
3. **`SPEC-fase-1-fundacao.md`** — motor de DSL e livro-razão
4. **`SPEC-fase-2-validacao.md`** — backtest e gate
5. **`SPEC-fase-3-agentes.md`** — orquestrador, agentes e transpilador
6. **`SPEC-fase-4-operacao.md`** — telemetria, coletor e alertas
7. **`ADR-INDEX.md`** — as onze decisões de arquitetura e o motivo de cada uma

## Arquivos que o código lê em tempo de execução

| Arquivo | Lido por | Observação |
|---|---|---|
| `gate-prereg.md` | `gate/config.py` | hasheado no gênesis; recusa se tiver `<<< DECIDIR >>>` |
| `catalogo-completo.md` | referência para `catalog/families.py` | o código vive no `.py`, este é o documento |
| `exemplos/h001.yaml` | CLI, teste de fumaça | a hipótese escrita à mão |

## Para entender o porquê

`plano-desenvolvimento-multiagente-win.md` é o documento de arquitetura: a
justificativa de cada decisão, os benchmarks de mercado que informaram o desenho, e
os riscos mapeados. Ele não é instrução de implementação — é o raciocínio por trás
das specs.

`benchmarks-teoremas-formulas-referencia.md` cobre o estado da arte em verificação
formal e por que ele diverge do nosso problema.

`dia-zero-checklist.md` é o que precisa existir antes da primeira linha de código.

## A frase que resume tudo

O gargalo não é gerar candidatos. É validá-los. Quando uma instrução nas specs
parecer ineficiente, ela provavelmente está protegendo validade estatística, não
desempenho.
