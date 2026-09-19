# gate-prereg.md — pré-registro do projeto

> **Este arquivo é hasheado no registro gênesis do livro-razão.**
> `gate/config.py` lê este arquivo, calcula o sha256 e compara com o gênesis.
> Se não bater, o sistema recusa rodar.
>
> Os campos marcados `<<< DECIDIR >>>` precisam ser preenchidos antes da primeira
> sessão. O carregador **recusa o arquivo** se qualquer marcador desses sobrar.
>
> Alterar qualquer valor aqui depois de o gênesis existir constitui um projeto
> novo, com contador zerado e histórico separado. Não é proibido — é rastreado.

---

## Identificação

```
project_id:   <<< DECIDIR >>>        # ex.: win-intraday-2026
escrito_em:   <<< DECIDIR >>>        # data
autor:        <<< DECIDIR >>>
```

## Escopo

```
ativo:              WIN (mini índice, B3)
timeframe:          M1
horizonte-alvo:     2 a 60 minutos de holding      # ADR-001
capital_estudo:     <<< DECIDIR >>>                # ex.: 5000.00 BRL
contratos_max:      <<< DECIDIR >>>                # ex.: 2
```

## Hold-out sagrado

```
fatia:                   últimos 6 meses do dataset
consultas_por_candidato: 1                          # irreversível
registro:                toda consulta entra no livro-razão com kind='holdout'
diretorio:               <<< DECIDIR >>>            # fora de data/, sem acesso do loader
```

## Teto global de tentativas

```
MAX_TRIALS: <<< DECIDIR >>>
```

> Escolha consciente. Este número entra no cálculo do `SR*` de **todas** as
> fórmulas do projeto. Planejar 5.000 significa nascer com um corte de
> significância alto. Planejar 500 significa poucas sessões.
> Referência, com `var_sr = 0,60` e a fórmula de SPEC-fase-2 §2.8:
> `SR*(500) ≈ 2,36` · `SR*(3000) ≈ 2,75` · `SR*(5000) ≈ 2,86`.

## Triagem de sanidade

```
min_total_trades:         400
min_trades_per_path:      30
min_active_days:          120
max_trade_concentration:  0.25
```

## Cortes estatísticos

```
max_pbo:            0.20
min_dsr:            0.95
max_sign_flip_frac: 0.30
```

## Bateria de robustez

Conjuntiva: todos precisam passar. Conta como **uma** tentativa.

```
ruido:        ±1 tick, 100 reamostragens, exige mediana positiva
subperiodos:  3 fatias cronológicas, exige consistência de sinal
custos:       reroda a 1.5x e 2.0x, exige líquido positivo
parametros:   janelas deslocadas para buckets vizinhos, consistência de sinal
```

## Validação cruzada

```
n_grupos:     8
k_teste:      2
caminhos:     28
embargo_mult: 3.0                      # múltiplo do horizonte do rótulo
fronteira:    nenhum fold atravessa o fechamento do pregão
```

## Condição de parada do projeto

```
parar_se:
  - MAX_TRIALS atingido
  - 3 hipóteses consecutivas encerradas por FAILED_GATE
  - o teste de calibração do gate passar a aprovar mais de 5% de ruído
```
