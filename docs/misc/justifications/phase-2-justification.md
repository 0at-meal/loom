# Phase 2 First-Principles Explainer: Simulated Acquirer Service

When an online customer clicks "Pay," their transaction routes to an **acquirer** — a financial institution connecting merchants to card networks (Visa, Mastercard) to authorize charges. In Phase 1, Loom built a mathematical brain: a dual belief engine combining a decaying **Beta distribution** with an **EWMA** (Exponentially Weighted Moving Average) health score. But a brain cannot learn without sensory feedback. Phase 2 delivers that sensory environment: a **simulated acquirer service** (`acquirer_sim`). This service exposes mock payment gateways over **HTTP** (Hypertext Transfer Protocol), creating a scriptable testbed where success rates and outages are manipulated deterministically.

---

## 1. What Problem This Solves (And What Breaks Without It)

To verify that Loom dynamically diverts traffic during a payment outage, we must test against real failure modes. Two shortcuts fail:

1. **Real Gateways or Sandboxes**: Sandbox environments (e.g., Stripe test mode) are non-deterministic. We cannot command a third-party sandbox to fail at 12:00:00 and recover at 12:02:00, causing flaky, unrepeatable tests.
2. **Static Logs**: Replaying past transaction records cannot evaluate a closed-loop controller. If Loom diverts volume from Route A to Route B, a static log cannot reveal how Route B would have responded to that sudden surge.

The simulator provides a controllable **ground truth** — an environment with known baseline success rates and instantaneous, deterministic failure injection.

---

## 2. Core Mechanism and Mathematical Derivation

Payment authorization is modeled as a **Bernoulli trial** — an experiment with binary outcomes: success ($X = 1$, authorized) or failure ($X = 0$, declined).

### Bernoulli Sampling from Uniform Random Variates
Each acquirer has an intrinsic **payment success rate (PSR)** $p \in [0.0, 1.0]$. The probability mass function is:

$$P(X = x) = p^x (1 - p)^{1 - x}, \quad x \in \{0, 1\}$$

We draw a pseudo-random number $u$ from a continuous **uniform distribution**:

$$u \sim \text{Uniform}(0, 1)$$

We evaluate the trial via an **indicator function**:

$$X = \begin{cases} 1 & \text{if } u < p \implies \text{AUTHORIZED} \\ 0 & \text{if } u \ge p \implies \text{DECLINED} \end{cases}$$

Because $u$ is uniform over $[0, 1]$, the cumulative distribution gives:

$$P(u < p) = \int_{0}^{p} 1 \, du = p$$

This transforms a continuous pseudo-random draw into an unbiased binary transaction outcome.

### Convergence Under the Central Limit Theorem
When dispatching $N$ independent requests to an acquirer with rate $p$, total approved transactions $K = \sum_{i=1}^N X_i$ follows a **Binomial distribution**:

$$K \sim \text{Binomial}(N, p)$$

The observed sample rate is $\hat{p} = \frac{K}{N}$. By the **Central Limit Theorem**, $\hat{p}$ converges to a normal distribution with mean $\mu = p$ and standard deviation:

$$\sigma = \sqrt{\frac{p(1 - p)}{N}}$$

For our QA benchmark ($N = 2,500$ at $p = 0.80$), $\sigma = \sqrt{\frac{0.80 \times 0.20}{2500}} = 0.008$ ($0.8\%$). The **three-sigma ($3\sigma$) confidence interval** spans:

$$[p - 3\sigma, \; p + 3\sigma] = [0.80 - 0.024, \; 0.80 + 0.024] = [0.776, \; 0.824]$$

Empirical success inside this window proves the simulator functions with $99.73\%$ statistical certainty.

### Outage Dynamics: The Step Function
During an outage, effective rate $p_{\text{eff}}(t)$ shifts via a discrete **Heaviside step function**:

$$p_{\text{eff}}(t) = p \cdot (1 - \mathbb{I}_{\text{outage}}(t))$$

Where $\mathbb{I}_{\text{outage}}(t) = 1$ when active, and $0$ otherwise, driving $p_{\text{eff}} = 0.0$ and immediately declining all subsequent transactions.

---

## 3. Architectural Decisions and Rejected Alternatives

1. **Hard Step Function vs. Gradual Degrade Curve**:
   - *Alternative*: Sigmoid decay ramping down over 30 seconds.
   - *Why It Lost*: Background tickers add non-determinism. Crucially, an instantaneous cliff is the harshest test for Loom's control loop. Smoothing a step function visually proves the router works. External scripts can orchestrate gradual ramps by stepping rates in a loop.
2. **HTTP 200 Structured Declines vs. HTTP 503 Server Errors**:
   - *Alternative*: Returning `HTTP 503` exclusively during outages.
   - *Why It Lost*: Real banking declines return `HTTP 200 OK` with structured decline codes; 5xx errors denote network failures. Returning `HTTP 200` with `status: "DECLINED"` mirrors reality and prevents transport crashes in client loops. An optional `HTTP_503` mode remains available for network failure tests.
3. **Application Factory with Keyed Routes vs. Monolithic Multi-Process**:
   - *Alternative*: Running separate FastAPI processes on different physical ports.
   - *Why It Lost*: Multi-port setups risk port collisions during tests. The `create_app` factory supports multi-process deployment while enabling fast in-memory testing keyed by path (`/acquirers/{id}/authorize`).

---

## 4. The Interface Contract

The contract separates transaction execution from administrative control:

