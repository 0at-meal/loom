# Loom Phase 5: Local Environment Setup & Operational Runbook

**Target Audience**: DevOps Engineers, Site Reliability Engineers, and Backend Developers.
**Scope**: Phase 5 Data Layer (Redis Persistent Belief State & SQLite Append-Only Metrics Ledger).

---

## 1. System Health Definitions

Before interacting with the data layer, all team members and monitoring checks must adhere to the agreed definitions of service state:

| Status Badge | Meaning for Phase 5 Data Layer | Routing Engine Impact |
| :--- | :--- | :--- |
| **`HEALTHY` / `UP`** | Redis responds to `PING` in $< 5\text{ms}$; SQLite accepts queries in WAL mode in $< 2\text{ms}$; constitutional abort triggers active. | Normal multi-acquirer closed-loop routing with instantaneous telemetry publishing and asynchronous batch logging. |
| **`DEGRADED`** | Redis or SQLite RTT $> 50\text{ms}$, OR Redis connection pool saturated ($> 90\%$), OR SQLite write queue $> 5,000$ records. | Latency backpressure applied. In-memory fallback buffers hold writes temporarily while alarms fire. |
| **`DOWN`** | Redis socket unreachable / connection refused, OR SQLite file unwritable / database locked beyond `busy_timeout` ($5\text{s}$). | Process crashes or operates in read-only fallback mode if configured. No telemetry broadcast; transactions cannot be durably logged. |

---

## 2. Infrastructure Setup (Starting Services)

### Primary: Containerized Docker Environment (Recommended)

Spins up pinned `redis:7-alpine` configured with Append-Only File (`AOF`) persistence and a native Docker container health check (`redis-cli ping` every 5s).

```powershell
# 1. Copy environment template if not already present
cp .env.example .env

# 2. Start the Redis persistence container in detached mode
docker compose up -d

# 3. Verify the container is running and healthy
docker compose ps
```

Expected output:
```text
NAME         IMAGE            COMMAND                  SERVICE   CREATED         STATUS                    PORTS
loom-redis   redis:7-alpine   "docker-entrypoint.s…"   redis     3 seconds ago   Up 3 seconds (healthy)    0.0.0.0:6379->6379/tcp
```

### Secondary: Zero-Docker / CI Standalone Mode

For local workstations without Docker, or headless CI runners (GitHub Actions):
- **Redis Double**: Python tests automatically mock or instantiate `fakeredis[json]`. Zero background daemons required.
- **SQLite Storage**: Runs directly against a local file (`loom_metrics.db`) or in-memory (`:memory:`).

---

## 3. Database Initialization

Execute the schema bootstrapping command to ensure the SQLite database file, tables, indexes, and constitutional triggers exist:

```powershell
python -m data_layer.cli init-db
```

Expected output:
```text
[*] Initializing SQLite schema at 'loom_metrics.db'...
[OK] SQLite database initialized successfully at 'loom_metrics.db' (journal_mode: wal, triggers active: 4).
```

Verification details:
- Tables created: `transactions`, `acquirer_outcomes`.
- Pragma applied: `journal_mode = WAL`, `synchronous = NORMAL`, `busy_timeout = 5000`.
- Constitutional Triggers: `prevent_transactions_update`, `prevent_transactions_delete`, `prevent_acquirer_outcomes_update`, `prevent_acquirer_outcomes_delete`.

---

## 4. Health & Readiness Verification

Run the automated health probe to test end-to-end connectivity and round-trip query latencies:

```powershell
python -m data_layer.cli ping
```

Expected output (Healthy):
```text
================================================================================
 Loom Data Layer Health & Readiness Check
================================================================================
  [UP]   Redis:  Host=localhost:6379 (db=0 v7.2.4) | RTT=0.45ms
  [UP]   SQLite: Target='loom_metrics.db' (journal_mode=wal) | Latency=0.18ms
--------------------------------------------------------------------------------
  [HEALTHY] All Phase 5 data layer dependencies are operational.
================================================================================
```
*Note: Exits with code `0` on success, or `1` on failure (suitable for container orchestrators and CI test gates).*

