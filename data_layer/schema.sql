-- ============================================================================
-- Loom Metrics Ledger Schema (Phase 5 Ticket C)
-- Conforms strictly to docs/CONSTITUTION.md: snake_case, plural, append-only.
-- ============================================================================

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

-- ----------------------------------------------------------------------------
-- Table 1: transactions
-- Records every routing decision, actuation weight, and outcome envelope.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transactions (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id              TEXT NOT NULL UNIQUE,
    timestamp                   REAL NOT NULL,
    chosen_acquirer             TEXT NOT NULL,
    allocation_weight           REAL NOT NULL,
    status                      TEXT NOT NULL CHECK(status IN ('AUTHORIZED', 'DECLINED', 'ERROR')),
    authorized                  INTEGER NOT NULL CHECK(authorized IN (0, 1)),
    success                     INTEGER NOT NULL CHECK(success IN (0, 1)),
    decline_code                TEXT,
    routing_latency_ms          REAL NOT NULL,
    acquirer_latency_ms         REAL NOT NULL,
    total_latency_ms            REAL NOT NULL,
    smoothed_allocation_json    TEXT NOT NULL,
    target_allocation_json      TEXT,
    thompson_samples_json       TEXT NOT NULL,
    pid_diagnostics_json        TEXT,
    error_message               TEXT,
    created_at                  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_transactions_timestamp
    ON transactions(timestamp);

CREATE INDEX IF NOT EXISTS idx_transactions_acquirer
    ON transactions(chosen_acquirer);

CREATE INDEX IF NOT EXISTS idx_transactions_status
    ON transactions(status);

CREATE INDEX IF NOT EXISTS idx_transactions_created_at
    ON transactions(created_at);

-- ----------------------------------------------------------------------------
-- Table 2: acquirer_outcomes
-- Tracks acquirer belief and health mutations resulting from each transaction.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS acquirer_outcomes (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id              TEXT NOT NULL,
    acquirer_id                 TEXT NOT NULL,
    timestamp                   REAL NOT NULL,
    success                     INTEGER NOT NULL CHECK(success IN (0, 1)),
    alpha                       REAL NOT NULL,
    beta                        REAL NOT NULL,
    health_score                REAL NOT NULL,
    expected_success_rate       REAL NOT NULL,
    success_count               INTEGER NOT NULL,
    failure_count               INTEGER NOT NULL,
    total_count                 INTEGER NOT NULL,
    created_at                  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY(transaction_id) REFERENCES transactions(transaction_id)
);

CREATE INDEX IF NOT EXISTS idx_acquirer_outcomes_acquirer_ts
    ON acquirer_outcomes(acquirer_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_acquirer_outcomes_tx_id
    ON acquirer_outcomes(transaction_id);

-- ============================================================================
-- Constitutional Enforcement: Database-Level Append-Only Triggers
-- Prohibits all UPDATE and DELETE operations at the SQLite engine level.
-- ============================================================================

CREATE TRIGGER IF NOT EXISTS prevent_transactions_update
BEFORE UPDATE ON transactions
BEGIN
    SELECT RAISE(ABORT, 'Transactions table is append-only: UPDATE operations are strictly prohibited by CONSTITUTION.md');
END;

CREATE TRIGGER IF NOT EXISTS prevent_transactions_delete
BEFORE DELETE ON transactions
BEGIN
    SELECT RAISE(ABORT, 'Transactions table is append-only: DELETE operations are strictly prohibited by CONSTITUTION.md');
END;

CREATE TRIGGER IF NOT EXISTS prevent_acquirer_outcomes_update
BEFORE UPDATE ON acquirer_outcomes
BEGIN
    SELECT RAISE(ABORT, 'Acquirer outcomes table is append-only: UPDATE operations are strictly prohibited by CONSTITUTION.md');
END;

CREATE TRIGGER IF NOT EXISTS prevent_acquirer_outcomes_delete
BEFORE DELETE ON acquirer_outcomes
BEGIN
    SELECT RAISE(ABORT, 'Acquirer outcomes table is append-only: DELETE operations are strictly prohibited by CONSTITUTION.md');
END;
