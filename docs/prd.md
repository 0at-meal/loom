# Loom — Product Requirements Document

**Internal build doc. Solo project. No fixed deadline — this document exists to keep the build order honest, not to hit a date.**

## What this is

Loom is a payment-routing layer that treats acquirer selection as a live control problem instead of a static rulebook. A bandit algorithm continuously learns which acquirer is healthiest right now; a PID controller turns that belief into a smooth traffic reallocation instead of a hard switch. The whole thing runs against a live, scriptable simulation — no real acquirers, no real money — because the point of this build is to prove the *control loop* works, not to ship a production payments product.

## Why this exists

Static, rule-based routers have two failure modes and they're mirror images of each other: overreact to a two-second blip and you get herd migration onto a route that wasn't provisioned for the sudden traffic; underreact and you bleed transactions to a route that's been dying for ten minutes. Both failures trace back to the same root cause — a fixed threshold has no concept of duration or trend, so it can't tell a blip from a real outage. Loom exists to replace that threshold with something that actually learns (the bandit) and actually reacts proportionally (PID), and to make that difference visible and measurable rather than theoretical.

The metric that matters is PSR — payment success rate — not fee optimization. A 1% PSR drop removes 1% of total transaction volume from the top line; a 0.1% fee saving is a rounding error by comparison. Everything in this build is in service of moving that one number, with a visual proof (allocation smoothness during an outage) that backs up *how* it moved.

## Goals

- Demonstrate a live bandit + PID control loop reacting to a real-time, on-demand-triggered simulated outage.
- Produce a measurable PSR lift over a static-routing baseline, computed from the same simulated traffic and outage conditions.
- Produce a visible, smooth traffic-reallocation curve during that outage — no step function, no oscillation ringing.
- Keep the routing decision itself fast enough that latency is never the bottleneck in the demo.

## Non-goals (explicitly out of scope for this build)

- Real acquirer or sandbox integration. Simulated acquirers only — a real sandbox can't guarantee a scriptable, on-demand outage, and that controllability is the whole point of the demo.
- Value-scaled exploration. This is a real stretch goal (see below) but is not required for the core loop to work or to be demonstrable.
- Anything from the "productionize this" list: multi-region routing, PCI-scoped data handling, clustered/replicated data stores, canary/shadow deployment, a manual kill switch. All of that matters the moment this touches real money and real cards; none of it is needed to prove the control loop works.

## System overview

Four pieces, in a straight pipeline:

1. **Simulation harness** — a transaction generator plus a set of simulated acquirer services (FastAPI), each with a controllable success rate and a scriptable outage trigger.
2. **Router core** — the actual project. Thompson Sampling with decay produces a target allocation per acquirer from the decayed health signal; PID takes that target and smooths the actual traffic split toward it.
3. **Data layer** — Redis for health state and pub/sub (so the dashboard doesn't have to poll the router), SQLite for the transaction/outcome log that the PSR-lift number gets computed from afterward.
4. **Live dashboard** — React, fed over WebSocket, showing traffic allocation per acquirer over time, rolling PSR, per-acquirer health, and outage-trigger controls.

## Functional requirements, by component

**Simulation harness**
- Each simulated acquirer exposes a mock "authorize" endpoint and admin endpoints to set success rate and toggle an outage state.
- Outage state supports at minimum a hard on/off; a gradual degrade/recover curve is a nice-to-have, not required for v1.
- Transaction generator emits at a configurable rate and can be started/stopped independently of the router.

**Router core**
- Maintains per-acquirer Beta-distribution parameters (Thompson Sampling belief) and a decayed success-rate health score, updated on every transaction outcome.
- On each transaction, samples from each acquirer's distribution and produces a raw target allocation.
- PID layer consumes that target and the current actual allocation, and outputs the smoothed allocation actually used to route the next transaction — proportional, integral, and derivative terms all need to be independently tunable (gain constants), since the "good damping" behavior only shows up with reasonable tuning, not automatically.
- Routing decision (sample + PID update) must complete well within the demo's latency budget — this should never be the visible bottleneck.

**Data layer**
- Redis holds live health state and publishes every routing decision + outcome for the dashboard to consume.
- SQLite logs every transaction: timestamp, chosen acquirer, allocation weight at time of routing, outcome. This log is what both the PSR-lift computation and any post-hoc debugging depend on — it needs to exist from day one, not get bolted on at the end.

**Baseline (for comparison, not part of the "product" itself)**
- A simple static rule-based router, run against the identical simulated traffic and outage script, to produce the PSR number Loom's lift is measured against. Without this, "PSR lift" is just an assertion.

**Live dashboard**
- Real-time chart of traffic allocation per acquirer — this is the artifact that has to visibly show smooth easing during an outage, not a cliff.
- Rolling PSR, per-acquirer health signal, and buttons to trigger/clear an outage on a chosen acquirer live during a demo.

## Success metrics

- **PSR lift**: Loom's PSR during a simulated outage window, compared to the static-baseline router's PSR over the same window. This is the headline number.
- **Oscillation dampening**: qualitative but demo-critical — the allocation chart during an outage should show a smooth easing curve, not a step function or overshoot-and-ring pattern. This is the visual proof that backs the PSR number up.

## Build order

No deadline, so the order below is sequenced by dependency and by "can I actually test this piece in isolation before wiring it to anything else," not by calendar.

1. **Health signal + bandit state, standalone.** Beta-distribution update logic and decayed health score, tested against a hand-scripted sequence of outcomes before any service exists around it.
2. **Simulated acquirer service.** Scriptable success rate and outage toggle, callable directly, no router involved yet.
3. **Wire router (bandit only, no PID) to the simulator and a transaction generator.** This is deliberately the point where you should be able to *see* the oscillation problem happen live — hard-switching on raw bandit output — before building the thing that fixes it.
4. **PID layer**, smoothing the bandit's raw target into the actual allocation. Compare the same outage script with and without this layer running.
5. **Data layer** — Redis pub/sub and the SQLite outcome log. Log everything from here forward so nothing downstream is working off incomplete data.
6. **Static baseline router**, run against the same script, so the PSR-lift number has something real to compare against.
7. **Dashboard.** Only once the router is actually producing a smooth, loggable signal is there something worth visualizing live.
8. **Stretch: value-scaled exploration** as a policy layer on top of the existing bandit — dial down exploration for high-value transactions, leave it normal for low-value ones. Explicitly optional, and shouldn't be started until 1–7 are demo-ready.

## Open questions / risks

- PID gain tuning (the P/I/D constants) isn't something you compute analytically here — it'll take some manual tuning against the simulated outage script to get a damping curve that actually looks good on the dashboard. Budget real time for this, not just implementation time.
- A live simulation is more convincing than a replay but has more surface area to misbehave in front of an audience — worth rehearsing the exact outage-trigger sequence you'll run in a real demo before you need it to work under pressure.
- "Good enough" PSR lift hasn't been defined yet as a number — worth deciding what result would actually be convincing before the baseline comparison is built, so the target isn't retrofitted after the fact.

## Future work (not this build)

Value-scaled exploration (above), plus everything in the non-goals list once/if this ever needs to point at real acquirers instead of simulated ones: durable clustered state, real integrations, PCI scope, observability and alerting on the router itself, and a manual override to force static routing if the learned system misbehaves.
