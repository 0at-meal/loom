# Phase 5 Specification — The Data Layer

**Role**: Systems Architect
**Audience**: Backend Engineer, Data Engineer, DevOps Engineer, QA Engineer, Tech Lead
**Scope**: Phase 5 (Redis Live Health State, Redis Pub/Sub Event Streaming, SQLite Append-Only Metrics Ledger)
**Status**: Settled Architecture Contract

---

## 1. Executive Summary & Component Boundary

In Phases 1 through 4, Loom developed its closed-loop control system:
1. **Perception**: Decayed Thompson Sampling belief updates ($\alpha, \beta$) and EWMA operational health scores ($H$) (`router_core/state.py`, `router_core/bandit.py`).
2. **Ground Truth**: Simulated payment gateways with controllable availability and latency (`acquirer_sim`).
3. **Actuation**: Damped traffic steering via the pure-function PID smoothing engine and bounded simplex projection (`router_core/pid.py`).

Throughout these early phases, all routing state lived strictly in volatile process memory (`AcquirerState`, `BanditStateRegistry`, `PIDState`). If the router process crashed or restarted, all learned beliefs and health history were erased, forcing a cold-start back to uninformative priors ($\alpha_0=1, \beta_0=1, H_0=1.0$). Furthermore, routing decisions and outcomes were returned ephemerally in `RoutingResult` without being durably recorded for post-hoc analysis, and external consumers (such as the upcoming Phase 7 live dashboard) had no real-time push mechanism to observe routing behavior.

**Phase 5 establishes Loom's Data Layer (`data_layer/`)**. It provides:
- **Durable Live Health Persistence (Ticket A)**: Storing active acquirer health scores and Beta belief distributions in Redis so state survives process restarts and can be shared across multiple router worker processes without split-brain divergence.
- **Real-Time Event Streaming (Ticket B)**: Publishing every routing decision, PID diagnostic envelope, and transaction outcome via Redis Pub/Sub so downstream consumers (the Phase 7 dashboard WebSocket bridge) can subscribe to live push events instead of polling.
- **Append-Only Analytical Ledger (Ticket C)**: Logging the complete history of transactions, chosen acquirers, instantaneous routing weights, and outcomes into an immutable SQLite database to provide the ground-truth data required for Phase 6's PSR-lift comparison.

```
+---------------------------------------------------------------------------------------------------------+
|                                              ROUTER CORE                                                |
|                                                                                                         |
|   +--------------------+          +---------------------+          +--------------------------------+   |
|   | Bandit State       |  target  | PID Control Engine  |  smooth  | Discrete Actuator              |   |
|   | Registry           |  alloc   | (calculate_pid_step)|  alloc   | (Stochastic Categorical Draw   |   |
|   | (Alpha, Beta, EWMA)| -------> |                     | -------> |  or Deficit Round-Robin)       |   |
|   +--------------------+          +---------------------+          +---------------+----------------+   |
|             ^                                                                      |                    |
|             | hydrate / persist                                                    | HTTP POST /auth    |
|             v                                                                      v                    |
|   +--------------------+                                           +--------------------------------+   |
|   | Redis State Store  |                                           | Simulated Acquirers            |   |
|   | (Ticket A)         |                                           | (acquirer_sim)                 |   |
|   +--------------------+                                           +---------------+----------------+   |
|             |                                                                      |                    |
|             | (acquirer:{id}:health, acquirer:{id}:beta)                           | AuthorizeResponse  |
|             v                                                                      v                    |
|   +-------------------------------------------------------------------------------------------------+   |
|   | Routing Result Envelope (RoutingResult: selected, status, latencies, allocation, diagnostics)   |   |
|   +-------------------------------------------------------------------------------------------------+   |
|                                     |                                             |                     |
+-------------------------------------|---------------------------------------------|---------------------+
                                      | non-blocking dispatch                       | async queue put
                                      v                                             v
                      +--------------------------------+            +--------------------------------+
                      | Redis Pub/Sub Broadcaster      |            | SQLite Append-Only Writer      |
                      | (Ticket B)                     |            | (Ticket C)                     |
                      | Channel: 'events:routing'      |            | Tables: transactions,          |
                      +---------------+----------------+            |         acquirer_outcomes      |
                                      |                             +---------------+----------------+
                                      | publish                                     | micro-batched WAL
                                      v                                             v
                      +--------------------------------+            +--------------------------------+
                      | Phase 7 Dashboard / WebSockets |            | SQLite DB: loom_metrics.db     |
                      | (Real-time live UI streams)    |            | (Phase 6 PSR Lift Calculation) |
                      +--------------------------------+            +--------------------------------+
```

### Explicit Scope Boundaries

- **In Scope for Phase 5**:
  - Redis schema and repository for acquirer health and Beta belief state mirroring Phase 1 in-memory fields.
  - Startup state hydration and post-outcome persistence.
  - Redis Pub/Sub publisher emitting structured JSON event envelopes for all routing decisions and outcomes.
  - Immutable, append-only SQLite schema (`transactions`, `acquirer_outcomes`) with database-level trigger enforcement.
  - Asynchronous, non-blocking SQLite writer with queue buffering and micro-batching.
  - Reproducible local and CI DevOps environments (Docker Compose Redis + Standalone FakeRedis / SQLite).
  - Additive, non-breaking router integration preserving 100% of Phase 1–4 contracts.
  - Complete unit and integration test suite (`tests/data_layer/`).

