# Loom — CONSTITUTION.md

**Fixed ground rules. Unlike the Decisions Log, this file doesn't grow phase by phase — it's what every role invocation, at every phase, treats as already settled. If something here needs to change, that's itself a decision worth recording in the Decisions Log, with a note on why, before this file gets edited.**

---

## Tech stack (pinned)

Don't re-decide this per phase. Don't substitute a library because it's more familiar unless it's recorded as a new decision in the log first.

- **Router core:** Python (bandit: `numpy`/`scipy.stats.beta` for Thompson Sampling; PID: plain arithmetic, no library needed)
- **Simulated acquirer + transaction generator services:** `fastapi` + `uvicorn`, `pydantic` for validation
- **Data layer:** `redis-py` (health state + pub/sub), `sqlite3`/`aiosqlite` (metrics log)
- **Dashboard:** React, connected via native WebSocket (or FastAPI's WebSocket support on the backend side)
- No PyTorch, TensorFlow, or any gradient-based ML library — there is nothing in this system that's a learned model; if a future phase seems to need one, that's a signal to stop and re-check against the architecture, not to add the dependency.

## Folder structure

```
loom/
  router_core/        # bandit + PID engine (Phase 1, 3, 4, 8)
  acquirer_sim/        # simulated acquirer services (Phase 2)
  data_layer/           # redis client, sqlite schema + access (Phase 5)
  baseline_router/     # static rule-based comparison router (Phase 6)
  dashboard/            # React frontend (Phase 7)
  scripts/              # transaction generator, demo/outage orchestration
  tests/                # test suites, mirroring the module structure above
  docs/
    PRD.md
    decisions-log.md
    CONSTITUTION.md
```

Each component's tests live under `tests/<component_name>/`, not inline next to source, so QA's output has one consistent place to land regardless of which phase or role produced it.

## Naming conventions

- **Python:** `snake_case` for files, functions, variables. `PascalCase` for classes. Module names match the folder above (e.g. `router_core/bandit.py`, `router_core/pid.py`).
- **JavaScript/React:** `camelCase` for variables/functions, `PascalCase` for component names and files (`AllocationChart.jsx`), `kebab-case` for non-component files.
- **Environment variables:** `UPPER_SNAKE_CASE`.
- **Redis keys:** namespaced, colon-separated — `acquirer:{id}:health`, `acquirer:{id}:beta`.
- **SQLite tables:** `snake_case`, plural — `transactions`, `acquirer_outcomes`.
- **Phase references in commits/PRs:** prefix with the phase number, e.g. `[Phase 4] add PID smoothing layer`.

## Coding standards

- Type hints on Python function signatures where the type isn't obvious from context.
- No bare `except:` — catch and handle (or explicitly re-raise) specific exceptions. This matters more than usual here, since Unit 5's whole latency-budget argument depends on failures being visible, not silently swallowed.
- Public functions get a one-line docstring stating what they do, not how.
- A function that updates shared state (health signal, Redis, SQLite) should make that side effect obvious from its name or docstring — no silent mutations buried inside something that looks like a pure read.
- Tests accompany the code they test in the same phase's deliverable, not deferred to a later cleanup pass.

## External documentation (reference, don't guess)

When implementing against a library, check the real docs rather than reconstructing the API from memory — this matters more across phase boundaries where a different invocation might otherwise assume a slightly different API surface.

- FastAPI: https://fastapi.tiangolo.com/
- Redis-py: https://redis.readthedocs.io/
- scipy.stats (Beta distribution): https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.beta.html
- React: https://react.dev/
- WebSocket API (MDN): https://developer.mozilla.org/en-US/docs/Web/API/WebSockets_API

## Never do automatically

These aren't phase-specific — any role, at any phase, should treat these as hard stops, not judgment calls.

- **Never call a real acquirer, sandbox, or payment API.** Simulated acquirers only, per the explicit scope decision in the Decisions Log. If a phase seems to need real integration, that's a signal to stop and flag it, not proceed.
- **Never commit secrets or credentials**, even mock ones for the simulator's fake auth tokens — use placeholder/env-var patterns from day one so the habit doesn't need retrofitting later.
- **Never delete or overwrite the SQLite metrics log.** Append-only, matching the Decisions Log's own philosophy — the PSR-lift comparison in Phase 6 depends on this history being intact and untouched.
- **Never edit a past entry in the Decisions Log.** Add a new entry noting the change and why, rather than rewriting history.
- **Never skip the QA or Tech Lead step in a phase to save time.** The loop exists specifically to catch a bad assumption before it's built on top of for three more phases.
- **Never expand scope mid-phase** (e.g. start on Phase 5 while "finishing up" Phase 3) without a fresh Architect pass and a corresponding Decisions Log entry first.
- **Never hardcode the acquirer count or IDs** in a way that can't be reconfigured — the simulation harness has to stay scriptable, per the PRD's own definition of what a simulated acquirer needs to provide.
- **Never change anything pinned in this file** (stack, structure, conventions) without recording why in the Decisions Log first and getting a Tech Lead pass on it — this file is the thing that's supposed to *not* need re-deciding every phase.
