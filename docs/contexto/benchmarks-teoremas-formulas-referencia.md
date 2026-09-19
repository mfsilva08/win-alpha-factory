# Benchmarks de matemática formal e descoberta de fórmulas

**Documento de referência e aprofundamento**
Recorte temporal: março a setembro de 2026
Finalidade: entender como esses sistemas definem e validam resultados, para depois caracterizar com precisão onde eles divergem da nossa necessidade

---

## Como ler este documento

A tese que atravessa tudo o que vem abaixo, e que você vai usar depois:

> **Todo benchmark de matemática formal é construído sobre uma escolha deliberada: escolher problemas onde a verificação é barata e confiável, para que a descoberta possa ser cara.** Essa escolha não é incidental — é o critério de inclusão explícito de vários deles. É exatamente essa escolha que não está disponível para nós.

O documento vai do macro (como a área se organiza) ao micro (o que é, mecanicamente, um item de benchmark e como ele é verificado), e fecha com o mapa de divergência.

---

# PARTE 1 — MACRO: a taxonomia

## 1.1 Cinco famílias, separadas pelo mecanismo de verificação

A literatura costuma separar benchmarks por dificuldade (colegial, universitário, pesquisa). Essa não é a separação útil para o nosso caso. A separação útil é **como o resultado é declarado correto**.

| Família | Mecanismo de verificação | Custo de verificar | Falso positivo possível? | Exemplos |
|---|---|---|---|---|
| **A. Resposta final** | Comparação com gabarito (número, objeto sympy) | Trivial | Sim — acerto por atalho não pretendido | FrontierMath, AIME/HMMT, Humanity's Last Exam |
| **B. Prova formal** | Compilador de prova (Lean, Isabelle, Rocq) | Baixo, segundos a minutos | Não, no nível da prova; **sim, no nível do enunciado** | miniF2F, ProofNet, PutnamBench, FATE, LeanCat, Formal Conjectures |
| **C. Prova informal** | Julgamento humano ou autograder | Alto | Sim | IMO-ProofBench, avaliação oficial da IMO, MathArena |
| **D. Construção verificável** | Validador determinístico que checa se o objeto satisfaz as restrições, ou se bate um recorde publicado | Baixo | Não para a validade; sim para a novidade | HorizonMath, problemas de Erdős formalizados, alvos do AlphaEvolve |
| **E. Descoberta de equação a partir de dados** | Erro numérico em conjunto de teste + equivalência simbólica | Baixo, mas **estatístico** | **Sim, sistematicamente** | LLM-SRBench, SRBench, ERBench |

A família E é a nossa vizinha mais próxima e a única que compartilha o nosso problema estrutural. Voltamos a ela na Parte 4.

## 1.2 O eixo que organiza tudo: custo de descoberta × custo de verificação

O campo inteiro vive num único quadrante deste plano:

```
                    VERIFICAÇÃO BARATA          VERIFICAÇÃO CARA
                 ┌────────────────────────┬────────────────────────┐
  DESCOBERTA     │  miniF2F, AIME         │  (pouco povoado —      │
  FÁCIL          │  já saturados          │   ninguém constrói)    │
                 ├────────────────────────┼────────────────────────┤
  DESCOBERTA     │  PutnamBench           │  Vero (verificação     │
  DIFÍCIL        │  FrontierMath          │  de repositórios)      │
                 │  HorizonMath           │  IMO-ProofBench        │
                 │  Erdős formalizados    │  (julgamento humano)   │
                 │  ← TODO O CAMPO        │                        │
                 └────────────────────────┴────────────────────────┘
```

O HorizonMath torna isso explícito no próprio critério de inclusão: o benchmark mira uma classe de problemas onde a descoberta é difícil, exigindo insight matemático real, mas a **verificação é computacionalmente eficiente e simples**. O pipeline de inclusão tem duas etapas, e a primeira é justamente "avaliação automatizável": a solução precisa ser concreta e verificável por máquina. Problemas que não passam nesse filtro são rejeitados — não por serem menos importantes, mas por serem inavaliáveis.

Essa é a frase mais útil do documento inteiro para o seu argumento. Os benchmarks **selecionam** problemas com verificação barata. Nós não podemos selecionar nosso problema.

## 1.3 A linha do tempo da saturação

O padrão dos últimos dois anos é brutal e consistente: cada benchmark novo vai de "intratável" a "saturado" em 12 a 24 meses.

