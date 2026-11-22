# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Loom is a payment-routing experiment: Thompson Sampling picks the healthiest of several **simulated** acquirers, and a PID-style filter smooths the bandit's choices into a slowly changing allocation weight. It is a research/demo repo whose current work is remediation of an adversarial audit (`docs/AUDIT.md`, findings F-01 … F-34), one PR per remediation phase. `docs/CONTEXT_TRANSFER.md` holds the latest handoff state and next steps; where it and the code disagree, the code wins.

## Commands

Python 3.11, virtualenv in `.venv/` (`.venv\Scripts\Activate.ps1` on PowerShell). Node 20 for the dashboard.

```bash
pip install -e ".[dev]"                 # backend + linters + test tools
npm --prefix dashboard ci               # dashboard deps

# What CI runs (.github/workflows/ci.yml); all must pass
ruff check .
ruff format --check .
mypy .                                  # strict; covers scripts/ and tests/ too
pytest --cov=router_core --cov=acquirer_sim --cov=data_layer --cov=baseline_router --cov-report=term-missing
npm --prefix dashboard test             # Vitest, jsdom
npm --prefix dashboard run build

# Single tests
pytest tests/router_core/test_pid.py
pytest tests/router_core/test_pid.py::test_name
pytest -k "wall_clock"
cd dashboard && npx vitest run src/__tests__/telemetryState.test.js
cd dashboard && npx vitest run -t "name fragment"

# Run it
python scripts/run_demo.py --tps 15     # simulator :8001 + router :8000 + synthetic traffic
npm --prefix dashboard run dev          # http://localhost:5173 (proxies /api and /ws to :8000)
python -m acquirer_sim.server --port 8001      # or each service on its own
python -m router_core.server --port 8000       # flags: --no-pid --kp --ki --kd --min-allocation
                                               #   --half-life-sec --max-in-flight --no-ledger --redis
                                               #   --fail-fast (Stage 1 gate + cascade + rate floor; opt-in)
uvicorn router_core.app:app --reload           # auto-reload (default three-acquirer topology)
python scripts/simulate_outage.py --acquirer-id acquirer_alpha --action trigger|clear|pulse

# Benchmarks (every number in the README comes from one of these)
python scripts/compare_psr.py                              # single seed; DELETES compare_psr_*.db first
python scripts/compare_psr.py --n-seeds 100 --out-json dashboard/src/data/baselineComparison.json
python scripts/compare_psr.py --out-reference-json dashboard/src/data/baselineReferenceRun.json
python scripts/steady_state_share.py [--config served]     # ~10 min
python scripts/value_policy_outage.py                      # Phase 8 protection, 30 seeds
python scripts/concurrency_inflight.py --seeds 10

# Scenario suite (discrete-event, all policies incl. loom_failfast, paired seeds; ~3.5 h on 10 workers at 200)
python scripts/suite/run_suite.py --policies all --seeds 200 --split test --out-json docs/results/suite.json
python scripts/suite/report.py docs/results/suite.json --out docs/results/suite.md
python scripts/suite/run_suite.py --scenarios low_tps --seeds 2 --split train --workers 1   # quick check
python -m tests.router_core.loom_golden --write   # re-record golden traces (only on purpose)

# Data layer CLI
python -m data_layer.cli init-db | ping | status | inspect-state | reset-demo --force
```

Redis is optional everywhere (`docker compose up -d redis`); tests use `fakeredis`, and the router only touches Redis with `--redis`.

## Architecture

Four Python packages plus a React dashboard. Tests mirror the packages under `tests/<package>/` (never inline next to source); `tests/dashboard/` is Python backend tests for the WebSocket contract, while the React tests live in `dashboard/src/__tests__/`.

**Request path** (`router_core/router.py`, `BanditRouter._route`). Since Stage 0 every decision and belief update is made by a sync, I/O-free policy (`router_core/policy.py`: `decide(ctx, now, exclude) -> Decision`, `feedback(decision, observation, now)`, `should_retry`); `BanditRouter` is the async shell around `LoomPolicy` and `StaticBaselineRouter` around `StaticBreakerPolicy`. Steps 2–5 below live in `LoomPolicy`; `tests/router_core/test_policy_golden.py` pins them bit-for-bit to the pre-extraction router.

