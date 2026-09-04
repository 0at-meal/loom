# Phase 3 First-Principles Explainer: Bandit-Only Routing Pipeline & Hard-Switching Dynamics

In Phase 1, Loom engineered the mathematical brain: a dual-belief state model uniting a mean-reverting decayed **Beta distribution** with an **EWMA** (Exponentially Weighted Moving Average) health signal. In Phase 2, Loom built the physical world: a standalone, scriptable **simulated acquirer service** exposing mock payment gateways over HTTP.

Phase 3 wires the brain to the world. It constructs the closed-loop pipeline connecting a synthetic **transaction generator**, the **bandit routing engine**, and the **acquirer gateways**.

Crucially, Phase 3 implements this routing pipeline **without PID smoothing**. Routing decisions operate via raw **Thompson Sampling argmax selection**, directing 100% of each transaction to whichever acquirer draws the highest instantaneous sample. This document derives the mathematical mechanics of this pipeline, analyzes the inevitable instability (allocation oscillation and herd migration) that results, and explains why this hard-switching behavior is an intentional, foundational requirement of Loom's architectural roadmap.

---

## 1. What Problem This Solves (And What Breaks Without It)

Before Phase 3, Loom existed as two disconnected islands:
- Phase 1's state engine was tested only against hand-scripted synthetic arrays of outcomes in isolated memory.
- Phase 2's acquirers were tested only by sending static HTTP requests with direct client calls.

Neither component formed a closed loop. In payment routing, a closed loop requires three sequential steps:
1. **Perception & Action**: The router inspects its current beliefs, draws samples, and selects a route for an inbound payment intent.
2. **Environmental Interaction**: The router dispatches the payment across the network to an external gateway and waits for authorization.
3. **Sensory Feedback**: The gateway's response (or failure) is fed back into the mathematical state, updating the belief distribution to govern the *next* transaction.

Without Phase 3, Loom cannot learn from dynamic runtime events. But more importantly, Phase 3 establishes the **experimental control baseline** for the entire project: it makes the raw bandit's inherent instability visible, measurable, and reproducible.

---

## 2. Core Mechanism and Mathematical Derivation

### 2.1 The Indivisibility of Discrete Payment Authorizations
In control theory, an actuator often outputs a continuous control signal (e.g. a valve opening $u(t) \in [0.0, 1.0]$). However, a financial transaction is **discrete and indivisible**. A customer attempting to purchase a $100 item cannot have $73 of that payment processed by Acquirer Alpha and $27 processed by Acquirer Beta. The router must commit 100% of that specific authorization attempt to a single gateway:

$$w_t \in \{\mathbf{e}_1, \mathbf{e}_2, \dots, \mathbf{e}_K\}$$

Where $\mathbf{e}_i$ is the $i$-th standard basis vector representing an exclusive 100% commitment to route $i$.

### 2.2 Thompson Sampling Winner-Take-All Selection
At step $t$, the router holds independent Beta belief distributions for each registered acquirer $i \in \{1, \dots, K\}$:

$$\theta_{i, t} \sim \text{Beta}(\alpha_{i, t}, \beta_{i, t})$$

The router draws a pseudo-random variate $\tilde{\theta}_{i, t}$ from each distribution and selects the arm with the maximum draw:

$$A_t^* = \arg\max_{i \in \{1, \dots, K\}} \tilde{\theta}_{i, t}$$

100% of transaction $t$ is routed to $A_t^*$.

### 2.3 Macroscopic Traffic Split from Microscopic Variates
Although each individual transaction is routed exclusively to one arm, the macroscopic traffic allocation over a window of $N$ transactions reflects the probability that arm $i$'s draw exceeds all others:

$$P(A^* = i) = \int_{0}^{1} f_i(\theta; \alpha_i, \beta_i) \prod_{j \ne i} F_j(\theta; \alpha_j, \beta_j) \, d\theta$$

Where $f_i$ is the probability density function (PDF) and $F_j$ is the cumulative distribution function (CDF) of the respective Beta distributions.

#### Steady-State Dominance
Consider a two-acquirer deployment:
- **Acquirer Alpha**: $p_A = 0.95$
- **Acquirer Beta**: $p_B = 0.80$
- **Retention Factor**: $\gamma = 0.98 \implies N_{\text{eff}} = \frac{1}{1 - \gamma} = 50$