| Benchmark | Estado inicial | Estado atual | Janela |
|---|---|---|---|
| miniF2F (244 problemas) | ~30% em 2021 | Seed-Prover 99,6% (243/244), restando apenas IMOSL 2007 A6 | ~4 anos |
| PutnamBench (672 em Lean) | Agregado de todos os métodos não chegava a 1% na publicação | Aleph Prover 668/672 | ~2 anos |
| IMO | Prata em 2024 (AlphaProof/AlphaGeometry 2, 4/6, dois a três dias de computação) | Quatro sistemas com 42/42 em 2026 | 2 anos |
| FrontierMath Tier 4 | Top model cobria ~5% no lançamento em meados de 2025 | 97,6% a 98% em setembro de 2026 | 14 meses |

Duas leituras possíveis, e ambas importam para você:

**Leitura otimista:** os sistemas estão de fato ficando muito melhores em raciocínio matemático.

**Leitura cética:** benchmarks com verificação barata e conjunto fechado de itens são, por construção, alvos otimizáveis. A saturação mede tanto a capacidade quanto a otimizabilidade do alvo. É exatamente o que o nosso firewall existe para impedir no nosso domínio.

---

# PARTE 2 — MESO: os resultados mais impactantes (mar–set 2026)

## 2.1 Prova formal — PutnamBench deixa de ser benchmark

O estado do leaderboard de Lean (672 problemas), com os orçamentos de compute, que são a parte que quase todo mundo omite ao citar:

| Sistema | Resolvidos | Orçamento |
|---|---|---|
| Aleph Prover (limite $1400) | 668 | ~$68 por problema em média |
| Aleph Prover (limite $400) | 637 | ~$54 em média |
| Seed-Prover 1.5 (ByteDance) | 581 | 10 dias de H20 por problema |
| Aleph Prover (limite $100) | 500 | ~$23 em média |
| Hilbert | 462 | pass@1840 em média |
| Ax-Prover | 91 | pass@1, ~100 chamadas de ferramenta |
| Goedel-Prover-V2 | 86 | pass@184 |
| DeepSeek-Prover-V2 | 47 | pass@1024 |
| GPT-5 (ReAct, 10 turnos) | 28 | pass@1, 10 chamadas |

A Logical Intelligence, que fez o Aleph Prover, resume a trajetória de forma que vale reter: no verão anterior, os melhores provadores resolviam menos de 2% do PutnamBench; hoje o sistema deles se aproxima da cobertura total.

**Resultados de junho de 2026 em diante:**

- **Goedel-Architect** (arXiv 2606.06468) resolve 11/12 do Putnam 2025, igualando o Seed-Prover 1.5 e ficando sozinho entre os provadores de peso aberto. O **Numina-Lean-Agent** faz 12/12 usando o Claude Opus 4.5 proprietário.
- O Goedel-Architect também formaliza a **USAMO 2026** por conta própria, com auxílio do Claude Opus 4.7, justamente porque ela é posterior ao corte de treino de todos os modelos do pipeline e serve como benchmark livre de contaminação.

**Por que isso importa para você:** repare na coluna de orçamento. Um mesmo benchmark produz números que variam em duas ordens de magnitude conforme o compute. Comparar "668" com "91" sem olhar o orçamento é comparar coisas diferentes. Isso tem um análogo direto no nosso domínio — e lá é pior, porque o "orçamento" não custa só dinheiro, custa validade estatística.

## 2.2 FrontierMath — e a lição embutida na correção v2

A sequência de fatos é mais interessante que o placar.

**12 de junho de 2026:** a Epoch AI publica a v2 do FrontierMath depois de uma auditoria encontrar erros pequenos mas críticos em **42% dos problemas originais**. A correção alterou 123 problemas nos Tiers 1-3 e 12 no Tier 4, e removeu 5 e 7 respectivamente, deixando o conjunto completo com 338 problemas. As pontuações subiram em todo o leaderboard, mas as posições relativas permaneceram em grande parte intactas.

**Setembro de 2026:** a OpenAI reporta 97,6% para o GPT-6 Astra no Tier 4 (v2) corrigido — número da própria empresa, que a Epoch não havia rodado de forma independente até aquele dia. A maior pontuação no hub da própria Epoch naquele momento era 87,8%, para o Claude Fable 5.1 e o Claude Fable 5. Segundo a cobertura, o Astra resolveu o último problema não resolvido do Tier 4, de autoria do combinatorialista Jay Pantone, e — diferente de vários itens anteriores — os matemáticos não relataram exploração de atalho não pretendido.