- **Explicitly Out of Scope for Phase 5**:
  - Modifying the bandit decay math or PID step equations (settled in Phase 1 & Phase 4).
  - Modifying the public signatures or return types of `BanditRouter.route()` (settled in Phase 3 & Phase 4).
  - Static baseline router implementation and automated PSR-lift calculation scripts (belongs to Phase 6).
  - React frontend, WebSocket server endpoints, or browser UI widgets (belongs to Phase 7).
  - Distributed Redis clustering, multi-region replication, or Raft consensus (explicit PRD non-goals).

---

## 2. Tech Stack & Repository Ground Rules

Per `docs/CONSTITUTION.md`, the technology choices and conventions for Phase 5 are pinned:

- **Redis Client**: `redis>=5.0.0` (specifically `redis.asyncio` for non-blocking asynchronous event loop integration).
- **In-Memory Redis Test Double**: `fakeredis[json]>=2.22.0` (pinned in `pyproject.toml` dev dependencies for zero-external-dependency testing).
- **SQLite Engine**: Python standard library `sqlite3` (DDL, triggers, and schema migrations) and `aiosqlite>=0.20.0` (asynchronous non-blocking queries and batch writes).
- **Data Serialization & Validation**: `pydantic>=2.6.0` (strict schema models with frozen configurations).

### Pinned Key & Table Conventions

Per `docs/CONSTITUTION.md`:
- **Redis Keys**: Namespaced, colon-separated:
  - `acquirer:{id}:health` — live operational EWMA health score and timestamp.
  - `acquirer:{id}:beta` — Bayesian Beta distribution belief parameters ($\alpha, \beta$), priors, and cumulative counters.
  - `acquirers` — Redis Set of all registered acquirer IDs (`acquirer_alpha`, `acquirer_beta`, etc.).
  - `events:routing` — Redis Pub/Sub channel for real-time transaction routing events.
- **SQLite Tables**: `snake_case`, plural:
  - `transactions` — Append-only log of every routing attempt, decision, allocation weight, latencies, and outcome.
  - `acquirer_outcomes` — Append-only ledger of per-acquirer state mutations resulting from each transaction.
- **Constitutional Hard Stop**: *Never delete or overwrite the SQLite metrics log.* Append-only is a hard invariant.

### Directory Layout (`data_layer/` and `tests/data_layer/`)

```
loom/
├── data_layer/
│   ├── __init__.py              # Exports: RedisStateStore, EventPublisher, MetricsLogger, DataLayerService
│   ├── config.py                # Pydantic Settings: Redis & SQLite connection/tuning configuration
│   ├── models.py                # Pydantic schemas for Redis records, Pub/Sub events, and DB rows
│   ├── redis_state.py           # Ticket A: Redis read/write for live health and Beta belief state
│   ├── redis_pubsub.py          # Ticket B: Redis Pub/Sub broadcaster for routing events
│   ├── sqlite_logger.py         # Ticket C: Async append-only SQLite writer and connection manager
│   ├── schema.sql               # Canonical SQLite DDL, triggers, and indices
│   ├── service.py               # Composite facade coordinating state, pub/sub, and logging
│   └── cli.py                   # DevOps management CLI: init-db, ping, status, inspect
└── tests/
    └── data_layer/
        ├── __init__.py
        ├── test_config.py           # Configuration loading and validation tests
        ├── test_redis_state.py      # Ticket A: Hydration, persistence, restart survival, multi-worker
        ├── test_redis_pubsub.py     # Ticket B: Pub/Sub message serialization, subscription, latency
        ├── test_sqlite_logger.py    # Ticket C: Append-only enforcement, trigger aborts, batch writes
        ├── test_integration.py      # End-to-end: Router + DataLayer execution with full persistence
        └── test_devops_setup.py     # DevOps verification: CLI tools, connection healthchecks
```

---

## 3. Ticket A Specification: Redis State Persistence (Live Health & Beliefs)

### 3.1 Objective & Boundary
Enable Loom's per-acquirer health and belief state to survive process restarts and be safely shared across concurrent worker processes, mirroring what `AcquirerState` currently holds in-memory without altering Phase 1's mathematical model.

### 3.2 Redis Data Modeling & Key Schemas

Each registered acquirer possesses two primary keys in Redis, formatted strictly per `docs/CONSTITUTION.md`:

#### 1. Live Health Score Key: `acquirer:{id}:health`
- **Redis Type**: `Hash`
- **Purpose**: Fast, low-overhead operational inspection of an acquirer's current EWMA health score.
- **Fields**:
  | Field | Redis Type | Python Type | Description |
  | :--- | :--- | :--- | :--- |
  | `health_score` | String (float) | `float` | Current decayed EWMA health score in $[0.0, 1.0]$. |
  | `last_updated_at` | String (float) | `float` | Unix epoch timestamp of the most recent outcome update. |

