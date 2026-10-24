# Loom: Context Transfer

*Handoff for continuing Loom in a fresh Claude Code setup (new account, no prior sessions or memory). Written 2026-10-24 against branch `audit-value-dispatch-dashboard`, local HEAD `caf3784`.*

Read this file first, then the files in [§9](#9-what-to-read-and-in-what-order). Everything here can be checked against the repo; where this file and the code disagree, the code wins.

---

## 1. Where things stand (TL;DR)

- **Loom** is a payment-routing experiment: Thompson Sampling picks the healthiest simulated acquirer, and a PID-style filter smooths the bandit's choice into a slowly changing allocation weight. Everything runs against a local acquirer simulator with injectable outages. No real payment APIs, ever.
- The original build ran as **Phases 1–8** (spec → code → QA → tech-lead review per phase). An **independent adversarial audit** ([`docs/AUDIT.md`](AUDIT.md), at commit `8001acb`) then found the headline claims oversold and listed findings F-01 … F-34.
- Since then the work has been **audit remediation**, one PR per remediation phase (PRs #1–#7). PRs #1–#6 are merged into `main`.
- **PR #7 is open**: [0at-meal/loom#7](https://github.com/0at-meal/loom/pull/7), "Phase 4b: value-scaled protection at dispatch, dashboard honesty and lifecycle (F-10, F-27)", branch `audit-value-dispatch-dashboard`. CI (`quality-and-test`, `dashboard`) was green on the pushed head (`87e223c`).
- **Two local commits are not pushed** (see [§3](#3-git-state-and-what-to-do-before-switching)): the Bloomberg-style dashboard layout (`cc6bb59`) and the from-scratch explainer (`caf3784`). Push them before relying on a different machine or clone.
- The honest result: Loom **loses** to a standard 3-failure circuit breaker on hard outages (−3.09 pp PSR, 100 seeds), **wins** on gray failures (+1.97 pp), and sends too much traffic to a slightly worse healthy acquirer. The README says this up front; keep it that way.

---

## 2. Repository

| Item | Value |
|---|---|
| Local path (old setup) | `D:\loom` (Windows 11, PowerShell + Git Bash) |
| Remote | `https://github.com/0at-meal/loom` |
| Default branch | `main` |
| Working branch | `audit-value-dispatch-dashboard` (PR #7) |
| Other branch | `audit-hygiene` (merged as PR #6; can be deleted) |
| Git author | Niraj Jadhav |
| Python | 3.11 (`.venv/` in repo root, not committed) |
| Node | 20 (CI), Vite 5 + React 18 + Tailwind 3 + Vitest 2 dashboard |
| Tests | 358 Python tests (`pytest`), 27+ Vitest tests (`dashboard/`); ~87% coverage |

### Layout

```
router_core/      bandit (state.py, bandit.py), PID (pid.py), value_policy.py (Phase 8),
                  router.py (pipeline), app.py / server.py (FastAPI, port 8000)
acquirer_sim/     simulated acquirers + admin/fault endpoints (port 8001)
data_layer/       SQLite append-only ledger (sqlite_logger.py, schema.sql), Redis state/pubsub, cli.py
baseline_router/  static priority + circuit-breaker comparator
dashboard/        Vite/React mission-control UI (port 5173), telemetry reducer in src/hooks/telemetryState.js
scripts/          run_demo.py, compare_psr.py, steady_state_share.py, value_policy_outage.py,
                  concurrency_inflight.py, simulate_outage.py, generate_transactions.py, ...
tests/            mirrors the packages above
docs/             PRD, CONSTITUTION, decisions-log, AUDIT, WHITEPAPER_L1, phase specs + QA reports, personas
LOOM_TECHNICAL_EXPLAINER.md   (repo root) long-form explainer of the whole system, at cc6bb59
```

---

## 3. Git state and what to do before switching

At handoff:

```
## audit-value-dispatch-dashboard...origin/audit-value-dispatch-dashboard [ahead 2]
?? .claude/
?? docs/CONTEXT_TRANSFER.md   (this file)
```

Unpushed commits on the PR branch:

| Commit | Message |
|---|---|
| `cc6bb59` | feat: Bloomberg-style terminal layout for the dashboard |
| `caf3784` | docs: add a from-scratch Loom explainer |

Untracked: `.claude/launch.json` (dev-server configs for the Claude desktop app's preview: `loom-demo` → `python scripts/run_demo.py --tps 15` on 8000, `dashboard` → `npm --prefix dashboard run dev` on 5173). Commit it or recreate it; it holds no secrets.

**Before moving to the new setup** (from the old one, or from any clone that has these commits):

```bash
git add docs/CONTEXT_TRANSFER.md
```

```bash
git commit -m "docs: add a context transfer document for a new setup"
```

```bash
git push origin audit-value-dispatch-dashboard
```

Pushing adds `cc6bb59` and `caf3784` to PR #7, so CI re-runs and the PR description should mention the new dashboard layout and explainer (it currently describes only F-10 and F-27). If the new setup is on the same machine and folder, nothing is lost either way, but push anyway so the work is not only local.

**Not in git** (gitignored, recreate locally): `.env` (copy from `.env.example`), `.venv/`, `dashboard/node_modules/`, all `*.db` files (`loom_metrics.db`, `baseline_metrics.db`, `compare_psr_*.db`), `.coverage`, caches. `loom_metrics.db` is the live ledger; the constitution forbids deleting or overwriting it (use `data_layer/cli.py reset-demo` only for demo rehearsal, deliberately).

---

## 4. Setup on the new machine or account

```bash
git clone https://github.com/0at-meal/loom.git
```

```bash
cd loom && git checkout audit-value-dispatch-dashboard
```

```bash
python -m venv .venv
```

Activate (`.venv\Scripts\Activate.ps1` on PowerShell, `source .venv/bin/activate` elsewhere), then:

```bash
pip install -e ".[dev]"
```

```bash
npm --prefix dashboard ci
```

```bash
cp .env.example .env
```

Redis is optional (`docker compose up -d` for Mode A; the served router runs without it by default). The SQLite ledger is on by default.

New account checklist:

- `gh auth login` with an account that has push access to `0at-meal/loom` (needed for PR #7 and new PRs).
- Recreate `.claude/launch.json` if it was not committed (contents in §3).
- Claude Code memory from the old account does not transfer. The old project memory folder was empty, so nothing is lost; this file replaces it. Consider running `/init` to create a `CLAUDE.md`, and copy the conventions in §6 into it.

### Verify the checkout (what CI runs)

```bash
ruff check .
```

```bash
ruff format --check .
```

```bash
mypy .
```

```bash
pytest --cov=router_core --cov=acquirer_sim --cov=data_layer --cov=baseline_router --cov-report=term-missing
```

```bash
npm --prefix dashboard test
```

```bash
npm --prefix dashboard run build
```

### Run it

```bash
python scripts/run_demo.py --tps 15
```

```bash
npm --prefix dashboard run dev
```

Then open http://localhost:5173. The README Quickstart (§4–§8) covers component-by-component runs, fault injection (`scripts/simulate_outage.py`) and benchmarks.

---

## 5. Architecture in one screen

1. A payment hits `POST /route` on the router (`router_core/app.py`). Idempotent by `transaction_id` (in-memory, 24 h, per process). An admission limit (`ROUTER_MAX_IN_FLIGHT`, default 50) bounds concurrent dispatches.
2. Each acquirer has **two wall-clock-decayed Beta beliefs**: technical health (2.3 s half-life; only `ACQUIRER_OUTAGE`, HTTP errors, timeouts and unreadable replies count against it) and issuer approval (60 s half-life; every other decline code). Thompson Sampling draws from their product.
3. With the PID on (default), the draw sets a one-hot target; the PID (`Kp=0.12, Ki=0.005, Kd=0.25, I_max=1.0`, floor `w_min=0.03`) moves the allocation weight at most ~12.5% per transaction; deficit (benchmark) or stochastic (served) actuation turns weights into a single acquirer per payment.
4. **Phase 8 value policy (opt-in, `value_scaled_config=None` by default).** If λ(V) = 1 − e^(−V/τ) ≥ `protect_lambda` (0.95), the payment goes to the highest posterior mean and skips the PID step and the draw. Below that, samples are blended toward the mean. This dispatch-time protection is PR #7's main change (F-10).
5. Every decision is logged to the SQLite ledger (append-only triggers) and broadcast over WebSocket to the dashboard; with `--redis`, events and belief snapshots also go to Redis.
6. The dashboard (PR #7 + `cc6bb59`) is a multi-pane "terminal": status bar, key figures, allocation chart, acquirer table, event tape, benchmark table, simulation controls, F1–F6 shortcuts. **F-27 rule: it shows only live or recorded data.** Missing values read "no data"; skipped sequence numbers count as dropped frames; a new `stream_id` marks a router restart. The static-baseline line replays `baselineReferenceRun.json`, regenerated by `compare_psr.py --out-reference-json`.

Key reproducible numbers and the scripts that produce them are in the README "Results at a Glance" and "Value-Scaled Exploration" sections. Do not quote numbers that no committed script produces.

---

## 6. How the work is done (conventions to keep)

These are the habits established over PRs #1–#7. A new session should follow them without being told.

**Process**

- One remediation phase = one branch = one PR into `main`, titled `Phase Nx: <what> (F-xx, F-yy)`. Branch names start with `audit-`.
- Every PR that closes audit findings also **updates the status note at the top of `docs/AUDIT.md`** (one sentence per finding: what changed, what is still open). The audit body below the note is never rewritten.
- **`docs/decisions-log.md` is append-only.** New decisions go in as new entries (`[Audit F-xx, Phase Nx] Decision — alternatives — why`); never edit past entries. The same applies to `docs/CONSTITUTION.md` changes: log first.
- Old docs that turned out wrong get a **correction note at the top** (as `docs/phase8-tech-lead-review.md` now has), not a silent rewrite.
- Every claimed number comes from a committed script with a fixed seed set; README tables cite the command. Multi-seed with paired seeds and 95% t-intervals for any comparison.
- Tests come in the same PR as the code. Regression tests should fail against the old code.

**Commits and PRs**

- Conventional prefixes: `fix:`, `feat:`, `docs:`, `ci:`. Subject in plain words, finding IDs in parentheses, e.g. `fix: apply value-scaled protection at dispatch, with the PID on (AUDIT F-10)`.
- Body: problem, change, measured before/after, trade-offs. End with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` (or whatever attribution the new setup specifies).
- PR descriptions follow PR #7's shape: Problem, Change, Before/after table, Trade-offs (measured), Verification (local) listing exactly what was and was not run. End with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- Don't push, merge, or open PRs without the user's go-ahead.

**Writing style (README, docs, PRs)**

- Plain, short, factual sentences. State losses as plainly as wins. No marketing words ("blazing", "robust", "revolutionary"), no unmeasured claims.
- Say what a test did not cover (e.g. "not exercised against a live router plus traffic").
- Specific numbers with their configuration and seed count.

**Code**

- Python 3.11, ruff (line length 100, rules E/W/F/I/B/BLE/UP/RUF; no bare `except`), mypy `--strict`, pytest with `filterwarnings = error` (new warnings break tests; Starlette's httpx TestClient warning is explicitly ignored).
- Avoid the deprecated Starlette 422 constant; use the literal status or the current name (fixed twice already).
- Public functions get a one-line docstring; side effects on shared state must be obvious from the name.
- Don't hardcode acquirer IDs or counts.
- Never call a real acquirer, sandbox, or payment API. Never commit secrets (even mock ones).

---

## 7. Audit finding status (best reading at handoff)

Confirm against the status note at the top of [`docs/AUDIT.md`](AUDIT.md) before acting; it is the authoritative record.

| Status | Findings |
|---|---|
| **Closed** | F-02 (decline attribution), F-04 (ledger wired), F-05 (telemetry off the payment path), F-06 (numbers regenerated), F-07 (post-dispatch safety, idempotency), F-10 (dispatch protection, PR #7), F-12 (logger loss), F-15 (seeds, multi-seed CIs), F-18 (admin proxies return 502), F-23 (wall-clock decay), F-26 (simulator typos, LATENCY_SPIKE), F-27 (dashboard honesty, PR #7), F-32 (non-finite PID inputs), F-34 |
| **Partly done** | F-01 (README now leads with the loss; the *competent* comparator, i.e. decline-aware breaker + sane all-tripped policy + single retry, is not built), F-03 (steady-state share reduced, remaining 1-point gap documented as a measured limit), F-11 (documentation only), F-13 (silent forwarder removed; multi-process Redis state still open), F-14 (no lockfile yet), F-16 (admission limit added; adaptive limit not), F-29 (counts/phase docs corrected; recheck after PR #7), F-33 (sequence numbers still in completion order and reset per process; `stream_id` makes restarts detectable) |
| **Open** | F-08 (herd protection never measured: simulator has no capacity limits or 429s), F-09, F-17 (volume-based 3% floor; no time-based probing), F-19 / F-20 (process and retrofitted-metric criticisms; addressed by honesty in docs, not code), F-21 (latency claims; check README wording), F-22 (no eligibility rules, kill switch, fail-open default, authN/Z, admin-plane separation; CORS `*` with credentials), F-24 (Pub/Sub at-most-once; subscriber validates every message as a RoutingEvent), F-25 (integrator can exceed `I_max` for K ≥ 3 after mean-centring), F-28 (tests that miss their target bugs), F-30 (benchmark logging/unit asymmetries), F-31 (single-process throughput) |

---

## 8. Next steps (suggested order)

1. **Finish PR #7.** Push `cc6bb59` and `caf3784`, update the PR description to cover the terminal-style dashboard and the explainer, confirm CI is green, and run the dashboard once against a live router with traffic (`run_demo.py` + `npm run dev`), which PR #7 says was not done. Then merge on the user's approval.
2. **Competent baseline (F-01, AUDIT roadmap step 3).** Add a decline-aware breaker with a wall-clock half-open probe, a sane all-tripped policy and one retry on technical failure; rerun `compare_psr.py --n-seeds 100` and publish whatever wins.
3. **Capacity in the simulator (F-08, F-09; README "What's Next" #3).** Concurrency limits and HTTP 429 on backup acquirers, so the herd-migration case for the PID can be measured, or the smoothing claim dropped.
4. **Time-based probing (F-17; What's Next #4).** Replace the volume-based 3% floor with wall-clock probes; consider a wall-clock PID step.
5. **Small correctness fixes:** F-25 integrator clamp for K ≥ 3 (re-clamp after centring, test K=3,5), F-24 subscriber accepting health alerts, F-28 tests that actually exercise shutdown flush and restart routing, F-14 lockfile.
6. **Hardening (F-22, AUDIT step 2):** fail-open default acquirer, kill switch, admin-plane separation, CORS fix; then shadow mode and multi-process state (F-13, What's Next #1).

Each step: decisions-log entry first, then code + tests, then AUDIT status note, then README numbers regenerated from scripts.

---

## 9. What to read and in what order

1. This file.
2. [`README.md`](../README.md): results, limitations, quickstart. The limitations section is the honest summary.
3. [`docs/AUDIT.md`](AUDIT.md): status note (top), §2 top issues, §3 findings table, §7 roadmap. The appendix is long scratch code; skip it unless reproducing.
4. [`docs/CONSTITUTION.md`](CONSTITUTION.md): pinned stack and hard stops.
5. [`docs/decisions-log.md`](decisions-log.md): skim the Decisions list; the last entries are the audit-era ones.
6. [`LOOM_TECHNICAL_EXPLAINER.md`](../LOOM_TECHNICAL_EXPLAINER.md): the full system explained from scratch, if more depth is needed. [`docs/WHITEPAPER_L1.md`](WHITEPAPER_L1.md) covers the maths.
7. Phase specs and QA reports in `docs/` are historical records; where they disagree with the README, the README numbers are the reproduced ones.

---

## 10. Starter prompt for the new session

Paste this into the first message of the new Claude Code session, from the repo root:

> Read `docs/CONTEXT_TRANSFER.md`, then `README.md` and the status note at the top of `docs/AUDIT.md`. We are continuing audit remediation on Loom. The current branch is `audit-value-dispatch-dashboard` (PR #7, open). Check `git status` and whether `cc6bb59` and `caf3784` are on the remote, run the CI checks from §4, and then tell me what is left to merge PR #7. Follow the conventions in §6 of the transfer doc.