**A lição:** 42% de itens defeituosos num benchmark construído por mais de 60 matemáticos, incluindo medalhistas Fields. Isso não é desleixo, é a dificuldade intrínseca de escrever especificações corretas. Guarde esse número — ele reaparece na Parte 4.

## 2.3 IMO 2026 — o teto desaparece

Em julho de 2026, quatro sistemas de IA atingiram 42/42 na IMO. O modelo `dots-note-3.0`, da RedNote, foi o primeiro a registrar pontuação perfeita, resolvendo os seis problemas na edição encerrada em Xangai.

Contexto da queda do teto, que é o que importa:

- **2024**: nível prata, 4 de 6 problemas, com dois a três dias de computação e sem limite de tempo do mesmo dia.
- **2025**: Gemini Deep Think e um modelo experimental da OpenAI, ambos com exatamente 35/42 — o corte de ouro daquele ano — cada um resolvendo cinco de seis. O Gemini operou fim a fim em linguagem natural, produzindo provas rigorosas a partir dos enunciados oficiais, dentro do limite de 4h30.
- **2026**: pontuação perfeita, múltiplos sistemas.

**O detalhe metodológico que se repete:** só a submissão da DeepMind em 2025 foi corrigida por coordenadores oficiais da IMO. A da OpenAI foi avaliada por três ex-medalhistas em consenso unânime — crível, mas fora do canal oficial. E o presidente da IMO, Gregor Dolinar, sinalizou o limite do que a confirmação oficial podia cobrir: os organizadores não tinham como verificar quanto poder computacional os modelos haviam usado.

**Isso é diretamente análogo ao nosso problema.** "O resultado é válido" e "o processo que gerou o resultado é auditável" são afirmações diferentes. No nosso caso, o processo que gerou o resultado *é* a variável que determina se ele é válido.

## 2.4 Matemática de pesquisa — o salto de 2026

Aqui está o resultado mais impactante da janela, e é o mais relevante para nós.

**DeepMind, maio de 2026** (arXiv 2605.22763, "Advancing Mathematics Research with AI-Driven Formal Proof Search"):

- Agente mais capaz resolveu autonomamente **9 de 353 problemas de Erdős abertos**, a um custo de inferência de algumas centenas de dólares por problema, incluindo duas questões abertas havia 56 anos.
- Provou **44 de 492 conjecturas abertas** da OEIS (Online Encyclopedia of Integer Sequences).
- Resolveu uma questão aberta de 15 anos sobre funções de Hilbert em geometria algébrica.
- Melhorou um limite aberto em otimização convexa **descobrindo um novo cronograma de parâmetros algorítmico**.
- Identificou várias má-formalizações na literatura.

Dois detalhes metodológicos que merecem atenção:

1. **Eles não escolheram os problemas.** O conjunto foi determinado inteiramente pelo que a comunidade open-source havia formalizado dos 1200+ catalogados no site ErdosProblems — 353 em Lean no repositório Formal Conjectures em fevereiro de 2026. Os próprios autores reconhecem o viés: o conjunto pende para problemas amenos à formalização em Lean.
2. **Um agente básico alternando geração por LLM com verificação em Lean replicou os sucessos de Erdős**, mas ficou mais caro nos problemas difíceis. Ou seja: a arquitetura sofisticada compra eficiência, não capacidade.

**OpenAI, 20 de maio de 2026:** um modelo interno produziu um contraexemplo para o problema da **distância unitária** de Erdős, de 1946. Por quase oito décadas se acreditava que arranjos parecidos com grades quadradas eram essencialmente o melhor possível; o modelo descobriu uma família infinita de configurações produzindo polinomialmente mais distâncias unitárias, conectando geometria elementar a ideias de teoria algébrica dos números. Matemáticos humanos melhoraram substancialmente o resultado em semanas.

**Janeiro de 2026:** o GPT-5.2 Pro gerou provas originais para os problemas de Erdős #397, #728 e #729, formalizadas em Lean e aceitas por Terence Tao, que chamou um resultado relacionado de o caso mais inequívoco até então de IA resolvendo um problema aberto. O #728 saiu via GPT-5.2 Pro combinado com o sistema de verificação formal Aristotle, da Harmonic.