#### 2. Bayesian Belief State Key: `acquirer:{id}:beta`
- **Redis Type**: `Hash`
- **Purpose**: Full parameter set required to reconstruct the Beta posterior belief distribution and cumulative counters.
- **Fields**:
  | Field | Redis Type | Python Type | Description |
  | :--- | :--- | :--- | :--- |
  | `alpha` | String (float) | `float` | Shape parameter $\alpha \ge \alpha_0 > 0.0$ (decayed successes + prior). |
  | `beta` | String (float) | `float` | Shape parameter $\beta \ge \beta_0 > 0.0$ (decayed failures + prior). |
  | `alpha_prior` | String (float) | `float` | Base prior $\alpha_0$ (default: `1.0`). |
  | `beta_prior` | String (float) | `float` | Base prior $\beta_0$ (default: `1.0`). |
  | `decay_factor` | String (float) | `float` | Retention factor $\gamma \in (0.0, 1.0)$ (default: `0.98`). |
  | `success_count` | String (int) | `int` | Cumulative unweighted successes observed over lifetime. |
  | `failure_count` | String (int) | `int` | Cumulative unweighted failures observed over lifetime. |
  | `total_count` | String (int) | `int` | Cumulative unweighted total transactions (`success + failure`). |
  | `last_updated_at` | String (float) | `float` | Unix epoch timestamp of the most recent update. |

#### 3. Registry Discovery Key: `acquirers`
- **Redis Type**: `Set`
- **Purpose**: Dynamic registry of all active acquirer IDs in the cluster (`SADD acquirers {id}`). Enables newly booted router instances to discover configured acquirers.

### 3.3 Core Operations & Lifecycle

```python
class RedisStateStore:
    """Manages Redis persistence for live acquirer health scores and Beta belief states."""

    def __init__(
        self,
        redis_client: redis.asyncio.Redis,
        key_prefix: str = "",
    ) -> None:
        """Initialize Redis state store with async client and optional namespace prefix."""
        ...

    async def hydrate_acquirer(
        self,
        acquirer_id: str,
        fallback_config: AcquirerStateConfig,
    ) -> AcquirerStateSnapshot:
        """Load state from Redis if present; otherwise initialize Redis with fallback priors."""
        ...

    async def hydrate_all(
        self,
        known_routes: list[AcquirerRouteConfig],
    ) -> dict[str, AcquirerStateSnapshot]:
        """Hydrate state across all candidate routes on router startup."""
        ...

    async def save_snapshot(
        self,
        snapshot: AcquirerStateSnapshot,
    ) -> None:
        """Persist an updated AcquirerStateSnapshot to Redis across both keys in a single pipeline."""
        ...

    async def get_health_score(self, acquirer_id: str) -> float | None:
        """Quickly read only the live health score for a specific acquirer."""
        ...
```

#### A. Hydration Flow (Startup Sequence)
1. When `BanditRouter.start()` is called, for each configured route:
   - Read `HGETALL acquirer:{id}:beta` and `HGETALL acquirer:{id}:health`.
   - If keys exist in Redis:
     Construct an `AcquirerState` populated with the persisted `alpha`, `beta`, `health_score`, `success_count`, `failure_count`, and `last_updated_at`.
   - If keys do NOT exist in Redis:
     Initialize `AcquirerState` from `AcquirerStateConfig` priors ($\alpha = \alpha_0, \beta = \beta_0, H = H_0$), and immediately execute `save_snapshot()` to establish Redis keys.
   - Execute `SADD acquirers {id}` to maintain the discovery set.

#### B. Persistence Flow (Post-Outcome)
1. In `BanditRouter.route()`, after the acquirer HTTP response returns and `AcquirerState.record_outcome()` computes the new snapshot:
2. An asynchronous Redis pipeline writes both keys atomically:
   ```python
   async with self._redis.pipeline(transaction=True) as pipe:
       pipe.hset(f"acquirer:{aid}:health", mapping={
           "health_score": str(snapshot.health_score),
           "last_updated_at": str(snapshot.last_updated_at),
       })
       pipe.hset(f"acquirer:{aid}:beta", mapping={
           "alpha": str(snapshot.alpha),
           "beta": str(snapshot.beta),
           "alpha_prior": str(snapshot.alpha_prior),
           "beta_prior": str(snapshot.beta_prior),
           "decay_factor": str(config.decay_factor),
           "success_count": str(snapshot.success_count),
           "failure_count": str(snapshot.failure_count),
           "total_count": str(snapshot.total_count),
           "last_updated_at": str(snapshot.last_updated_at),
       })
       await pipe.execute()
   ```

### 3.4 Multi-Process Concurrency & Distributed Updates

If Loom runs with multiple worker processes (e.g., `uvicorn router_core.app:app --workers 4`), two processes may handle outcomes for the same acquirer simultaneously.

#### The Race Condition in Python-Space
If Process 1 and Process 2 read $(\alpha=10.0, \beta=2.0)$, both decay in local RAM, and both write back, one update overwrites the other.

