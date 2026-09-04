# Phase 3 First-Principles Explainer: The Closed-Loop Bandit Pipeline

Imagine a train dispatcher routing express trains across two tracks. Track A is faster than Track B. When a landslide blocks Track A, panicking and flipping the track switch back and forth on every incoming train causes cars to screech, buffer, and collide.

Phase 3 of Loom builds the digital equivalent: an end-to-end, closed-loop payment routing pipeline connecting a synthetic **transaction generator** to a **bandit router** and **simulated acquirers** (payment processors). Crucially, Phase 3 implements routing *without* PID smoothing, directing 100% of each transaction to whichever acquirer currently looks best.

This document derives the pipeline mechanics from first principles, explains why raw multi-armed bandits oscillate violently, audits QA edge cases, and establishes the baseline Phase 4's PID controller must fix.

---

## 1. What Problem This Solves (And What Breaks Without It)

Before Phase 3, Loom consisted of disconnected parts: Phase 1's mathematical belief engine ran in isolated memory with hardcoded vectors, while Phase 2's mock gateways sat idle awaiting manual calls.

A payment router requires a **closed-loop feedback system**:
1. **Perception & Action**: The router evaluates beliefs, draws random samples, and commits a transaction to a gateway.
2. **Network Interaction**: The router dispatches authorization over HTTP and awaits a response.
3. **Sensory Feedback**: The gateway response immediately mutates beliefs, altering routing for the next transaction.

Without Phase 3, Loom has no runtime system. Furthermore, we cannot prove *why* a PID controller is necessary without first exposing the raw instability of unbuffered Thompson Sampling under stress to establish an indisputable baseline.

---

## 2. Core Mechanism and Mathematical Derivation

### Indivisibility of Payment Authorizations
Actuators in classical control theory output continuous signals (e.g., opening a valve 43.5%). But payments are **discrete and indivisible**. A customer paying $100 cannot split funds across multiple processors; 100% must commit to a single gateway:

$$w_t \in \{(1, 0), (0, 1)\}$$

### Thompson Sampling Argmax Selection
Each acquirer $i$ has an unknown success rate $\theta_i \in [0, 1]$, modeled as a **Beta distribution** $\text{Beta}(\alpha_i, \beta_i)$ tracking decayed successes $\alpha_i$ and failures $\beta_i$.

Under **Thompson Sampling**, the router balances exploration and exploitation by drawing a random sample from each arm:

$$\tilde{\theta}_i \sim \text{Beta}(\alpha_i, \beta_i)$$

The router applies a **winner-take-all argmax rule**, committing 100% of the transaction to the highest sample:

$$A^* = \arg\max_i \tilde{\theta}_i$$

### Why Raw Bandits Inevitably Flap Near Decision Boundaries
The variance of a Beta distribution is:

$$\sigma^2 = \frac{\alpha \beta}{(\alpha + \beta)^2 (\alpha + \beta + 1)}$$

When two acquirers have similar health (Alpha at 95% and Beta at 94%), their posterior means $\mu_A$ and $\mu_B$ are close, creating substantial distribution overlap. When an outage hits Alpha, Phase 1's offset decay rule penalizes Alpha on every failure:

$$\alpha_{t} = 1.0 + \gamma (\alpha_{t-1} - 1.0), \quad \beta_{t} = 1.0 + \gamma (\beta_{t-1} - 1.0) + 1.0$$

Alpha's mean declines gradually over several transactions. As $\mu_A$ crosses $\mu_B$, both distributions maintain statistical variance ($\sigma \approx 0.08$). Consequently, the probability of Alpha out-sampling Beta, $P(\tilde{\theta}_A > \tilde{\theta}_B)$, hovers near 50%.

Because argmax routing is binary, tiny sample variations flip the entire traffic stream:
- Tx 53: Alpha draws 0.952, Beta draws 0.632 $\to$ 100% to Alpha (Fails)
- Tx 54: Beta draws 0.874, Alpha draws 0.800 $\to$ 100% to Beta (Succeeds)
- Tx 55: Alpha draws 0.802, Beta draws 0.512 $\to$ 100% to Alpha (Fails)

This high-frequency chatter is **decision boundary flapping**—the mathematical consequence of unbuffered argmax selection on overlapping densities.

---

## 3. Decisions Made and Rejected Alternatives

1. **Deliberate 100% Hard-Switching vs. Heuristic Smoothing**:
   - *Alternative*: Smooth traffic using a softmax function or rolling average.
   - *Why Rejected*: Interim smoothing masks bandit dynamics and preempts Phase 4's PID controller. Hard-switching is an intentional baseline feature.
2. **Single-Shot Routing vs. Cascading Retries**:
   - *Alternative*: Immediately retry failed payments on a backup gateway.
   - *Why Rejected*: Retries mask failures from the learning loop, double latency, and risk duplicate card charges during network timeouts.
3. **Dual-Mode Engine Architecture**:
   - *Alternative*: Build only an HTTP service or only an in-process library.
   - *Why Chosen*: In-process `BanditRouter` enables fast unit tests (>5,000 TPS), while the FastAPI wrapper (`router_core/app.py`) provides a realistic network daemon for multi-process testing.