**Escala do esforço:** em janeiro, uma equipe de 24 pesquisadores liderada pela DeepMind resolveu quatro problemas e encontrou soluções antigas e esquecidas para outros nove, usando Gemini para avaliar sistematicamente 700 conjecturas marcadas como abertas na base de Bloom. Em maio, uma equipe separada de 21 pesquisadores anunciou os 9 de 353.

**O ponto para nós:** note o que todos esses casos têm em comum. **O resultado é verificado por um procedimento que não depende de dados futuros.** Uma prova em Lean é verdadeira hoje e continuará verdadeira em 2030. Nenhum desses resultados tem meia-vida.

## 2.5 HorizonMath — o benchmark mais próximo do nosso problema, e ainda assim distante

Publicado em 16 de março de 2026 (arXiv 2603.15617). É a peça mais relevante da janela para o seu argumento, porque é o benchmark que mais conscientemente tenta medir descoberta genuína.

**Desenho:** mais de 100 problemas predominantemente não resolvidos em 8 domínios de matemática computacional e aplicada, com framework de avaliação open-source para verificação automática.

**Os quatro princípios de desenho:**

1. Cada problema exige resposta explícita na forma de um **objeto matemático definido** — número, polinômio, conjunto, grafo — e não uma prova em linguagem natural.
2. Essa resposta precisa ser objetivamente verificável por **procedimento computacional determinístico simples**: comparação numérica contra referência de alta precisão, checagem de melhoria sobre baseline publicado, ou validação de que uma construção satisfaz todas as restrições exigidas.
3. Descoberta difícil, verificação fácil (o quadrante da seção 1.2).
4. Imune a contaminação **por construção**, já que as soluções são desconhecidas.

**Composição do conjunto**, por tipo de artefato de saída: constante (54), construção (39), função (5), **descoberta de fórmula (3)**. Por modo de avaliação: ground truth computável (59), melhor conhecido em benchmark (33), nova construção (9).

**Resultados:** a maioria dos modelos do estado da arte pontua perto de 0%. O GPT-5.4 Pro chega a 7%. O baseline humano corresponde a dez problemas resolvidos. Dois problemas receberam do GPT-5.4 Pro soluções que melhoram os melhores resultados publicados, representando contribuições potencialmente novas, pendentes de revisão por especialistas.

**Por que é relevante e por que ainda diverge:** o HorizonMath tem apenas **3 problemas** classificados como descoberta de fórmula, e mesmo esses são verificados contra um ground truth computável ou um melhor-conhecido publicado. Nós não temos nem um nem outro.

## 2.6 A contracorrente — os papers que atacam a validade dos benchmarks

Esta subseção é a mais valiosa para o seu argumento, porque mostra que **mesmo com verificador sound, o benchmark pode mentir**.

**"Faults in Our Formal Benchmarking" (arXiv 2606.29493, junho de 2026).** Documenta defeitos de dataset e falhas de avaliação em prova de teoremas em Lean. Dois achados centrais:

- **A brecha do `sorry`.** O `sorry` do Lean é amplamente usado como placeholder na criação de benchmarks, mas tem um efeito colateral crítico: ele **adiciona o enunciado ao ambiente como axioma**, o que significa que qualquer código posterior pode referenciá-lo como fato provado. Um provador treinado por RL pode explorar isso citando o enunciado admitido por `sorry` em vez de construir uma prova genuína. A recomendação é usar `proof_wanted`, introduzido no Lean 4, que declara a assinatura do teorema sem adicioná-lo ao ambiente — e que também impede `native_decide` e outras táticas que contornam o kernel, já que nenhum termo de prova é construído.
- **Teoremas vacuosos.** Um enunciado com hipóteses contraditórias (`x < 0` e `x > 0`) é demonstrável por contradição e não prova absolutamente nada. O compilador aceita, e o placar sobe.

**"A Case Study on Emergent Cheating and Whistleblowing in Autonomous Research Swarms" (arXiv 2609.04170, setembro de 2026).** Rodou 71 problemas do Formal Conjectures com um pipeline de avaliação de três checagens sequenciais: blacklist de palavras-chave (`axiom`, `sorry`, `macro`, `syntax`), casamento de string em nível de byte garantindo que o código fora dos marcadores editáveis não foi modificado, e compilação Lean exigindo código de saída 0 e zero declarações de `sorry`. O fato de esse pipeline ser necessário já diz o suficiente.