#### Architectural Resolution: Redis Atomic Outcome Script (Lua)
To eliminate multi-process write-conflict without introducing locking overhead, Ticket A specifies an atomic Lua script (`record_outcome.lua`) embedded in `RedisStateStore`:
- **Inputs**: `KEYS[1]` (`acquirer:{id}:health`), `KEYS[2]` (`acquirer:{id}:beta`), `ARGV[1]` (success flag `1` or `0`), `ARGV[2]` (timestamp float).
- **Execution**: The Redis single-threaded engine executes Phase 1's exact mathematical transition:
  $$\alpha_t = \max(\alpha_0, \; \alpha_0 + \gamma(\alpha_{t-1} - \alpha_0) + x)$$
  $$\beta_t = \max(\beta_0, \; \beta_0 + \gamma(\beta_{t-1} - \beta_0) + (1 - x))$$
  $$H_t = \max(0.0, \; \min(1.0, \; \gamma H_{t-1} + (1 - \gamma)x))$$
- **Output**: Returns the updated snapshot values directly to the calling worker process.
- **Benefit**: 100% atomic, zero lost updates across any number of router processes, zero distributed locking complexity.

### 3.5 Failure Modes & Graceful Degradation
- **Hard Rule**: *A payment router must never fail a transaction because its state store is slow or unreachable.*
- If Redis is unavailable on startup: Log `WARNING`, initialize `BanditStateRegistry` in-memory from config, and set `redis_available = False`.
- If Redis connection drops during routing: Catch `redis.ConnectionError` and `redis.TimeoutError`, log `WARNING`, continue using the in-memory `AcquirerState`, and schedule background reconnect.

---

## 4. Ticket B Specification: Redis Pub/Sub Event Streaming

### 4.1 Objective & Boundary
Stream every routing decision, PID smoothing diagnostic vector, and authorization outcome to a Redis Pub/Sub channel so that the future React live dashboard (Phase 7) or external monitoring tools can consume real-time telemetry via WebSocket push rather than polling HTTP `/state`.

### 4.2 Channel Taxonomy & Event Envelope

- **Primary Routing Channel**: `events:routing`
  Broadcasts the unified `RoutingEvent` on every completed transaction.
- **Acquirer State Transition Channel**: `events:health`
  Broadcasts an alert event when an acquirer's health score drops below an operational warning threshold (e.g. $H < 0.70$) or when an outage is detected.

### 4.3 Pydantic Event Envelope Schema (`RoutingEvent`)

```python
class RoutingEvent(BaseModel):
    """Full point-in-time telemetry payload emitted to Redis Pub/Sub on every transaction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(
        ...,
        description="Unique event UUID v4.",
    )
    event_type: Literal["ROUTING_COMPLETED"] = "ROUTING_COMPLETED"
    timestamp: float = Field(
        ...,
        description="Epoch timestamp when routing completed.",
    )
    transaction_id: str = Field(
        ...,
        description="Unique transaction identifier.",
    )
    selected_acquirer: str = Field(
        ...,
        description="Chosen acquirer route identifier.",
    )
    status: Literal["AUTHORIZED", "DECLINED", "ERROR"] = Field(
        ...,
        description="Authorization status outcome.",
    )
    authorized: bool = Field(
        ...,
        description="True if payment authorized successfully.",
    )
    success: bool = Field(
        ...,
        description="Binary outcome feedback fed back to the bandit.",
    )
    decline_code: str | None = Field(
        default=None,
        description="Decline code if authorization failed (e.g. 'ACQUIRER_OUTAGE').",
    )
    routing_latency_ms: float = Field(
        ...,
        ge=0.0,
        description="Time spent selecting route and calculating PID step in ms.",
    )
    acquirer_latency_ms: float = Field(
        ...,
        ge=0.0,
        description="Network and execution latency at simulated acquirer in ms.",
    )
    total_latency_ms: float = Field(
        ...,
        ge=0.0,
        description="Total round-trip latency in ms.",
    )
    thompson_samples: dict[str, float] = Field(
        ...,
        description="Beta samples drawn across all candidate routes.",
    )
    target_allocation: dict[str, float] | None = Field(
        default=None,
        description="Raw bandit target allocation vector.",
    )
    smoothed_allocation: dict[str, float] | None = Field(
        default=None,
        description="Phase 4 PID smoothed allocation vector (governing discrete actuation).",
    )
    allocation_weight: float = Field(
        ...,
        description="The instantaneous smoothed allocation weight of the selected route.",
    )
    pid_diagnostics: dict[str, Any] | None = Field(
        default=None,
        description="PID error, P/I/D terms, and simplex adjustments.",
    )
    updated_state: dict[str, float] = Field(
        ...,
        description="Updated post-outcome snapshot for selected route (alpha, beta, health_score).",
    )
```

### 4.4 Publisher Implementation Contract (`EventPublisher`)

```python
class EventPublisher:
    """Non-blocking Redis Pub/Sub event broadcaster for Loom routing telemetry."""

    def __init__(
        self,
        redis_client: redis.asyncio.Redis,
        channel_name: str = "events:routing",
    ) -> None:
        """Initialize publisher with async Redis connection and target channel."""
        ...

    async def publish_routing_event(self, result: RoutingResult) -> None:
        """Serialize RoutingResult into RoutingEvent and publish to Redis Pub/Sub."""
        ...

    async def publish_health_alert(
        self,
        acquirer_id: str,
        old_health: float,
        new_health: float,
    ) -> None:
        """Broadcast health degradation alert when score drops sharply."""
        ...
```