At steady state:
- $\alpha_A = 1 + 50 \times 0.95 = 48.5, \quad \beta_A = 1 + 50 \times 0.05 = 3.5 \implies \mathbb{E}[\theta_A] \approx 0.933, \ \sigma_A \approx 0.034$
- $\alpha_B = 1 + 50 \times 0.80 = 41.0, \quad \beta_B = 1 + 50 \times 0.20 = 11.0 \implies \mathbb{E}[\theta_B] \approx 0.788, \ \sigma_B \approx 0.056$

Because $\mathbb{E}[\theta_A] - \mathbb{E}[\theta_B] = 0.145$, which is more than double the pooled standard deviation ($\sqrt{\sigma_A^2 + \sigma_B^2} \approx 0.065$), the integral evaluates to:

$$P(A^* = \text{Alpha}) \approx 0.988 \ (98.8\%)$$

Under normal operations, Alpha captures almost all traffic, sending only $\approx 1.2\%$ of transactions to Beta as exploratory probes.

---

## 3. The Instability Dynamics of Raw Bandits Under Outage

What happens when Acquirer Alpha suffers an instantaneous outage ($p_A \to 0.0$)?

### 3.1 Step-by-Step Belief Collapse
Because Alpha receives 98.8% of traffic, incoming transactions continue hitting Alpha. Under Phase 1's mean-reverting offset update rule with $\gamma = 0.98$, each consecutive failure ($x=0$) attenuates $\alpha$ and inflates $\beta$:

$$\alpha_k = 1.0 + 0.98 \cdot (\alpha_{k-1} - 1.0)$$
$$\beta_k = 1.0 + 0.98 \cdot (\beta_{k-1} - 1.0) + 1.0$$

The posterior mean of Alpha plummets over consecutive failed authorizations:
- **Step 0 (Normal)**: $\alpha = 48.50, \beta = 3.50 \implies \mathbb{E}[\theta_A] = 0.9327$ ($P(A^* = \text{Alpha}) = 98.8\%$)
- **Step 2 (Failing)**: $\alpha = 46.62, \beta = 5.38 \implies \mathbb{E}[\theta_A] = 0.8965$ ($P(A^* = \text{Alpha}) = 96.8\%$)
- **Step 4 (Failing)**: $\alpha = 44.81, \beta = 7.19 \implies \mathbb{E}[\theta_A] = 0.8618$ ($P(A^* = \text{Alpha}) = 90.8\%$)
- **Step 6 (Failing)**: $\alpha = 43.08, \beta = 8.92 \implies \mathbb{E}[\theta_A] = 0.8284$ ($P(A^* = \text{Alpha}) = 76.1\%$)
- **Step 8 (Crossover)**: $\alpha = 41.41, \beta = 10.59 \implies \mathbb{E}[\theta_A] = 0.7964 \approx \mathbb{E}[\theta_B] = 0.7885$ ($P(A^* = \text{Alpha}) \approx 50.6\%$)
- **Step 10 (Overturned)**: $\alpha = 39.81, \beta = 12.19 \implies \mathbb{E}[\theta_A] = 0.7656$ ($P(A^* = \text{Alpha}) = 24.3\%$)
- **Step 15 (Abandoned)**: $\alpha = 36.08, \beta = 15.91 \implies \mathbb{E}[\theta_A] = 0.6940$ ($P(A^* = \text{Alpha}) = 1.1\%$)

### 3.2 The Two Pathologies of Raw Thompson Sampling
The trajectory above reveals the two exact failure modes of an undamped bandit router:

#### Pathology 1: Crossover Allocation Flapping (Ringing / Chatter)
Between failure steps 6 and 10, the probability density functions of Alpha and Beta heavily overlap. Because each transaction performs an independent pseudo-random draw, the winning arm alternates erratically on a transaction-by-transaction basis:
- Call 101 $\to$ Alpha (Fails!)
- Call 102 $\to$ Beta (Succeeds!)
- Call 103 $\to$ Alpha (Fails!)
- Call 104 $\to$ Beta (Succeeds!)

This is **high-frequency allocation ringing**. It creates operational chaos in payment systems: connection pools flap, downstream gateway caches thrash, and merchants experience unpredictable authorization results.