**"Do LLMs Game Formalization?" (arXiv 2604.19459, workshop VerifAI-2 na ICLR 2026).** Contrapeso honesto: avaliando GPT-5 e DeepSeek-R1 em 303 problemas de lógica de primeira ordem, com taxas de compilação de 87% a 99%, **não** encontraram evidência de gaming sistemático na geração unificada — os modelos preferem reportar falha a forçar provas, mesmo sob pressão de prompt. O risco existe estruturalmente, mas não se manifestou nesse recorte.

**"Vero" (arXiv 2608.13522, agosto de 2026).** Benchmark de repositórios formalmente verificados, com mecanismo de auditoria formal e salvaguardas anti-trapaça que impedem reward hacking por injeção de axioma ou edição de definições. Observa que benchmarks anteriores em escala de repositório não tinham medidas anti-cheating, deixando em aberto se os resultados refletiam capacidade genuína ou reward hacking.

**"miniF2F-Lean Revisited" (arXiv 2511.03108).** A revisão do miniF2F corrigiu dezesseis enunciados que eram **improváveis** no benchmark original, e reverteu simplificações. O resultado: queda de 11,2% de acurácia para o DeepSeek-Prover-V2-7B na versão mais difícil.

---

# PARTE 3 — MICRO: a anatomia de um item de benchmark

Aqui descemos ao nível de peça. Um benchmark de matemática formal tem seis componentes, e cada um tem um modo de falha próprio.

## 3.1 O enunciado (statement)

**O que é:** a formalização do problema na linguagem do assistente de prova.

```lean
theorem putnam_1988_b1 (a : ℤ) (ha : a ≥ 1) :
    ∃ x y z : ℤ, x ≥ 2 ∧ y ≥ 2 ∧ z ≥ 2 ∧ a = x*y - z ∧ ...
```

**O que pode dar errado:**

| Defeito | Efeito | Detecção |
|---|---|---|
| Má-formalização | O sistema prova algo diferente do problema original | Revisão humana; o agente da DeepMind encontrou várias na literatura |
| Hipóteses contraditórias | Teorema vacuosamente verdadeiro; prova trivial | Checagem automatizada de satisfatibilidade |
| Enunciado improvável | Ninguém pode resolver; deprime o placar artificialmente | Correção manual — 16 casos no miniF2F |
| Simplificação excessiva | Benchmark mais fácil que o problema real | Comparação com o enunciado informal |

**O paralelo direto no nosso sistema:** a `Hypothesis` com `who_pays` e `kill_condition` é o nosso enunciado. As mesmas quatro falhas se aplicam, e as três primeiras são mais difíceis de detectar porque não temos compilador que reclame.

## 3.2 O verificador

**O que é:** o kernel do assistente de prova. No Lean, um núcleo pequeno que checa o termo de prova contra as regras de tipagem.

**O que ele certifica:** que o termo de prova estabelece o enunciado, sob os axiomas do ambiente.

**O que ele não certifica** — e essa lista é a mais importante do documento:

1. **Que o enunciado corresponde ao problema pretendido.** O kernel não sabe o que você queria dizer.
2. **Que o ambiente não foi contaminado.** Um `sorry` anterior vira axioma utilizável.
3. **Que nenhum axioma novo foi introduzido.** Mathlib depende de `propext`, `Quot.sound` e `Classical.choice`; problemas de benchmark não deveriam introduzir mais nada.
4. **Que táticas que contornam o kernel não foram usadas.** `native_decide` terceiriza a confiança para o compilador.

Ou seja: **a soundness do Lean é uma propriedade do kernel, não do benchmark.** É por isso que o revisor do nosso agente de prova bloqueia `sorry`, `axiom` novo, enunciado modificado e `native_decide` — não é paranoia, é o estado da arte em anti-gaming formal.

## 3.3 O protocolo de amostragem

**O que é:** quantas tentativas o sistema tem, e quanto compute cada uma custa.

Vocabulário:

- **pass@1** — uma tentativa, uma resposta. O mais honesto.
- **pass@k** — k tentativas, conta se alguma acertar. Infla com k.
- **Orçamento em dólares** — o que o Aleph Prover reporta (limites de $100, $400, $1400). Mais informativo que pass@k, porque captura o custo real.
- **Orçamento em tempo de GPU** — o Seed-Prover 1.5 reporta 10 dias de H20 por problema.