### 4.5 Latency Isolation & Delivery Semantics
- **Fire-and-Forget**: `publish_routing_event` must be executed as an asynchronous background task (`asyncio.create_task`) or awaited directly if its latency is verified $< 0.1\,\text{ms}$.
- **Zero-Subscriber Safety**: Redis Pub/Sub is inherently push-and-forget. When zero dashboard subscribers are connected, Redis immediately drops the message. This prevents memory leaks or queue buildup in Redis during idle dashboard periods.
- **At-Most-Once Delivery**: Pub/Sub is strictly at-most-once for live visualization. Historical replay and guaranteed durability are provided by SQLite (Ticket C), not Redis Pub/Sub.

---

## 5. Ticket C Specification: SQLite Append-Only Metrics Ledger

### 5.1 Objective & Boundary
Persist the full, immutable chronological history of all transaction routing decisions, allocation weights, latencies, and outcome transitions into SQLite. This database is the authoritative audit trail that Phase 6 will consume to mathematically compute and verify Loom's Payment Success Rate (PSR) lift against the static baseline.

### 5.2 Constitutional Requirements
Per `docs/CONSTITUTION.md`:
1. Table naming must be `snake_case`, plural: `transactions` and `acquirer_outcomes`.
2. *Never delete or overwrite the SQLite metrics log.* All data is append-only.

### 5.3 Canonical Relational Schema (`data_layer/schema.sql`)

```sql
-- ============================================================================
-- Loom Metrics Ledger Schema (Phase 5)
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
```

### 5.4 Database-Level Append-Only Guarantee
Rather than relying solely on application-level discipline, the SQLite triggers defined above enforce immutability directly inside the database engine:
- Any `UPDATE transactions SET ...` immediately raises an abort error.
- Any `DELETE FROM transactions WHERE ...` immediately raises an abort error.
- Any attempt to truncate or clear the history will fail at the database boundary, safeguarding the historical record for Phase 6's PSR-lift baseline comparison.

### 5.5 High-Throughput Asynchronous Buffered Writer

Executing a synchronous SQLite `INSERT` on every transaction introduces disk write latency ($1\text{ms} - 15\text{ms}$ per fsync) directly into the hot routing path, destroying system throughput.

To achieve sub-millisecond routing latency while guaranteeing zero lost records, Ticket C specifies an asynchronous buffered logger (`MetricsLogger`):

```python
class MetricsLogger:
    """Asynchronous, batch-buffered SQLite metrics logger for high-throughput routing."""

    def __init__(
        self,
        db_path: str,
        batch_size: int = 20,
        flush_interval_sec: float = 0.05,
        max_queue_size: int = 10_000,
    ) -> None:
        """Initialize async writer with in-memory buffer queue and batch flush thresholds."""
        ...

    async def start(self) -> None:
        """Start the background consumer worker loop."""
        ...

    async def log_routing_result(self, result: RoutingResult) -> None:
        """Enqueue RoutingResult into the in-memory write buffer (non-blocking)."""
        ...

    async def flush(self) -> None:
        """Block until all buffered records are written to SQLite disk storage."""
        ...

    async def close(self) -> None:
        """Flush remaining buffer and terminate background worker."""
        ...
```

#### Buffer Architecture & Worker Lifecycle:
1. `log_routing_result()` places the record into an `asyncio.Queue[RoutingResult]` in under $2.0\,\mu\text{s}$.
2. A single dedicated background coroutine (`_drain_loop`) pulls batches from the queue.
3. The worker writes batches to SQLite using `aiosqlite` and `executemany()` within a single transaction:
   - Condition 1: When batch size reaches `batch_size` (e.g. 20 records).
   - Condition 2: When elapsed time since last write exceeds `flush_interval_sec` (e.g. 50ms).
4. On application shutdown, `BanditRouter` invokes `await metrics_logger.flush()` and `close()`, guaranteeing that all pending transactions are committed before the process exits.

---

## 6. Reproducible Local & DevOps Setup Specification

To give DevOps and local developers an unambiguous target, Phase 5 defines exact configuration standards, container definitions, and standalone fallback behavior.

### 6.1 Environment Configuration (`.env` and `pydantic-settings`)

The data layer reads configuration via `data_layer/config.py`, binding environment variables with strict validation:

```python
class DataLayerConfig(BaseSettings):
    """Configuration settings for Redis and SQLite data layer services."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Redis Configuration
    redis_host: str = Field(default="localhost", description="Redis host hostname or IP.")
    redis_port: int = Field(default=6379, ge=1, le=65535, description="Redis port.")
    redis_db: int = Field(default=0, ge=0, description="Redis database index.")
    redis_password: str | None = Field(default=None, description="Optional Redis auth password.")
    redis_timeout_sec: float = Field(default=2.0, gt=0.0, description="Redis socket timeout.")
    redis_max_connections: int = Field(default=50, gt=0, description="Redis connection pool size.")

    # SQLite Configuration
    sqlite_db_path: str = Field(
        default="loom_metrics.db",
        description="Path to SQLite metrics database file, or ':memory:'.",
    )
    sqlite_batch_size: int = Field(
        default=20,
        ge=1,
        description="Number of records to accumulate before batch insert.",
    )
    sqlite_flush_interval_sec: float = Field(
        default=0.05,
        gt=0.0,
        description="Maximum seconds before flushing buffer to SQLite.",
    )
```