#### Pathology 2: The Herd Migration Cliff (The Step-Function Slam)
By step 15, Alpha's probability drops below 1%. Traffic makes a violent, unbuffered 100% cliff-dive directly into Beta. If Beta was provisioned for normal secondary volume, this sudden 100% surge can overwhelm Beta's ingress rate limits or thread capacity, precipitating a cascading outage.

---

## 4. Architectural Decisions and Rejected Alternatives

### 4.1 Deliberate 100% Hard-Switching vs. Interim Heuristic Smoothing
- **The Alternative**: In Phase 3, we could easily compute a rolling average of target weights, apply a softmax function over posterior means, or enforce an exponential decay on allocation switches.
- **Why It Lost**: PRD Build Order Step 3 explicitly mandates:
  *"3. Wire router (bandit only, no PID) to the simulator and a transaction generator. This is deliberately the point where you should be able to see the oscillation problem happen live — hard-switching on raw bandit output — before building the thing that fixes it."*
  If the Architect introduces ad-hoc smoothing in Phase 3, two things break:
  1. We mask the true underlying dynamics of the bandit algorithm.
  2. We preempt Phase 4's PID controller. When Phase 4 arrives, there would be no clean, unadulterated baseline against which to evaluate PID damping performance and PSR lift.
  Therefore, **100% hard-switching is a settled feature of Phase 3, not an omission to be fixed.**

### 4.2 Single-Shot Routing vs. Cascading Multi-Arm Retries
- **The Alternative**: If Acquirer Alpha declines or times out, the router could catch the error and immediately retry the payment on Acquirer Beta before returning a response to the customer.
- **Why It Lost**:
  1. **Feedback Corruption**: In a bandit control loop, an arm's performance must be evaluated strictly on its primary authorization attempt. Retrying immediately masks route degradation from upstream monitoring.
  2. **Latency Bloat**: Chaining synchronous HTTP timeouts doubles or triples customer wait times ($2\text{s} + 2\text{s} = 4\text{s}$), violating checkout conversion thresholds.
  3. **Idempotency & Double-Charge Risks**: If an acquirer hangs due to a network partition, retrying blindly on a second gateway risks submitting duplicate credit card authorizations.
  Cascading retries belong to higher-level merchant orchestration, not the core bandit selection engine.

### 4.3 Dual-Mode Architecture: In-Process Engine with FastAPI Service Wrapper
- **The Alternative**: Building solely a standalone HTTP microservice, or building solely a Python library.
- **Why Dual-Mode Won**:
  - `BanditRouter` is implemented as a clean, asynchronous Python engine. It can be invoked directly in memory by test harnesses and high-throughput benchmark scripts ($> 5,000\ \text{TPS}$, zero socket overhead).
  - Simultaneously, `router_core/app.py` packages this engine into a production-grade FastAPI daemon. This enables real multi-process network execution (`scripts/generate_transactions.py` targeting `http://localhost:8000/route`) matching the real-world payments topology.

---

## 5. End-to-End Pipeline Contract Walkthrough

The pipeline operates across four modules:

```
[scripts/generate_transactions.py]
            │  (AuthorizeRequest)
            ▼
   [router_core/router.py: BanditRouter]
      ├── 1. BanditStateRegistry.sample_all() -> {Alpha: 0.82, Beta: 0.77}
      ├── 2. ArgMax Selection -> 'acquirer_alpha' (100% commitment)
      ├── 3. httpx.AsyncClient.post("http://localhost:8001/acquirers/acquirer_alpha/authorize")
      │           │
                  ▼
         [acquirer_sim/app.py: AcquirerSimulator]
            ├── Bernoulli trial: u ~ Uniform(0, 1) < p
            └── Return AuthorizeResponse (status="AUTHORIZED", authorized=True)
                  │
      ┌───────────┘
      ├── 4. Classify Outcome: authorized=True => x=1.0
      ├── 5. BanditStateRegistry.record_outcome('acquirer_alpha', success=True)
      │        ├── alpha_new = 1.0 + 0.98*(alpha - 1.0) + 1.0
      │        ├── beta_new  = 1.0 + 0.98*(beta - 1.0) + 0.0
      │        └── health_new = 0.98*health + 0.02*1.0
      └── 6. Return RoutingResult (envelope + latencies + snapshot)
            │
            ▼
[Transaction Generator: Logs Telemetry & Rolling PSR]
```