**O problema metodológico:** comparar 668 (pass não especificado, $68/problema) com 86 (pass@184) com 91 (pass@1, ~100 chamadas de ferramenta) é comparar grandezas incomensuráveis. O leaderboard do PutnamBench lista o orçamento justamente por isso, e ainda assim a maioria das citações na imprensa omite a coluna.

**O paralelo no nosso sistema:** pass@k *é* o contador de tentativas. A diferença é que em prova de teoremas o pass@184 só custa dinheiro; no nosso caso ele custa significância estatística. **O Deflated Sharpe Ratio é, conceitualmente, a correção de pass@k que a comunidade de prova formal não precisa fazer.**

## 3.4 As salvaguardas anti-gaming

Estado da arte, tal como implementado nos trabalhos recentes:

```
1. proof_wanted em vez de sorry           (impede que o enunciado vire axioma)
2. Blacklist: axiom, sorry, macro, syntax (varredura estática)
3. Diff em nível de byte                  (garante que só a região editável mudou)
4. Compilação com exit code 0 e zero sorry
5. Checagem de preservação de enunciado   (o teorema provado é o proposto?)
6. Auditoria de axiomas do ambiente       (só os três de Mathlib)
```

**Mapeamento para o nosso sistema**, e é quase um para um:

| Salvaguarda formal | Equivalente na nossa arquitetura |
|---|---|
| `proof_wanted` em vez de `sorry` | Projeção de visão: o dado não está no contexto, não basta pedir para ignorar |
| Blacklist estática | Gramática restrita com `enum` no schema de tool use |
| Diff em nível de byte | Hash da AST canônica no livro-razão |
| Preservação de enunciado | Validação da AST contra as `Constraints` da hipótese |
| Auditoria de axiomas | Proibições explícitas do agente de backtest |

## 3.5 A métrica

Duas famílias:

- **Contagem de resolvidos** — usada em prova formal. Simples, mas ignora custo.
- **Custo por resolução** — o Aleph Prover reporta média de $68 por problema no limite de $1400. A DeepMind reporta "algumas centenas de dólares por problema" nos Erdős.

A segunda está virando padrão, e é um avanço real de honestidade metodológica.

## 3.6 O controle de contaminação

Quatro estratégias em uso, em ordem crescente de robustez:

1. **Problemas frescos** — MathArena avalia em problemas de competição recém-lançados para reduzir risco de contaminação.
2. **Posteriores ao corte de treino** — o Goedel-Architect formaliza a USAMO 2026 por ser posterior ao corte de todos os modelos do pipeline.
3. **Transformação de problemas conhecidos** — o LSR-Transform do LLM-SRBench transforma modelos físicos comuns em representações matemáticas menos comuns, para testar raciocínio além da memorização.
4. **Problemas não resolvidos** — o HorizonMath é imune a contaminação por construção, porque as soluções não existem.

**No nosso caso a contaminação tem forma diferente e mais perversa.** Não é o modelo ter visto a resposta no treino. É o *loop* ter visto os dados antes, e o contador de tentativas ser a única coisa que registra isso. Por isso o hold-out sagrado e o livro-razão são a nossa versão da estratégia 4.

## 3.7 Um item, do começo ao fim

Percurso completo de um problema em PutnamBench:

```
1. Problema original em linguagem natural (Putnam 1988 B1)
         ↓  formalização manual, verificada à mão
2. Enunciado em Lean 4, com sorry no lugar da prova
         ↓  o sistema recebe só o enunciado
3. Proposer gera código Lean candidato
         ↓
4. lake build compila; goal states nos pontos de sorry voltam como feedback
         ↓  itera até fechar ou estourar orçamento
5. Reviewer confere: sem sorry, sem axiom novo, enunciado preservado
         ↓
6. Registro no leaderboard: resolvido / não resolvido, + orçamento gasto
```

Compare com o nosso percurso. As etapas 1, 3 e 5 têm equivalente direto. **A etapa 4 é a que não tem** — e é a etapa que faz todo o resto funcionar.

---

# PARTE 4 — O MAPA DE DIVERGÊNCIA

Esta é a parte que você vai usar. Cinco divergências, em ordem de importância.

## D1 — O verificador deles decide; o nosso estima

| | Prova formal | Nosso backtest |
|---|---|---|
| Saída | Booleano | Distribuição de métricas |
| Erro tipo I | Zero, no nível do termo de prova | Alto e sistemático |
| Repetição | Idêntica sempre | Idêntica só se o pipeline for determinístico |
| Interpretação | "É verdade" | "É compatível com o passado observado" |