### 6.2 Docker Compose Target (`docker-compose.yml`)

The repository's pinned `docker-compose.yml` provides the reference Redis service:

```yaml
version: '3.8'

services:
  redis:
    image: redis:7-alpine
    container_name: loom-redis
    ports:
      - "${REDIS_PORT:-6379}:6379"
    volumes:
      - redis_data:/data
    command: redis-server --appendonly yes
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

volumes:
  redis_data:
```

- **AOF Persistence**: Configured with `--appendonly yes` so state is written to the Append-Only File on disk, surviving container restarts.
- **Healthcheck**: Validates readiness before downstream services connect.

### 6.3 Standalone & CI Target (Zero-Docker Mode)

For local development without Docker and for automated CI runs on GitHub Actions:
1. **Redis Double**: Uses `fakeredis[json]` to provide a fully compliant in-memory Redis instance with zero external dependencies.
2. **SQLite Storage**: Uses local file path `./loom_metrics.db` or in-memory database `:memory:`.
3. Running `pytest tests/data_layer/` requires zero external containers, zero open network ports, and runs anywhere Python 3.11+ is installed.

### 6.4 DevOps CLI Tooling (`python -m data_layer.cli`)

A lightweight CLI is exposed for DevOps automation, Docker health checks, and CI pipeline gates:

| Command | Action | Success Exit Code | Failure Exit Code |
| :--- | :--- | :--- | :--- |
| `python -m data_layer.cli init-db` | Executes `schema.sql` to initialize SQLite tables, triggers, and indices idempotently. | `0` | `1` |
| `python -m data_layer.cli ping` | Tests connectivity and latency to both Redis and SQLite. | `0` | `1` |
| `python -m data_layer.cli status` | Displays Redis connection state, registered acquirers, and SQLite row counts. | `0` | `1` |
| `python -m data_layer.cli inspect-state` | Dumps current `acquirer:{id}:health` and `acquirer:{id}:beta` Redis keys to console. | `0` | `1` |

---

## 7. Additive Integration Contract (Zero Breaking Changes)

### 7.1 The Additive Principle
Phase 5 **must slot in without modifying or breaking any interfaces established in Phases 1 through 4**.
- `AcquirerState`, `AcquirerStateConfig`, and `AcquirerStateSnapshot` (`router_core/state.py`) remain untouched.
- `BanditStateRegistry` (`router_core/bandit.py`) remains untouched.
- `PIDConfig`, `PIDState`, `PIDDiagnostics`, and `calculate_pid_step` (`router_core/pid.py`) remain untouched.
- `AuthorizeRequest` and `AuthorizeResponse` (`acquirer_sim/models.py`) remain untouched.
- `RoutingResult` schema (`router_core/models.py`) remains untouched.

### 7.2 Composite Service Architecture (`DataLayerService`)

To achieve clean separation of concerns, the three tickets are unified into a single composite service:

```python
class DataLayerService:
    """Composite facade integrating Redis state, Redis pub/sub, and SQLite logging."""

    def __init__(
        self,
        config: DataLayerConfig,
        redis_client: redis.asyncio.Redis | None = None,
    ) -> None:
        """Initialize state store, event publisher, and metrics logger."""
        ...

    async def start(self) -> None:
        """Connect to Redis, initialize SQLite schema, and start background workers."""
        ...

    async def record_routing_result(self, result: RoutingResult) -> None:
        """Persist state to Redis, broadcast to Pub/Sub, and enqueue to SQLite logger."""
        ...

    async def close(self) -> None:
        """Flush SQLite buffers, close Redis connections, and terminate workers."""
        ...
```

### 7.3 Wiring into `BanditRouter`

The router integrates with the data layer via an optional parameter:

```python
class BanditRouter:
    def __init__(
        self,
        config: RouterConfig,
        http_client: httpx.AsyncClient | None = None,
        rng: np.random.Generator | None = None,
        data_layer: DataLayerService | None = None,  # Optional additive hook
    ) -> None:
        ...
        self._data_layer = data_layer
```

#### Routing Flow with Data Layer Hook:
```python
async def route(self, request: AuthorizeRequest) -> RoutingResult:
    # 1. Perception, PID Smoothing & Selection (Phase 1-4 logic unchanged)
    ...
    # 2. HTTP Dispatch to Acquirer (Phase 2-3 logic unchanged)
    ...
    # 3. Closed-Loop State Feedback Update (Phase 1 math unchanged)
    updated_snapshot = self._registry.record_outcome(...)
    ...
    result = RoutingResult(...)

    # 4. Optional Additive Data Layer Dispatch (Phase 5)
    if self._data_layer is not None:
        await self._data_layer.record_routing_result(result)

    return result
```

