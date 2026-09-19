# Como começar

## 1. Onde cada coisa vive

```
CLAUDE.md          ← na raiz, carregado automaticamente em toda sessão
docs/              ← specs e ADRs, lidos sob demanda
docs/contexto/     ← racional do projeto. NÃO ler a menos que perguntado:
                     são 94 KB e vão consumir contexto à toa
docs/exemplos/     ← h001.yaml (teste de fumaça) e costs.yaml (template)
config/costs.yaml  ← preencher com as taxas reais antes da Fase 2
src/ tests/        ← ainda não existem; o M1 cria
```

## 2. O que pode começar hoje

O **M1 (motor de DSL) não precisa de dados de mercado.** AST, tipos,
canonicalização e assinatura estrutural são código puro, testáveis com vetores
escritos à mão. Dá para começar enquanto os dados são exportados em paralelo.

O **M2 (livro-razão)** pode ser construído, mas o registro gênesis só pode ser
escrito depois do M0, porque precisa do hash do dataset e do hash do config.

## 3. O que está bloqueado

**ADR-004 — o teto global de tentativas.** Precisa estar escrito em
`docs/gate-prereg.md` antes da Fase 2. Sem ele o gate não tem como calcular o
`SR*`. O arquivo tem oito marcadores `<<< DECIDIR >>>` esperando decisão.

## 4. Primeiro prompt sugerido

> Leia `CLAUDE.md` e `docs/BUILD-ORDER.md`.
>
> Implemente **apenas o marco M1**, seguindo `docs/SPEC-fase-1-fundacao.md`
> seções 1.1 a 1.6. Crie a estrutura `src/dsl/` e `tests/`.
>
> Pare quando os cinco critérios de aceite do M1 passarem. Não avance para o M2.
> Não crie arquivos fora da estrutura declarada no CLAUDE.md.
> Não leia `docs/contexto/` — é racional, não instrução.

## 5. Depois de cada marco

Rode os critérios de aceite antes de seguir. Eles estão em `BUILD-ORDER.md`,
com checkbox. Um marco que não passa não é um marco concluído.

O marco que mais importa é o **M4**: ao final dele o sistema já é útil sem
nenhum agente, e o teste de calibração do gate (menos de 5% de aprovação sobre
ruído) é o único que verifica a coisa de que todo o resto depende.