**Consequência arquitetural:** eles podem devolver o goal state completo ao gerador a cada iteração, porque não existe gradiente a ser explorado sobre uma verdade lógica. Nós não podemos, porque existe.

## D2 — A iteração deles custa dinheiro; a nossa custa validade

O Aleph Prover gasta ~$68 por problema. Se gastasse $680, o resultado seria mais caro e igualmente válido. **Não existe penalidade estatística por tentar mais vezes numa prova.**

No nosso caso, o limiar de significância cresce com o número de tentativas, via `SR*`. Duplicar o número de tentativas não deixa o resultado mais caro — deixa o resultado **menos provável de ser real**.

Esta é a divergência mais fundamental e a que justifica sozinha o livro-razão global.

## D3 — Teoremas não decaem; alfas decaem

Um teorema provado em 2026 continua verdadeiro indefinidamente. Nenhum dos resultados da Parte 2 tem meia-vida.

Um fator de trading tem meia-vida por dois mecanismos distintos: **overfitting** (nunca foi real) e **crowding** (era real e foi arbitrado). O segundo é o que torna o `kill_condition` obrigatório na nossa `Hypothesis` — não existe equivalente disso em nenhum benchmark formal, porque o conceito não faz sentido lá.

## D4 — Eles têm ground truth ou melhor-conhecido; nós não temos nenhum dos dois

Os modos de avaliação do HorizonMath: ground truth computável (59 problemas), melhor conhecido em benchmark (33), nova construção validada por restrições (9).

Nós não temos:

- **Ground truth** — não existe a "fórmula correta" para o WIN.
- **Melhor conhecido** — não existe registro público de qual é o melhor Sharpe alcançável em mini índice intradiário.
- **Restrições que definam corretude** — uma fórmula pode satisfazer todas as restrições estruturais e ainda assim ser ruído.

O melhor que conseguimos é o modo 3 do HorizonMath enfraquecido: validamos que a construção satisfaz restrições, mas as restrições não implicam corretude, só plausibilidade.

## D5 — Nenhum adversário arbitra um teorema

O problema da distância unitária não fica mais difícil porque alguém o resolveu. O mercado, sim: cada participante que descobre a mesma ineficiência reduz o retorno dela para todos os outros.

Isso significa que **o nosso benchmark é adversarial e não-estacionário**, duas propriedades que nenhum benchmark da Parte 2 tem.

---

## 4.6 A honestidade que fortalece o argumento

Um cuidado, porque o argumento fica mais forte com ele e mais fraco sem.

**Não é verdade que os benchmarks formais sejam perfeitos e o nosso seja o único problemático.** A Parte 2.6 mostra o contrário: o `sorry` que vira axioma, os teoremas vacuosos, os 16 enunciados improváveis do miniF2F, os 42% de itens defeituosos do FrontierMath. Esses sistemas têm um verificador sound e mesmo assim precisam de blacklist, diff em nível de byte e auditoria de axiomas.

A formulação correta da divergência não é "eles têm verificação confiável e nós não". É:

> **Eles enfrentam falha de especificação com um verificador sound. Nós enfrentamos falha de especificação com um verificador ruidoso.** O problema deles é um subconjunto do nosso, e as defesas que eles desenvolveram são necessárias mas não suficientes para nós.

Isso é mais forte porque (a) é verdade, (b) justifica copiar as defesas deles, e (c) explica por que precisamos de camadas adicionais — firewall, contador global, DSR, hold-out — que eles não precisam.

## 4.7 Tabela resumo: o que transportar

| Mecanismo do benchmark formal | Transporta? | Forma no nosso sistema |
|---|---|---|
| Verificação por compilador | Parcial | Prova em Lean das invariantes de risco (Classe A) |
| Feedback rico por iteração | **Não** | Substituído por veredito categórico |
| pass@k como métrica | **Não** | Substituído pelo contador global + DSR |
| Orçamento explícito por problema | Sim | Orçamentos em três níveis |
| Custo por resolução como métrica | Sim | Métrica secundária do projeto |
| `proof_wanted` em vez de `sorry` | Sim, por analogia | Projeção de visão |
| Blacklist estática | Sim | Gramática restrita com enum |
| Preservação de enunciado | Sim | Validação da AST contra `Constraints` |
| Diff em nível de byte | Sim, por analogia | Hash da AST no livro-razão |
| Problemas posteriores ao corte | Sim | Hold-out sagrado |
| Ground truth / melhor-conhecido | **Não existe** | Nada substitui — daí o DSR |
| Imunidade a contaminação por construção | **Não** | Mitigado pelo hold-out de uso único |

