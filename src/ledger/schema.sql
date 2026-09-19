-- Livro-razão. Append-only: os gatilhos abaixo recusam UPDATE e DELETE.
-- Alterar este arquivo depois do gênesis constitui um projeto novo.

CREATE TABLE IF NOT EXISTS ledger (
  seq            INTEGER PRIMARY KEY AUTOINCREMENT,
  trial_id       TEXT NOT NULL,
  kind           TEXT NOT NULL
                 CHECK(kind IN ('genesis','backtest','holdout','kill','daily')),
  hypothesis_id  TEXT,
  ast_hash       TEXT,
  structural_sig TEXT,
  data_hash      TEXT NOT NULL,
  config_hash    TEXT NOT NULL,
  verdict        TEXT,
  crashed        INTEGER NOT NULL DEFAULT 0 CHECK(crashed IN (0, 1)),
  payload        TEXT,           -- JSON canônico; genesis, kill e daily
  prev_hash      TEXT NOT NULL,
  row_hash       TEXT NOT NULL,
  ts             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_struct ON ledger(structural_sig);
CREATE INDEX IF NOT EXISTS idx_hyp    ON ledger(hypothesis_id);
CREATE INDEX IF NOT EXISTS idx_kind   ON ledger(kind);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_genesis ON ledger(kind) WHERE kind = 'genesis';

CREATE TRIGGER IF NOT EXISTS ledger_append_only_update
BEFORE UPDATE ON ledger
BEGIN
  SELECT RAISE(ABORT, 'livro-razão é append-only');
END;

CREATE TRIGGER IF NOT EXISTS ledger_append_only_delete
BEFORE DELETE ON ledger
BEGIN
  SELECT RAISE(ABORT, 'livro-razão é append-only');
END;