---

## 4. The Interface Contract

The pipeline contract uses three Pydantic models:
- **`AcquirerRouteConfig`**: Binds an acquirer ID to its URL, timeout budget (2.0s), and Phase 1 state settings.
- **`RouterConfig`**: Configures route lists, connection pool limits (`max_connections=100`), and PRNG seeds.
- **`RoutingResult`**: Captures transaction ID, selected acquirer, Thompson samples, authorization status, latencies, and an updated state snapshot.

Outcomes map cleanly: HTTP 200 with `authorized: true` records success ($x = 1.0$); HTTP 200 declines, HTTP 503, and timeouts record failure ($x = 0.0$); HTTP 422 schema errors raise exceptions without penalizing acquirer health.

---

## 5. Logic-Level Implementation Walkthrough

In [`router_core/router.py`](file:///d:/loom/router_core/router.py), `BanditRouter.route()` executes sequentially:
1. **Sampling**: `samples = self.state_registry.sample_all()` draws independent floats in $[0, 1]$ across arms.
2. **Selection**: `selected = self._argmax_route(samples)` evaluates $\arg\max$, breaking ties deterministically by sorting route IDs alphabetically.
3. **Network Call**: `await self._client.post(...)` dispatches the payload via a persistent `httpx.AsyncClient` pool.
4. **Outcome Classification**: Gateway timeouts and HTTP 503 errors are intercepted and classified as `"ERROR"`.
5. **Feedback Mutation**: `self.state_registry.record_outcome(selected, success=...)` synchronously triggers offset decay and EWMA updates in the same turn.

---

## 6. QA Findings: The Baseline and Discovered Pathologies

QA ran a 150-transaction scripted scenario (Alpha 95% vs Beta 94%, outage on Alpha at Tx 50, recovery at Tx 100):

### The Quantified 'Before' Baseline
- **Flip Frequency**: 13 route flips across 50 outage transactions.
- **Flapping Streak**: 4 consecutive back-to-back flips (Tx 53–57: A $\to$ B $\to$ A $\to$ B $\to$ A).
- **Hairline Decisions**: At Tx 62, Alpha won by $\Delta \theta = 0.0013$.
- **Discontinuity**: Instantaneous 100% binary jumps without transition damping.

### Discovered Pathology: Post-Outage Route Starvation
When Alpha recovered at Tx 100 (`base_rate = 0.95`), it received **0 out of 50 subsequent transactions**.
*Why?* Phase 1 uses event-driven decay: unselected routes never step decay. Alpha remained frozen at a depressed mean ($\mu = 0.617$), while Beta accumulated tight successes ($\mu = 0.874, \sigma \approx 0.05$). Without an **exploration floor**, a recovered route remains starved indefinitely.

---

## 7. Dependencies and Contract with Phase 4

- **What Came Before**: Phase 3 consumes Phase 1's `BanditStateRegistry` for state math and Phase 2's `POST /authorize` API for network execution.
- **What It Owes Phase 4**: Phase 3 delivers the oscillating baseline. Phase 4's PID controller must:
  1. Dampen crossover flips from 13 down to $\le 2$.
  2. Enforce an **exploration floor** ($w_{\text{min}} \ge 3\%$) to resolve route starvation.
  3. Decouple PID error from raw Thompson sample noise to prevent **derivative kick**.

---

## 8. Defensible Scope Exclusions

We deliberately excluded three items:
1. **No Fractional Splitting**: Transactions are indivisible; fractional traffic allocation belongs to Phase 4 PID duty cycling.
2. **No Redis or SQLite Persistence**: In-memory registries allow rapid testing; distributed storage belongs in Phase 5.
3. **No Continuous Time-Decay**: Event-driven decay guarantees exact mathematical determinism in unit tests.

---

## Glossary

- **Actuator**: Mechanism executing a control action.
- **Argmax**: Function returning the argument yielding the highest value.
- **Bandit Router**: Router selecting destinations using multi-armed bandit distributions.
- **Beta Distribution**: Continuous probability distribution on $[0, 1]$, parameterized by $\alpha$ and $\beta$.
- **Closed-Loop System**: Control system using real-time feedback to govern decisions.
- **Decision Boundary Flapping**: Rapid switching between choices caused by sample noise near equal scores.
- **Derivative Kick**: Control spike in a PID controller from differentiating noisy inputs.
- **Exploration Floor**: Minimum traffic percentage guaranteed to non-lead routes to probe health.
- **Hard-Switching**: Instantly shifting 100% of volume between routes without a transition ramp.
- **Indivisibility**: Requirement that a single payment cannot be split across multiple processors.
- **Non-Stationary Environment**: System where underlying probabilities change over time.
- **Route Starvation**: When a recovered route receives zero traffic because its frozen state prevents selection.
- **Thompson Sampling**: Algorithm selecting actions probabilistically based on their likelihood of being optimal.