### Authorization Plane
- **`POST /acquirers/{acquirer_id}/authorize`**:
  - *Request*: `transaction_id` (str), `amount` (float $> 0$), `currency` (ISO code), `merchant_id`.
  - *Response*: `transaction_id`, `acquirer_id`, `status` ("AUTHORIZED" / "DECLINED"), `authorized` (bool), `authorization_code` (str or null), `decline_code` ("DO_NOT_HONOR" / "ACQUIRER_OUTAGE"), `simulated_latency_ms`.

### Admin Control Plane
- **`POST /acquirers/{acquirer_id}/admin/success-rate`**: Updates baseline PSR $p \in [0.0, 1.0]$.
- **`POST /acquirers/{acquirer_id}/admin/outage`**: Toggles outage state (`active: bool`, `behavior: OutageBehavior`).
- **`GET /acquirers/{acquirer_id}/admin/state`**: Inspects telemetry, request counts, and empirical PSR.
- **`POST /acquirers/{acquirer_id}/admin/reset`**: Resets counters between runs.

---

## 5. Implementation Logic Walkthrough

The engine spans three core files:

1. **`models.py`**: Declares **Pydantic** validation schemas enforcing strict constraints (`gt=0.0` for amounts, regex `^[A-Z]{3}$` for currencies, `extra="forbid"` to reject malformed inputs).
2. **`simulator.py` (`AcquirerSimulator`)**:
   - Tracks state: `_base_success_rate`, `_outage_active`, and counter integers.
   - Mutations and increments are protected by a **mutex** (`threading.Lock`), ensuring thread-safety under concurrent async requests.
   - Simulates non-blocking latency via `await asyncio.sleep(delay_ms / 1000.0)`. Tests default latency to 0ms for maximum throughput.
   - `execute_authorization` pipeline: If `_outage_active` is true, increments `_outage_declines` and declines with `decline_code="ACQUIRER_OUTAGE"`. Otherwise, samples $u \sim \text{Uniform}(0, 1)$; if $u < p$, increments `_authorized_count` with an auth code; otherwise increments `_declined_count` with `decline_code="DO_NOT_HONOR"`.
3. **`app.py`**: Configures **FastAPI**, routing, dependency injection, and maps domain exceptions (`AcquirerOutageHttpException`) to clean responses.

---

## 6. QA Verification and Edge Cases

The QA audit executed 95 automated tests across unit, integration, and statistical suites:

1. **Statistical Verification**: Across $p \in \{0.25, 0.50, 0.70, 0.90\}$, observed rates matched configured targets within $3\sigma$ confidence ($N = 2,500$).
2. **Zero-Lag Mid-Run Outage**: In a 100-request stream, triggering an outage after call 50 caused call 51 to decline immediately with `ACQUIRER_OUTAGE`. Clearing the outage restored call 101 to `AUTHORIZED` with zero delay.
3. **Boundary Extremes**: At $p = 0.0$, 100% declined. At $p = 1.0$, 100% authorized. Toggling outages ON and immediately OFF produced zero lockup or race conditions.
4. **Contract Ambiguity Resolved**: QA revealed callers could pass an `acquirer_id` in the body clashing with the URL path. We established that **the URL path is authoritative**, matching production gateway routing.

---

## 7. Dependencies and Hand-off

- **What Came Before**: Implemented to meet [`docs/CONSTITUTION.md`](file:///d:/loom/docs/CONSTITUTION.md) architecture (FastAPI, uvicorn, Pydantic).
- **What It Owes Phase 3**: Phase 3 will wire the Thompson Sampling router to this service. Phase 2 provides the exact callable endpoint (`POST /acquirers/{id}/authorize`).

---

## 8. Deliberate Scope Simplifications

We made three defensible scope omissions:

1. **No Idempotency Cache**: Real gateways deduplicate transaction IDs. In Phase 2, each call is an independent Bernoulli trial. Adding an idempotency database introduces distributed state prematurely; Phase 3 generates unique UUIDs.
2. **No Native Sigmoid Degradation**: Kept the server a pure step function primitive.
3. **In-Memory Telemetry**: Counters live in memory; persisting metrics to Redis or SQLite is deferred to Phase 5.

---

## Glossary

- **Acquirer**: Bank processing card payments for merchants.
- **Application Factory**: Pattern creating configured application instances on demand.
- **AsyncIO**: Python library for concurrent asynchronous I/O.
- **Bernoulli Trial**: Random experiment with binary outcomes: success or failure.
- **Binomial Distribution**: Probability distribution of successes in independent Bernoulli trials.
- **Central Limit Theorem**: Theorem stating normalized sums of independent variables converge to normal distributions.
- **Confidence Interval ($3\sigma$)**: Range enclosing $99.73\%$ probability mass under normality.
- **FastAPI**: Modern Python web framework for building APIs.
- **Ground Truth**: Controlled environment where parameters are known with certainty.
- **Heaviside Step Function**: Function modeling instantaneous zero-to-one transitions.
- **HTTP**: Hypertext Transfer Protocol for web communication.
- **Idempotency**: Property where repeating an operation produces identical results.
- **Indicator Function**: Function returning 1 when a condition holds, 0 otherwise.
- **Mutex**: Synchronization lock preventing concurrent access to shared resources.
- **Payment Success Rate (PSR)**: Percentage of authorization attempts successfully approved.
- **Pydantic**: Python data validation library using type annotations.
- **Uniform Distribution**: Continuous distribution where all intervals of equal length have equal probability.
- **Uvicorn**: High-performance ASGI web server for Python.
