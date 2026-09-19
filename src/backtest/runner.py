"""O selo: toda avaliação vira tentativa no livro-razão **antes** de o resultado sair (R4).

Ordem obrigatória (SPEC-fase-2 §2.6):

1. Assinaturas calculadas **fora** da tentativa. AST que nem canonicaliza é
   ``INVALID_AST`` e não consome tentativa — nunca chegou ao motor.
2. Chamada ao motor e validação da resposta.
3. ``ledger.append`` no ``finally``: grava inclusive quando o motor falha, a rede
   cai ou a resposta é inválida (``crashed=1``).
4. Só então o resultado é devolvido. Se a avaliação falhou, levanta
   ``BacktestCrashed`` — depois de gravar.

Se ``ledger.append`` falhar, nada é devolvido: um resultado sem registro não existe.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.backtest.costs import CostModel
from src.backtest.engine import BacktestEngine, BacktestRequest, CvConfig, TradeRules
from src.backtest.errors import BacktestCrashed, DuplicateTrial
from src.backtest.metrics import FoldMetrics, validate_outcome
from src.dsl.ast import ROOT_UNITS, Node, check_arg_kinds, infer_unit
from src.dsl.canonical import exact_signature, serialize, structural_signature
from src.dsl.errors import DslTypeError
from src.ledger.ledger import Kind, Ledger, LedgerRecord


@dataclass(frozen=True)
class BacktestConfig:
    rules: TradeRules
    costs: CostModel
    cv: CvConfig


@dataclass(frozen=True)
class SealedResult:
    """Só as métricas. Sem log, sem id, sem campo extra."""

    metrics: tuple[FoldMetrics, ...]


def run(
    ast: Node,
    hypothesis_id: str,
    trial_id: str,
    cfg: BacktestConfig,
    ledger: Ledger,
    engine: BacktestEngine,
) -> SealedResult:
    # AST malformada nunca chega ao motor e não consome tentativa (INVALID_AST)
    check_arg_kinds(ast)
    if infer_unit(ast) not in ROOT_UNITS:
        raise DslTypeError("a raiz deve ter unidade Z ou Bool")
    ast_hash = exact_signature(ast)
    struct_sig = structural_signature(ast)
    genesis = ledger.genesis_info()
    if ledger.has_trial(trial_id):
        # antes de chamar o motor: o id repetido não pode rodar sem registro
        raise DuplicateTrial(trial_id)
    req = BacktestRequest(
        test_id=trial_id,
        hypothesis_id=hypothesis_id,
        formula=serialize(ast),
        ast_hash=ast_hash,
        data_hash=genesis.data_hash,
        rules=cfg.rules,
        costs=cfg.costs,
        cv=cfg.cv,
    )

    metrics: tuple[FoldMetrics, ...] | None = None
    failure: BaseException | None = None
    try:
        outcome = engine.evaluate(req)
        validate_outcome(
            outcome,
            test_id=trial_id,
            data_hash=genesis.data_hash,
            expected_folds=cfg.cv.n_splits(),
        )
        metrics = outcome.folds
    except Exception as e:  # noqa: BLE001 — toda falha vira tentativa crashed
        failure = e
    finally:
        # PRIMEIRO: grava. Roda também em KeyboardInterrupt (crashed, pois metrics é None).
        ledger.append(
            LedgerRecord(
                trial_id=trial_id,
                kind=Kind.BACKTEST,
                data_hash=genesis.data_hash,
                config_hash=genesis.config_hash,
                hypothesis_id=hypothesis_id,
                ast_hash=ast_hash,
                structural_sig=struct_sig,
                crashed=metrics is None,
            )
        )
    # DEPOIS: devolve.
    if metrics is None:
        assert failure is not None
        raise BacktestCrashed(trial_id, failure) from failure
    return SealedResult(metrics)