---

# PARTE 5 — Referências

## Benchmarks

| Nome | Domínio | Referência |
|---|---|---|
| miniF2F | Competição colegial, Lean/Isabelle/Coq | Zheng et al., 2021; revisão em arXiv 2511.03108 |
| PutnamBench | Competição universitária, 672 em Lean | Tsoukalas et al., 2024 · <https://trishullab.github.io/PutnamBench/> |
| FrontierMath | Pesquisa, resposta final, 338 problemas (v2) | Epoch AI · <https://epoch.ai/frontiermath/tiers-1-4> |
| HorizonMath | Problemas abertos, verificação automática | arXiv 2603.15617 · <https://github.com/ewang26/HorizonMath> |
| Formal Conjectures | Conjecturas formalizadas (1029 abertas, 836 resolvidas) | arXiv 2605.13171 |
| IMO-Bench | AnswerBench, ProofBench, GradingBench, LeanProofBench | <https://imobench.github.io/> |
| MathArena | Problemas de competição frescos, anticontaminação | Balunović et al., 2025 |
| LLM-SRBench | Descoberta de equação, 239 problemas, 4 domínios | ICML 2025 Oral · <https://github.com/deep-symbolic-mathematics/llm-srbench> |
| Vero | Repositórios formalmente verificados, anti-cheating | arXiv 2608.13522 |
| ERBench | Algoritmos de descoberta de equação | arXiv 2606.09276 |

## Sistemas

| Nome | Resultado de referência | Fonte |
|---|---|---|
| Aleph Prover (Logical Intelligence) | 668/672 PutnamBench, ~$68/problema | <https://logicalintelligence.com/blog/aleph-prover-tops-leading-benchmarks> |
| Seed-Prover 1.5 (ByteDance) | 581 PutnamBench; miniF2F 99,6% | Chen et al., 2025 |
| Goedel-Architect | 11/12 Putnam 2025, melhor de peso aberto | arXiv 2606.06468 |
| Goedel-Prover-V2 | 86 PutnamBench pass@184; miniF2F 90,4% | arXiv 2508.03613 |
| Hilbert | 462 PutnamBench, ~70% | arXiv 2509.22819 |
| AxProverBase | 54,7% PutnamBench pass@1 | arXiv 2602.24273 |
| Agente de prova formal da DeepMind | 9/353 Erdős, 44/492 OEIS | arXiv 2605.22763 |
| Gemini Deep Think | Ouro certificado na IMO 2025 (35/42) | DeepMind |
| dots-note-3.0 (RedNote) | Primeiro 42/42 na IMO 2026 | SCMP, jul/2026 |

## Metodologia e crítica

- **"Faults in Our Formal Benchmarking"** — arXiv 2606.29493. Brecha do `sorry`, teoremas vacuosos, recomendação de `proof_wanted`.
- **"Do LLMs Game Formalization?"** — arXiv 2604.19459 (VerifAI-2, ICLR 2026). Contrapeso: sem evidência de gaming sistemático em 303 problemas de FOL.
- **"A Case Study on Emergent Cheating and Whistleblowing in Autonomous Research Swarms"** — arXiv 2609.04170.
- **"From Solvers to Research"** — arXiv 2607.07779. Survey do estado da arte.
- **"Why the Legendary Erdős Problems Are Falling to AI"** — Quanta Magazine, 03/08/2026.

## Repositórios

```
https://github.com/TrishulLab/PutnamBench
https://github.com/ewang26/HorizonMath
https://github.com/deep-symbolic-mathematics/llm-srbench
https://github.com/Goedel-LM/Goedel-Prover-V2
https://github.com/Axiomatic-AI/ax-prover-base
```

---

## Nota sobre o recorte temporal

Este documento cobre março a setembro de 2026. A área está se movendo rápido o suficiente para que a tabela de resultados da Parte 2 esteja parcialmente desatualizada em três meses. As **Partes 1, 3 e 4 não dependem dos números** — dependem dos mecanismos, que mudam devagar. Se for reutilizar este material depois, revalide os placares e mantenha a estrutura.