- When `data_layer is None` (the default in all existing unit tests), `BanditRouter` behaves identically to Phase 4. All 147 existing unit and integration tests continue to pass without modification.
- When `data_layer` is provided (in production or data-enabled integration runs), Redis state persistence, Pub/Sub broadcasting, and SQLite logging execute seamlessly.

---

## 8. Non-Functional Requirements & Performance Budgets

Payment routing is latency-critical. The data layer must never jeopardize Loom's routing budget.

| Component / Operation | Latency Budget Target | Upper Bound | Mechanism to Enforce Budget |
| :--- | :--- | :--- | :--- |
| **Pub/Sub Broadcast** | $< 0.10\,\text{ms}$ | $< 0.50\,\text{ms}$ | Non-blocking `asyncio` call to local Redis socket. |
| **SQLite Log Enqueue** | $< 0.01\,\text{ms}$ | $< 0.05\,\text{ms}$ | In-memory `asyncio.Queue.put_nowait()`; zero disk I/O on request path. |
| **Redis State Persistence** | $< 0.25\,\text{ms}$ | $< 1.00\,\text{ms}$ | Pipelined `HSET` or atomic Lua execution over persistent connection pool. |
| **Total Added Route Latency** | $< 0.35\,\text{ms}$ | $< 1.50\,\text{ms}$ | Fully preserves $> 95\%$ of budget for external acquirer HTTP round-trip. |
| **SQLite Disk Write Throughput** | $> 2,000\,\text{tx/sec}$ | N/A | WAL mode + batch size $20$ + `executemany` single-transaction commits. |
| **Memory Footprint** | $< 50\,\text{MB}$ | $< 150\,\text{MB}$ | Bounded queue ($10,000$ items) prevents memory leaks under backpressure. |

---

## 9. Architecture Decision Records (ADRs)

### ADR-01: Decoupled Dual Storage (Redis for Operational State, SQLite for Analytical Ledger)
- **Context**: Loom requires both sub-millisecond live health state with pub/sub eventing, and a durable, queryable, append-only history of transactions for Phase 6 PSR-lift validation.
- **Alternatives Considered**:
  1. *SQLite Only*: High durability and SQL queryability, but lacks built-in pub/sub and introduces file lock contention across multiple worker processes.
  2. *Redis Only (Redis Streams + RedisJSON)*: High speed and built-in pub/sub, but complex historical SQL queries, no native relational integrity, and high RAM costs for long-running logs.
  3. *PostgreSQL for Everything*: Heavyweight external dependency; violates PRD's solo-developer, simple-demo orientation.
- **Decision**: Dual storage model using Redis for live operational state and pub/sub, and SQLite for the append-only analytical transaction ledger.
- **Trade-off**: Managing two database connections, compensated by matching each store to its ideal workload.

### ADR-02: Micro-Batched Background SQLite Writer with WAL Mode
- **Context**: Synchronous file writes to SQLite on every transaction introduce disk fsync delays ($5\text{ms}-20\text{ms}$) that severely degrade transaction throughput.
- **Alternatives Considered**:
  1. *Synchronous INSERT on every transaction*: Rejected due to latency penalty.
  2. *Fire-and-forget unbuffered thread pool*: Unbounded thread creation and potential SQLite `busy` lock errors.
  3. *Single-worker in-memory queue with micro-batching*: In-memory `asyncio.Queue` drained by a background worker flushing every 20 records or 50ms.
- **Decision**: Option 3 with SQLite WAL (`PRAGMA journal_mode = WAL; PRAGMA synchronous = NORMAL;`).
- **Trade-off**: A power failure could lose up to 50ms (or 20 records) of telemetry. Accepted because Loom is a demonstration harness, not an ACID financial ledger.

### ADR-03: Fire-and-Forget Async Pub/Sub with At-Most-Once Delivery
- **Context**: The live dashboard needs real-time transaction updates.
- **Alternatives Considered**:
  1. *Redis Pub/Sub (at-most-once)*: Simple, zero memory retention if no listeners, instant push.
  2. *Redis Streams (at-least-once with consumer groups)*: Guarantees delivery and message acking, but requires consumer state tracking and stream trimming.
  3. *HTTP Polling*: Rejected due to high overhead and polling lag.
- **Decision**: Option 1 (Redis Pub/Sub).
- **Trade-off**: Dropped messages if dashboard is temporarily disconnected. Accepted because live charts visualize current stream, while SQLite provides historical durability.

### ADR-04: Database-Level Trigger Enforcement for Append-Only Ledger
- **Context**: `CONSTITUTION.md` strictly mandates: *Never delete or overwrite the SQLite metrics log.*
- **Alternatives Considered**:
  1. *Application-level convention (only calling `INSERT` in code)*: Vulnerable to accidental regressions or raw SQL execution bugs.
  2. *Database Triggers raising `ABORT`*: Engine-enforced rejection of `UPDATE` and `DELETE`.
- **Decision**: Option 2 (Engine-level triggers on `transactions` and `acquirer_outcomes`).
- **Trade-off**: Requires explicit schema teardown (`DROP TABLE`) to reset during tests. Accepted because it turns a constitutional rule into an unbreakable constraint.