### Full System Status & Record Audit

Inspect live connection stats, registered acquirers, file sizes, and transaction row counts:

```powershell
python -m data_layer.cli status
```

---

## 5. Live State & Belief Telemetry Inspection

Inspect the live Thompson Sampling belief parameters ($\alpha, \beta$), decayed health scores ($h_t$), and counters stored in Redis:

```powershell
# Inspect all registered acquirers
python -m data_layer.cli inspect-state

# Inspect a specific acquirer in JSON format
python -m data_layer.cli inspect-state --acquirer stripe --json
```

---

## 6. Resetting to a Clean State for Demo Rehearsal

When rehearsing demonstrations or preparing benchmarks, you need to wipe synthetic telemetry and reset priors to defaults without violating `CONSTITUTION.md`.

> [!CAUTION]
> **Why `DELETE FROM transactions;` Fails**:
> The SQLite database engine enforces `BEFORE DELETE` triggers that immediately raise an `ABORT` error per `CONSTITUTION.md`. Do not attempt manual SQL `DELETE` queries.

### Automated Demo Reset Command

```powershell
# Interactive mode (prompts for confirmation)
python -m data_layer.cli reset-demo

# Unattended / Automated script mode (bypasses prompt)
python -m data_layer.cli reset-demo --force
```

What `reset-demo` executes:
1. Safely drops `acquirer_outcomes` and `transactions` tables (or unlinks the SQLite file) and re-executes `data_layer/schema.sql` to regenerate empty tables, indices, and triggers.
2. Scans and deletes all `acquirer:{id}:health`, `acquirer:{id}:beta`, and `acquirers` Redis tracking keys.
3. Leaves the running Redis container and connection parameters intact.

---

## 7. 3 AM Incident Response & Troubleshooting Playbook

### Scenario A: Redis Connection Refused (`[DOWN] Redis: Host=localhost:6379`)
1. **Check container state**:
   ```powershell
   docker compose ps
   ```
2. **Inspect Redis logs**:
   ```powershell
   docker compose logs --tail 100 redis
   ```
3. **Restart the container**:
   ```powershell
   docker compose restart redis
   ```
4. **Confirm AOF durability**: Redis will replay `appendonly.aof` from the persistent Docker volume `redis_data`.

### Scenario B: SQLite `sqlite3.OperationalError: database is locked`
1. SQLite WAL mode supports multiple concurrent readers and a single concurrent writer.
2. Check for lingering background processes holding an uncommitted lock:
   ```powershell
   # Windows PowerShell
   Get-Process python | Select-Object Id, ProcessName
   ```
3. Ensure all writers use `aiosqlite` with `busy_timeout = 5000` (5-second wait queue).

### Scenario C: Constitutional Trigger Violation (`ABORT: UPDATE operations are strictly prohibited`)
1. **Root cause**: A rogue query or legacy module attempted an in-place SQL `UPDATE` or `DELETE` on `transactions` or `acquirer_outcomes`.
2. **Action**: Check stack trace of the offending caller. All ledger writes must be append-only inserts via `MetricsLogger.log_routing_result()`.

---

## 8. Flagged Single Points of Failure (SPOFs) & Architecture Risks

Per DevOps principles, we explicitly document single points of failure rather than leaving them implicit:

1. **Single-Node Redis Instance**:
   - *Risk*: `docker-compose.yml` provides a single Redis container. If the host machine crashes, live health state must be replayed from AOF on boot.
   - *Production Recommendation*: Transition to Redis Sentinel (high availability) or AWS ElastiCache / Redis Cluster with multi-AZ replication.
2. **Single-File SQLite Database**:
   - *Risk*: `loom_metrics.db` resides on local disk. While ideal for single-instance routing and sub-millisecond local queries, it cannot be shared across multiple physical nodes without distributed storage (e.g. LiteFS / DuckDB / cloud warehouse sync).
   - *Phase 6/7 Path*: Maintain local SQLite for real-time PSR calculation and stream events via Pub/Sub to analytical warehouses (e.g. BigQuery / ClickHouse).