1. `POST /route` (`router_core/app.py`) → idempotency check by `transaction_id` (`idempotency.py`: replay / 409 in-flight / 422 body mismatch) → admission semaphore (`max_in_flight`, default 50).
2. **Perception** (`state.py`, `bandit.py`): each acquirer holds two wall-clock-decayed Beta beliefs: *technical* (did the acquirer process it; 2.3 s half-life, prior Beta(4,1)) and *approval* (did the issuer approve; 60 s half-life, prior Beta(1,1)). Thompson Sampling multiplies one draw from each. Decay is applied to every arm before each draw using an **injectable clock** (`BanditRouter(clock=...)`), so idle arms drift back to their prior.
3. **Smoothing** (`pid.py`, pure functions over immutable state): the one-hot Thompson winner is the setpoint, the PID's own previous output is the measurement, so it acts as a low-pass filter on the bandit. Output is projected onto a simplex with a 3% per-acquirer floor. The PID steps once per transaction (`dt=1.0`), not per second.
4. **Actuation**: `actuation_mode="deficit"` (round-robin on cumulative target − dispatched) or `"stochastic"` (categorical draw). Each payment still goes to exactly one acquirer.
5. **Value policy** (`value_policy.py`, opt-in, `value_scaled_config=None` by default): high-value payments (λ(V) ≥ `protect_lambda`) go straight to the best posterior mean and bypass the PID step and deficit counters entirely; lower values only blend samples toward the mean.
6. **Dispatch** over HTTP to `acquirer_sim` (one httpx pool per acquirer). The response is classified into `Outcome`: `AUTHORIZED`, `ISSUER_DECLINE` (approval belief only), `TECHNICAL_FAILURE` (technical belief only: codes in `technical_decline_codes`, non-200, timeouts, unreadable bodies), or `None` for router-side pool exhaustion (no belief update).
7. **After dispatch nothing may fail the payment**: state update, ledger write, Redis publish and WebSocket broadcast are all best-effort with `# noqa: BLE001` catches. Keep it that way; these are the only sanctioned broad excepts.

**Shared contracts.** `acquirer_sim/models.py` defines `AuthorizeRequest`/`AuthorizeResponse`, which `router_core` imports. `router_core/models.py::RoutingResult` is the envelope everything downstream consumes: the SQLite ledger, Redis events, the WebSocket frames, and `baseline_router` (which returns the same type so `get_psr_metrics()` SQL applies to both routers).

**Data layer** (`data_layer/`), wired in `create_router_app`:
- `MetricsLogger` (`sqlite_logger.py`): micro-batched async writer to a WAL SQLite ledger, **on by default**; the app refuses to start if it cannot open. `schema.sql` installs triggers that abort `UPDATE`/`DELETE` (append-only).
- `RedisSidecar` (`live.py`): **off by default**; publishes events and periodic belief snapshots through a bounded queue, restores beliefs at startup. `RedisBanditStateRegistry` (`redis_state.py`) exists but is not used by the served app.
- Beliefs, PID state, deficit counters and the idempotency store live in process memory.

**Telemetry.** `router_core/telemetry.py` gives each WebSocket client its own bounded queue (oldest dropped), so a slow dashboard never blocks `/route`. Frames (`BOOTSTRAP`, `ROUTING_COMPLETED`, `HEALTH_ALERT`, `PONG`) carry a per-process `stream_id` and a `sequence_number` in completion order. The router also proxies `/api/simulator/...` admin calls to the simulator (upstream failure → 502, never a fabricated answer).

**Dashboard** (`dashboard/`, Vite + React 18 + Tailwind, plain JS): `hooks/useLoomTelemetry.js` owns the socket lifecycle; `hooks/telemetryState.js` is a pure reducer (last 120 frames, gap/restart accounting) and is where most logic and tests belong. Rule (AUDIT F-27): **show only received or recorded data**. Missing values render as "no data", sequence gaps count as dropped frames, a new `stream_id` is a restart. The static-baseline line and benchmark table read JSON in `src/data/` generated by `compare_psr.py`; regenerate, never hand-edit. See `dashboard/README.md` for panes, tokens and keyboard map.

**Benchmark scripts** run the simulator in-process through `httpx.ASGITransport` (no ports) and pass a virtual 15 TPS clock so results don't depend on machine speed.

**Scenario suite** (`scripts/suite/`, Stage 0): a discrete-event harness (`des.py`) drives the same policy objects against `acquirer_sim/world.py` (failure taxonomy, capacity, segments, timelines, common random numbers keyed by `(seed, transaction_id)`). Policies: Loom served/bench/no-PID, `baseline_router/policies.py` (static M=3, competent breaker ± retry, SR window, SW-UCB) and a clairvoyant oracle. The 26 scenarios in `scenarios.py` were pre-registered in the decisions log before any result: changing one needs a new entry. Seeds: train `k`, test `1_000_000 + k`; tune on train only. `tests/scripts/test_suite.py` checks DES-vs-HTTP decision parity and pins the suite's Loom configs to `router_core.server` and `compare_psr.py`. The DES has no admission queue, and the oracle ignores capacity.