### Outcome Mapping Guardrails
The router strictly parses gateway responses according to five categories:
1. **HTTP 200 & `authorized: true`**: Success ($x = 1.0$).
2. **HTTP 200 & `authorized: false`**: Business Decline ($x = 0.0$).
3. **HTTP 503 Service Unavailable**: Gateway Outage ($x = 0.0$).
4. **Transport Timeout / Connection Error**: Network Failure ($x = 0.0$).
5. **HTTP 422 Unprocessable Entity**: Client bug / malformed payload. **Do NOT penalize acquirer health.** Raise an error or alert immediately, because the gateway correctly rejected an invalid request from Loom.

---

## 6. QA Verification Criteria for Backend Handoff

The QA engineer and Backend engineer must verify Phase 3 against these explicit test scenarios:

1. **Deterministic ArgMax Enforcement**:
   - When seeded PRNG produces samples $\theta_A = 0.85, \theta_B = 0.70, \theta_C = 0.65$, route selection must pick Acquirer A with 100% certainty.
2. **Steady-State Volume Distribution**:
   - Emitting 1,000 transactions against healthy Alpha ($p=0.95$) and healthy Beta ($p=0.80$) must allocate $\ge 95\%$ of all volume to Alpha.
3. **Outage Crossover & Crossover Chatter Verification**:
   - Triggering an outage on Alpha via `POST /acquirers/acquirer_alpha/admin/outage` must cause Alpha's posterior mean to fall below Beta's within $7 \pm 2$ failed transactions.
   - During transactions 5 through 10 following outage initiation, test assertions must capture alternating route selections (flapping).
   - By transaction 15, traffic must be 100% hard-switched to Beta.
4. **Transport Exception Resilience**:
   - Mocking an `httpx.TimeoutException` on Alpha must record a failure ($x=0$) on Alpha without crashing the router event loop.

---

## 7. Dependencies and Hand-off

- **What Came Before**:
  - Implements state feedback directly against Phase 1's [`router_core/bandit.py`](file:///d:/loom/router_core/bandit.py) and [`router_core/state.py`](file:///d:/loom/router_core/state.py).
  - Consumes Phase 2's [`acquirer_sim/app.py`](file:///d:/loom/acquirer_sim/app.py) via authoritative URL-path routing (`POST /acquirers/{id}/authorize`).
- **What It Owes Phase 4 (PID Layer)**:
  - Phase 3 delivers the unbuffered, oscillating allocation signal. Phase 4 will introduce the PID control loop that computes the error between current allocation and bandit target, smoothing the transition curve and eliminating the crossover chatter.
- **What It Owes Phase 5 (Data Layer)**:
  - Phase 3 produces the `RoutingResult` schema, which contains the exact timestamp, selected route, samples, latencies, and outcome data that Phase 5 will persist to SQLite and publish over Redis.

---

## Glossary

- **Actuator**: In control theory, the physical or logical mechanism that executes a control decision.
- **Argmax**: Mathematical operator finding the argument that maximizes a given function or vector.
- **Bandit Router**: Payment routing component selecting gateways via multi-armed bandit exploration.
- **Crossover Window**: Range of transactions where degrading primary and healthy secondary belief distributions overlap.
- **Flapping (Chatter)**: Rapid, high-frequency oscillation between two alternate choices caused by noise.
- **Hard-Switching**: Instantaneous 100% reassignment of traffic from one route to another without gradual transition.
- **Herd Migration**: Sudden panic shift of all client traffic from a failing route onto a single backup route.
- **Indivisibility**: Characteristic of payment transactions where each discrete authorization must commit to one gateway.
- **In-Process Mode**: Executing components within a single Python memory process to avoid network overhead.
- **Non-Stationary Environment**: System where true underlying probabilities change over time (e.g. during an outage).
- **Outage Cliff**: The sharp step-function drop in traffic allocation experienced under raw bandit routing.
- **Pooled Connection**: Persistent HTTP socket kept open across multiple requests to eliminate TCP handshake latency.
- **Thompson Sampling**: Probabilistic heuristic selecting actions proportionally to their probability of being optimal.
- **Winner-Take-All**: Selection policy where the highest-scoring candidate receives 100% of the allocated resource.