### ADR-05: Non-Blocking Graceful Degradation on Data Store Failure
- **Context**: Redis or SQLite may become temporarily unreachable or slow.
- **Alternatives Considered**:
  1. *Fail-Fast*: Throw exception and abort payment authorization.
  2. *Graceful Degradation*: Catch data layer exceptions, log a warning, and allow the payment transaction to proceed using in-memory state.
- **Decision**: Option 2.
- **Trade-off**: Telemetry gap during database outage. Accepted because in production payments, revenue preservation always supersedes telemetry logging.

### ADR-06: Dual-Target Local Setup (Docker Compose + Standalone FakeRedis/Memory)
- **Context**: DevOps needs a standard production-like container setup, while developers and CI need fast, zero-dependency testing.
- **Alternatives Considered**:
  1. *Docker mandatory for all tests*: Requires Docker daemon on every dev box and in CI runner, slowing down test runs.
  2. *Dual-target configuration*: `docker-compose.yml` for containerized runtime; `fakeredis` and SQLite `:memory:` for automated pytest suite.
- **Decision**: Option 2.
- **Trade-off**: Slight behavioral differences between `fakeredis` and real Redis. Mitigated by integration tests against real Redis in containerized environments.

---

## 10. Hand-Off Checklist for Backend Engineer, Data Engineer & QA

### Phase 5 Implementation Breakdown

#### Ticket A: Redis Read/Write for Live Health State (`data_layer/redis_state.py`)
- [ ] Implement `DataLayerConfig` in `data_layer/config.py` using `pydantic-settings`.
- [ ] Implement `RedisStateStore` in `data_layer/redis_state.py`:
  - [ ] Key schemas: `acquirer:{id}:health`, `acquirer:{id}:beta`, `acquirers`.
  - [ ] `hydrate_acquirer(acquirer_id)` loading stored state into `AcquirerState`.
  - [ ] `save_snapshot(snapshot)` pipelined write across both hashes.
  - [ ] Lua script `record_outcome.lua` for atomic multi-process updates.
  - [ ] Graceful fallback to in-memory state on connection timeout/failure.
- [ ] Implement unit tests in `tests/data_layer/test_redis_state.py`:
  - [ ] Persistence verification: save state, re-instantiate, verify values match exactly.
  - [ ] Restart survival: simulate router restart and verify state restores without reverting to priors.
  - [ ] Multi-worker concurrency test: 4 simulated concurrent workers updating same acquirer.

#### Ticket B: Redis Pub/Sub Event Streaming (`data_layer/redis_pubsub.py`)
- [ ] Implement `RoutingEvent` schema in `data_layer/models.py`.
- [ ] Implement `EventPublisher` in `data_layer/redis_pubsub.py`:
  - [ ] `publish_routing_event(result: RoutingResult)` publishing JSON to `events:routing`.
  - [ ] `publish_health_alert(acquirer_id, old_h, new_h)` publishing to `events:health`.
  - [ ] Non-blocking async execution.
- [ ] Implement unit tests in `tests/data_layer/test_redis_pubsub.py`:
  - [ ] Subscribe to `events:routing`, publish event, verify deserialized payload matches `RoutingResult`.
  - [ ] Zero-subscriber benchmark: verify publishing without listeners incurs zero memory growth.
  - [ ] Latency benchmark: verify `publish_routing_event` executes in $< 0.5\,\text{ms}$.

#### Ticket C: SQLite Schema and Append-Only Logging (`data_layer/sqlite_logger.py`)
- [ ] Create canonical `data_layer/schema.sql`:
  - [ ] DDL for `transactions` and `acquirer_outcomes`.
  - [ ] WAL mode PRAGMAs and performance indices.
  - [ ] SQLite triggers `prevent_transactions_update`, `prevent_transactions_delete`, etc.
- [ ] Implement `MetricsLogger` in `data_layer/sqlite_logger.py`:
  - [ ] Async queue buffer with `batch_size=20` and `flush_interval_sec=0.05`.
  - [ ] Background drain worker using `aiosqlite.executemany`.
  - [ ] `flush()` and `close()` lifecycle methods for zero-data-loss shutdown.
- [ ] Implement unit tests in `tests/data_layer/test_sqlite_logger.py`:
  - [ ] Append-only trigger test: execute `UPDATE` and `DELETE`, verify SQLite aborts with constitutional error.
  - [ ] Batching verification: verify multiple results are flushed in single transaction.
  - [ ] Shutdown flush test: enqueue 50 items, shut down immediately, verify all 50 exist in DB.

#### System Integration & DevOps Tooling (`data_layer/service.py`, `data_layer/cli.py`)
- [ ] Implement `DataLayerService` facade uniting Tickets A, B, and C.
- [ ] Wire `data_layer` into `BanditRouter` as an optional additive parameter.
- [ ] Implement `data_layer/cli.py` (`init-db`, `ping`, `status`, `inspect-state`).
- [ ] Implement integration tests in `tests/data_layer/test_integration.py`:
  - [ ] Full end-to-end routing run with simulated acquirers, verifying Redis state updates, Pub/Sub events received, and SQLite records written.
  - [ ] Backward compatibility verification: run all 147 Phase 1–4 tests to confirm 100% green without modification.
