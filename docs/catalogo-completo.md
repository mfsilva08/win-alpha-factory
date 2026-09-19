# Catálogo completo de famílias

Artefato humano. O sistema nunca cria, altera ou remove uma família.
Acrescentar uma é decisão consciente e registrada, e muda o hash do config.

Para a primeira sessão, três famílias bastam. Comece por `INTERMERCADO_SP500`,
`ABERTURA_E_GAP` e `REGIME_DE_VOL`.

---

## Regras para escrever uma família

1. Se não conseguir nomear **quem paga** e por qual motivo econômico, não entra.
2. Se exigir dado que você não tem, entra com `available=False` — serve de
   lembrete do que não dá para testar, não de opção para o bandit.
3. `allowed_ops` deve ser o **menor** conjunto que permite expressar o mecanismo.
   Cada operador a mais amplia o espaço de busca e encarece o limiar de todas as
   fórmulas futuras.
4. `horizon_range` vem do mecanismo, não do que seria conveniente.

---

## As oito famílias

| Nome | Mecanismo | Quem paga | Dados | Horizonte | Disponível |
|---|---|---|---|---|---|
| `ABERTURA_E_GAP` | informação do overnight ainda não precificada | quem precisa executar na abertura | WIN M1 | 5–30 min | sim |
| `FLUXO_DE_AGRESSAO` | desequilíbrio entre agressores de compra e venda | market maker | **book** | 1–5 min | **não** |
| `INTERMERCADO_SP500` | atraso na transmissão de choque do S&P futuro | market maker que alarga spread e não repassa | WIN + ES | 2–10 min | sim |
| `INTERMERCADO_DOLAR` | fluxo estrangeiro move o dólar antes do índice | hedger local | WIN + WDO | 5–20 min | sim |
| `REVERSAO_HORARIA` | exagero em horários de baixa liquidez | retail alavancado | WIN M1 | 10–40 min | sim |
| `REGIME_DE_VOL` | mudança de regime altera o comportamento do preço | quem opera com parâmetro fixo | WIN M1 | 20–60 min | sim |
| `LIQUIDEZ_E_HORARIO` | spread e profundidade variam previsivelmente no dia | quem executa no horário errado | WIN M1 | 5–20 min | sim |
| `ROLAGEM_DE_VENCIMENTO` | distorção na virada do contrato | quem rola tarde | WIN M1 | 1–3 dias | sim |

> `FLUXO_DE_AGRESSAO` fica com `available=False`: o mecanismo é plausível, mas
> exige histórico de book de ofertas e o MetaTrader 5 não guarda isso. O bandit
> nunca a escolhe. Ela existe para que a ausência seja explícita.

---

## Definições completas

```python
from datetime import time

FAMILIES = [
  Family(
    name          = "INTERMERCADO_SP500",
    mechanism     = "atraso na transmissão de choque do S&P futuro",
    typical_payer = "market maker que alarga spread na incerteza e não repassa",
    data_needed   = frozenset({"WIN", "ES"}),
    horizon_range = (2, 10),
    allowed_ops   = frozenset({ZSCORE, RET, LAG, ROLLING_STD, SUB, MUL,
                               IN_WINDOW, REF}),
    forbidden_ops = frozenset({RANK_TS}),
    session_hint  = (time(10,30), time(16,30)),
    available     = True,
  ),
  Family(
    name          = "ABERTURA_E_GAP",
    mechanism     = "informação do overnight ainda não precificada",
    typical_payer = "quem precisa executar na abertura, independente do preço",
    data_needed   = frozenset({"WIN"}),
    horizon_range = (5, 30),
    allowed_ops   = frozenset({ZSCORE, RET, LAG, ROLLING_MEAN, ROLLING_STD,
                               SUB, GT, IN_WINDOW, MINUTES_SINCE_OPEN}),
    forbidden_ops = frozenset({RANK_TS, REF}),
    session_hint  = (time(9,0), time(11,0)),
    available     = True,
  ),
  Family(
    name          = "REGIME_DE_VOL",
    mechanism     = "mudança de regime altera o comportamento do preço",
    typical_payer = "quem opera com parâmetro fixo através da mudança",
    data_needed   = frozenset({"WIN"}),
    horizon_range = (20, 60),
    allowed_ops   = frozenset({ZSCORE, ROLLING_STD, EMA, RET, DIV, GT,
                               IN_WINDOW, CLIP}),
    forbidden_ops = frozenset({RANK_TS, REF}),
    session_hint  = None,
    available     = True,
  ),
  Family(
    name          = "INTERMERCADO_DOLAR",
    mechanism     = "fluxo estrangeiro move o dólar antes do índice",
    typical_payer = "hedger local que precisa ajustar exposição",
    data_needed   = frozenset({"WIN", "WDO"}),
    horizon_range = (5, 20),
    allowed_ops   = frozenset({ZSCORE, RET, LAG, ROLLING_STD, SUB, MUL,
                               IN_WINDOW, REF}),
    forbidden_ops = frozenset({RANK_TS}),
    session_hint  = (time(9,0), time(17,0)),
    available     = True,
  ),
  Family(
    name          = "REVERSAO_HORARIA",
    mechanism     = "exagero em horários de baixa liquidez",
    typical_payer = "retail alavancado, liquidado em cascata",
    data_needed   = frozenset({"WIN"}),
    horizon_range = (10, 40),
    allowed_ops   = frozenset({ZSCORE, RET, ROLLING_MEAN, ROLLING_STD,
                               SUB, LT, GT, IN_WINDOW, MINUTES_SINCE_OPEN}),
    forbidden_ops = frozenset({RANK_TS, REF}),
    session_hint  = None,
    available     = True,
  ),
  Family(
    name          = "LIQUIDEZ_E_HORARIO",
    mechanism     = "spread e profundidade variam previsivelmente ao longo do dia",
    typical_payer = "quem executa no horário errado por conveniência",
    data_needed   = frozenset({"WIN"}),
    horizon_range = (5, 20),
    allowed_ops   = frozenset({ZSCORE, ROLLING_MEAN, ROLLING_STD, DIV,
                               IN_WINDOW, MINUTES_SINCE_OPEN, GT}),
    forbidden_ops = frozenset({RANK_TS, REF}),
    session_hint  = None,
    available     = True,
  ),
  Family(
    name          = "ROLAGEM_DE_VENCIMENTO",
    mechanism     = "distorção de preço na virada do contrato",
    typical_payer = "quem rola tarde e paga o spread alargado",
    data_needed   = frozenset({"WIN"}),
    horizon_range = (1440, 4320),          # 1 a 3 dias, em minutos
    allowed_ops   = frozenset({ZSCORE, RET, LAG, ROLLING_MEAN, SUB, GT}),
    forbidden_ops = frozenset({RANK_TS, REF, IN_WINDOW}),
    session_hint  = None,
    available     = True,
  ),
  Family(
    name          = "FLUXO_DE_AGRESSAO",
    mechanism     = "desequilíbrio entre agressores de compra e venda",
    typical_payer = "market maker que não reprecifica a tempo",
    data_needed   = frozenset({"WIN", "BOOK"}),
    horizon_range = (1, 5),
    allowed_ops   = frozenset({ZSCORE, DIV, ROLLING_MEAN, GT, IN_WINDOW}),
    forbidden_ops = frozenset({RANK_TS}),
    session_hint  = None,
    available     = False,   # MT5 não guarda histórico de book
  ),
]
```
