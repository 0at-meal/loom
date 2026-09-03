# Loom — Decisions, Interfaces & Risks Log

**Append-only. This is the record of what actually got decided and found, growing phase by phase — it doesn't get rewritten, only added to. The PRD describes the intended system; this is what happened while building it.**

Each phase's Architect pass should add a Decision and/or Interface entry here before handoff to the Engineer role. Each phase's QA and Tech Lead passes should add to Open Risks as they find things — don't wait for a "risks phase," there isn't one.

---

## Decisions Log

Format: `[Phase] Decision — alternatives considered — why this one won`

- **[Pre-build] Gateway health signal = decayed (EWMA-style) success rate.** Alternative: plain rolling-window average. Chosen because decay updates smoothly on every new observation rather than jumping when an old data point falls out of a fixed window, and it composes naturally with Thompson Sampling's own weighted, probabilistic updates.

- **[Pre-build] Bandit algorithm = Thompson Sampling with decay.** Alternatives: epsilon-greedy (fixed exploration rate — rejected because it can't adapt exploration to actual confidence), UCB (viable alternative — rejected because its optimistic-bound output composes less cleanly with PID's need for a smooth probabilistic signal than Thompson Sampling's Beta-distribution samples do).

- **[Pre-build] Demo architecture = live simulation, not replay.** Alternative: replay-based demo from pre-recorded data. Chosen for credibility — a live, on-demand-triggered outage is more convincing evidence the system works than a chart generated from a script, at the cost of less control over what could visibly go wrong during a demo.

- **[Pre-build] Simulated acquirers only, no real sandbox.** Chosen because the demo needs a reliably scriptable outage (controllable success rate + on-demand outage trigger) that a real sandbox API's behavior can't guarantee on command.

- **[Pre-build] Headline metrics = PSR lift (primary) + oscillation dampening (visual proof).** Chosen because PSR lift is the metric with real economic weight (Unit 1's PSR-vs-fees argument); oscillation dampening is the visible evidence that the PID layer achieved that lift smoothly rather than through jerky, unstable swings.

- **[Pre-build] Value-scaled exploration is explicitly out of scope for the core build.** Tracked as future/stretch work only — not a committed feature, not to be started before Phases 1–7 are demo-ready.

- **[Pre-build] Product name = Loom.**

## Interface Contracts

*(Populated as each phase's Architect pass defines a boundary. Nothing here yet — Phase 1 will be the first entry.)*

## Open Risks

*(Rolls forward from the PRD, then grows as QA/Tech Lead surface new ones per phase.)*

- **PID gain tuning isn't computed analytically** — the P/I/D constants will need real manual tuning against the simulated outage script to get a damping curve that actually looks good on the dashboard. Budget real time for this, not just implementation time. *(from PRD)*
- **Live simulation has more surface area to misbehave in front of an audience** than a scripted replay would. Worth rehearsing the exact outage-trigger sequence before it needs to work under pressure. *(from PRD)*
- **"Good enough" PSR lift isn't yet defined as a number.** Worth deciding what result would actually be convincing before the Phase 6 baseline comparison is built, so the target isn't retrofitted after the fact. *(from PRD)*

---

## How to use this file

- Before each phase's Architect pass, paste in the relevant prior entries (not the whole file if it gets long — just the Decisions/Interfaces that phase actually depends on).
- After each phase's Architect pass produces a new contract, append it under Interface Contracts, phase-tagged.
- After QA or Tech Lead finds a gap, ambiguity, or risk, append it under Open Risks immediately — don't hold it for a later "cleanup" pass.
- Nothing here gets deleted or rewritten once added. If a decision changes later, add a new entry noting the change and why, rather than editing the old one — the history of what changed is itself useful.