### Served vs benchmark configuration

These differ, and numbers from one must not be quoted for the other:

| | Served (`router_core.server`, `run_demo.py`) | Benchmark (`compare_psr.py`) |
| :-- | :-- | :-- |
| Acquirers | alpha, beta, gamma | alpha (95%), beta (94%) |
| Technical half-life | 2.3 s, real clock | 0.9 s, virtual 15 TPS clock |
| Actuation | stochastic (`PIDConfig` default) | deficit |
| Seeds | unseeded | sim `42 + 10k`, Loom `777 + k` (paired) |

Simulator seeds are spaced by 10 because acquirer `i` uses `default_rng(seed + i)`.

## Gotchas

- `pyproject.toml` sets `filterwarnings = error`: any new warning (including from a library upgrade; deps are unpinned) fails tests. Use the literal `422`, not the deprecated Starlette constant.
- `router_core/app.py` builds a default `app` at import time, which reads `DataLayerConfig` from env/`.env`. `tests/conftest.py` sets `LEDGER_ENABLED=false` so tests never write `loom_metrics.db`; a test that needs the ledger must pass its own `data_config`/path.
- PID gains are **not** read from `.env` (`PID_KP/KI/KD`, `APP_ENV`, `LOG_LEVEL` are ignored). Defaults live in `PIDConfig` (Kp 0.12, Ki 0.005, Kd 0.25, I_max 1.0, floor 0.03); override via server CLI flags. Only `DataLayerConfig` keys, `DECAY_HALF_LIFE_SEC` and `ROUTER_MAX_IN_FLIGHT` are read.
- New entrypoints must construct `PIDConfig` explicitly: `RouterConfig(pid_config=None)` silently means raw winner-take-all (this bug once survived three phase sign-offs; `tests/router_core/test_server_cli.py` guards it).
- `AcquirerStateConfig` takes either `half_life_sec` (wall clock) or `decay_factor` (legacy per-observation), not both.
- A few tests assert wall-clock latency and can flake on a loaded machine.
- `*.db` files are gitignored. `loom_metrics.db` is the live ledger; `compare_psr.py` refuses to use it and deletes its own two DBs on each single-seed run.

## Hard stops (`docs/CONSTITUTION.md`)

- Never call a real acquirer, sandbox or payment API. Simulated acquirers only.
- Never delete or overwrite the SQLite metrics ledger (`reset-demo --force` only when the user asks for a demo reset).
- Never hardcode acquirer IDs or counts in a way that can't be reconfigured.
- Never commit secrets, even mock ones.
- The tech stack is pinned (numpy/scipy, FastAPI, pydantic, redis-py, aiosqlite, React with native WebSocket; no ML frameworks). Changing it, or anything else in the constitution, needs a decisions-log entry first.
- `docs/decisions-log.md` is append-only: add a new entry (`[Audit F-xx, Phase Nx] Decision — alternatives — why`), never edit a past one.

## Working conventions

- One remediation phase = one `audit-*` branch = one PR into `main`, titled `Phase Nx: <what> (F-xx, F-yy)`. Don't push, merge or open PRs without the user's go-ahead.
- Order of work for a finding: decisions-log entry → code + tests in the same PR (regression tests should fail against the old code) → status note at the top of `docs/AUDIT.md` (one sentence per finding; the audit body below it is never rewritten) → README numbers regenerated from scripts.
- A historical doc that turned out wrong gets a correction note at its top, not a silent rewrite. Phase specs and QA reports in `docs/` are historical; where they disagree with the README, the README has the reproduced values.
- Every published number must come from a committed script with fixed seeds, and the README cites the command. Comparisons use paired multi-seed runs with 95% t-intervals.
- Writing style for README, docs and PRs: plain, short, factual. State losses as plainly as wins (the README leads with Loom *losing* to a 3-failure circuit breaker on hard outages), no marketing words, say what a test did not cover.
- Commits use `fix:` / `feat:` / `docs:` / `ci:` with finding IDs in parentheses, e.g. `fix: apply value-scaled protection at dispatch, with the PID on (AUDIT F-10)`. Body: problem, change, measured before/after, trade-offs.
- Python: ruff line length 100, rules E/W/F/I/B/BLE/UP/RUF (no bare or blind `except`), mypy strict, one-line docstrings on public functions, and state-mutating functions must make the side effect obvious in their name or docstring.
- JS: `PascalCase.jsx` components, `camelCase` elsewhere.
