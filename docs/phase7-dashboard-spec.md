# Phase 7 Specification — The Mission-Control Live Dashboard

**Role**: Systems Architect
**Audience**: Frontend Engineer, Backend Engineer, DevOps Engineer, QA Engineer, Tech Lead
**Scope**: Phase 7 (React Frontend, Native WebSocket Streaming Bridge, Live Allocation Chart, PSR/Health Readouts, Outage Injection Controls)
**Status**: Settled Architecture Contract

---

## 1. Executive Summary & System Topology

Throughout Phases 1 through 6, Loom developed and empirically verified its dynamic, closed-loop payment routing engine:
1. **Perception**: Decayed Thompson Sampling belief updates ($\alpha, \beta$) and operational EWMA health tracking ($H$) (`router_core/state.py`, `router_core/bandit.py`).
2. **Ground Truth**: Independent simulated payment gateways with scriptable availability, latency jitter, and HTTP decline codes (`acquirer_sim`).
3. **Actuation**: Proportional-Integral-Derivative (PID) smoothing engine with bounded simplex projection ($w_{\text{min}} \ge 3\%$) eliminating herd migration and route starvation (`router_core/pid.py`).
4. **Data Layer**: Redis persistent health state, high-throughput Redis Pub/Sub event streaming (`events:routing`, `events:health`), and engine-enforced append-only SQLite metrics logging (`data_layer/`).
5. **Comparative Baseline**: Static priority router reproducing industry circuit breakers ($M=3, N=30$), proving an 8.5x stability improvement ($\Delta w_{\text{max}} = 11.77\%$ vs $100.0\%$) and demonstrating +1000 bps PSR lift during sensitive overreaction scenarios (`baseline_router/`, `docs/phase6-qa-report.md`).

Until now, Loom's dynamic traffic steering has been observable only via terminal logs, SQLite queries, and test assertions.

**Phase 7 delivers Loom's crowning visual artifact: The Mission-Control Live Dashboard (`dashboard/`)**.
The dashboard provides a real-time cockpit for payment operators, executives, and demonstration audiences. It makes the abstract mathematics of closed-loop control visible, tangible, and undeniable.

```
+-----------------------------------------------------------------------------------------------------------------------------+
|                                                  LOOM LIVE MISSION CONTROL                                                  |
|                                                                                                                             |
|   +---------------------------------------+  +---------------------------------------+  +-------------------------------+   |
|   | TICKET A: LIVE ALLOCATION CHART       |  | TICKET B: METRIC READOUTS             |  | TICKET C: OPERATOR CONTROLS   |   |
|   | - 60s Rolling Multi-Acquirer Split    |  | - Rolling & Lifetime PSR Readout      |  | - Per-Acquirer Outage Toggles |   |
|   | - Smooth PID Curve (Amber Line)       |  | - Phase 6 Baseline Comparison Card    |  | - Decline / 503 / Hang Modes  |   |
|   | - Pinned Rust Outage Marker           |  | - Stability Multiplier (8.5x Lift)    |  | - Gray Failure Slider (p=0.6) |   |
|   | - Single-Step Delta Metric (dw_max)   |  | - Per-Acquirer EWMA Health & Beliefs  |  | - Scenario Gauntlet Presets   |   |
|   +---------------------------------------+  +---------------------------------------+  +-------------------------------+   |
|                                                               ^                                         |                   |
|                                                               | React State Updates                     | HTTP POST /admin  |
+---------------------------------------------------------------|-----------------------------------------|-------------------+
                                                                |                                         |
                                          +---------------------+---------------------+                   |
                                          | TICKET D: WEBSOCKET STREAMING GATEWAY     |                   |
                                          | - Native Browser WebSocket (RFC 6455)     |                   |
                                          | - Circular Ring Buffer (200 events)       |                   |
                                          | - Decoupled requestAnimationFrame (60 FPS)|                   |
                                          | - Hybrid Bootstrap (SQLite + Redis Stream)|                   |
                                          +---------------------+---------------------+                   |
                                                                ^                                         |
                                                                | WS Frames (JSON)                        |
                                            +-------------------+-------------------+                     |
                                            | FastAPI WebSocket Bridge (/ws/stream) |                     |
                                            | (router_core/app.py)                  |                     |
                                            +---------+-------------------+---------+                     |
                                                      ^                   ^                               |
                                                      | Sub               | Sub                           |
                                                      |                   |                               v
+-----------------------------------------------------+--+             +--+-------------------------------+-------------------+
| REDIS PUB/SUB (Phase 5)                                |             | SIMULATED ACQUIRERS (Phase 2)    | SQLITE DB         |
| - Channel: 'events:routing' (RoutingEvent)             |             | - Acquirer Alpha (Port 8001)     | loom_metrics.db   |
| - Channel: 'events:health'  (HealthAlertEvent)         |             | - Acquirer Beta  (Port 8001)     | (transactions,    |
| - State: 'acquirer:{id}:health', 'acquirer:{id}:beta'  |             | - Acquirer Gamma (Port 8001)     |  acquirer_outcomes|
+--------------------------------------------------------+             +----------------------------------+-------------------+
```

### Explicit Scope Boundaries

- **In Scope for Phase 7**:
  - React application bootstrapped with Vite under `dashboard/`.
  - Strict enforcement of the mission-control visual direction (navy ground, panel gray, hairline borders, amber telemetry, rust danger states, monospace numbers, clean sans typography).
  - Ticket A: Live multi-line traffic allocation chart ($w_i \in [0.0, 1.0]$) rendering smooth PID easing curves with dynamic, pinned outage-event markers in rust.
  - Ticket B: Telemetry readouts for rolling PSR, per-acquirer EWMA health score ($H$), Beta posterior parameters ($\alpha, \beta, \mathbb{E}[\theta]$), transaction latencies, and Phase 6 baseline comparison cards.
  - Ticket C: Interactive operator controls per acquirer supporting on-demand step-outages, behavior selection (`RETURN_DECLINE`, `HTTP_503`, `LATENCY_SPIKE`), partial gray-failure sliders ($p \in [0.0, 1.0]$), and one-click benchmark scenario gauntlets.
  - Ticket D: FastAPI WebSocket streaming endpoint (`/ws/telemetry`), consuming Redis Pub/Sub via `AsyncEventSubscriber`, with client-side automatic reconnection, ring buffer throttling, and hybrid cold-start bootstrapping from SQLite.
  - Comprehensive unit and integration test suite (`tests/dashboard/` or component tests).

- **Explicitly Out of Scope for Phase 7**:
  - Modifying the core router math or PID algorithms (settled in Phase 1 & Phase 4).
  - Modifying Phase 5 Redis or SQLite schemas (settled in Phase 5).
  - Real payment gateways, merchant credentials, or live banking APIs (strictly prohibited by `docs/CONSTITUTION.md`).
  - Complex authentication, multi-tenant RBAC, or user management (unnecessary for local demo harness).
  - Value-scaled exploration (deferred to Phase 8).

---

## 2. Pinned Mission-Control Visual Direction Contract

Per the architectural mandate, the visual direction is not a cosmetic afterthought left to individual frontend developer whim. It is a **hard, locked contract** that governs the design tokens, layout geometry, color semantics, and typography of every component across Tickets A, B, C, and D.

### 2.1 Design Philosophy: The Mission-Control Cockpit
Loom is an enterprise control system for high-velocity payment infrastructure. The interface must evoke the calm, information-dense authority of an aerospace telemetry console, a power grid control room, or a high-frequency trading terminal.

- **Zero Consumer-App Clutter**: No bubbly pastel buttons, no cartoon illustrations, no floating card kit drop-shadows, no large decorative gradients.
- **High Data Density**: Minimal padding, compact grid gutters ($8\text{px} - 12\text{px}$), tabular numerical alignment, and high signal-to-noise ratio.
- **Semantic Color Discipline**: Color is an alert signal, not decoration. Every hue carries a strict, inviolable meaning.

### 2.2 Locked Color Palette

| Token Identifier | Hex Code | Purpose & Semantic Invariant |
| :--- | :--- | :--- |
| `--color-ground` | `#0B1120` | **Deep Space Navy**: Canvas ground background. All panels sit on this ground. |
| `--color-panel` | `#111827` | **Cockpit Gray-Navy**: Surface background for all telemetry panels, gauges, and decks. |
| `--color-panel-inset` | `#070C18` | **Deep Inset Well**: Sunken backgrounds for charts, dark data wells, and metric inputs. |
| `--color-border-hairline`| `#1E3A5F` | **Hairline Border**: Strictly $1\text{px}$ solid border for all panels, dividers, and chart grids. |
| `--color-border-subtle` | `#172554` | **Subtle Divider**: Ultra-low contrast internal divider lines within tables. |
| `--color-telemetry-amber`| `#D9A441` | **Telemetry Amber**: **STRICTLY RESERVED** for live telemetry numbers, active gauges, and the healthy-state chart line. |
| `--color-danger-rust` | `#C1622D` | **Alert Rust**: **STRICTLY RESERVED** for outage indicators, tripped routes, error readouts, and danger markers. |
| `--color-route-secondary`| `#38BDF8` | **Steel Cyan**: Dedicated color for Secondary Route (Acquirer Beta) healthy line/indicator. |
| `--color-route-tertiary` | `#94A3B8` | **Muted Slate**: Dedicated color for Tertiary Route (Acquirer Gamma) healthy line/indicator. |
| `--color-text-primary` | `#F8FAFC` | **High-Contrast White**: Panel titles, section headers, primary labels. |
| `--color-text-muted` | `#64748B` | **Muted Steel**: Units, secondary metadata, table column headers, timestamps. |

> [!CAUTION]
> **Strict Color Invariant**:
> - **Amber (`#D9A441`)** is exclusively reserved for live telemetry numbers, active healthy operational metrics, and the Primary Acquirer (Alpha) healthy chart line. It must never be used for static decorative borders, random buttons, or generic text.
> - **Rust (`#C1622D`)** is exclusively reserved for outage, danger, degradation, and trip states. It must never be used for normal buttons, secondary route lines, or neutral elements.
> - Under no circumstances should green (`#00FF00` or `#10B981`) or purple (`#8B5CF6`) be introduced into the primary telemetry scheme.

### 2.3 Typography Contract

1. **Monospace Font Stack (Numeric & Telemetry Readouts Only)**:
   - Font Family: `'JetBrains Mono', 'Fira Code', 'SF Mono', Consolas, monospace`.
   - Applied Exclusively To:
     - Real-time rolling PSR percentages (e.g. `89.33%`).
     - Acquirer health scores (e.g. `0.942`).
     - Latency numbers (e.g. `0.048 ms`).
     - Allocation weights (e.g. `0.820`).
     - Beta belief parameters ($\alpha, \beta$).
     - Transaction IDs, sequence numbers, and epoch timestamps.
   - CSS Features: `font-feature-settings: "tnum" 1, "zero" 1;` (tabular figures and slashed zero). Tabular figures are mandatory to prevent numerical jitter and layout shift as values stream in at 50 TPS.

2. **Clean Sans Font Stack (Everything Else)**:
   - Font Family: `'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif`.
   - Applied Exclusively To:
     - Application header and mission-control deck titles.
     - Card titles and section headers.
     - Table column titles, route labels, and parameter descriptors.
     - Button text, toggle switches, and form labels.
     - Outage alert descriptions and tooltip text.

### 2.4 Geometry, Borders & Elevation (Zero Rounded-Card Shadows)

- **Border Radius**: Strictly flat or micro-radius:
  `border-radius: 0px` (preferred cockpit aesthetic) or at most `border-radius: 2px` (`rounded-sm`).
- **Shadows**: **STRICTLY FORBIDDEN**.
  `box-shadow: none !important;`
  No diffuse dropshadows, no blurred glowing halos, and no Tailwind `shadow-lg` or `shadow-2xl`.
- **Hairline Borders**:
  All panels, cards, and input fields are demarcated by a razor-sharp `1px solid #1E3A5F`.
- **Grid Density**:
  Dense 12-column grid layout with uniform $8\text{px}$ or $12\text{px}$ gap.

---

## 3. Ticket A Specification: Live Allocation Chart with Outage-Event Marker

### 3.1 Objective & Scope
Render a high-frequency, real-time time-series chart showing the dynamic traffic allocation percentage ($w_i(t) \in [0.0, 1.0]$) for each candidate acquirer (`acquirer_alpha`, `acquirer_beta`, `acquirer_gamma`). The chart serves as the **unquestionable visual proof** that Loom's PID controller smooths traffic shedding into a continuous easing curve, completely eliminating the discontinuous $100\%$ cliff jumps and herd migration stampedes of static routing.

### 3.2 Visual & Rendering Requirements (`AllocationChart.jsx`)

```
+---------------------------------------------------------------------------------------------------------+
| TRAFFIC ALLOCATION OVER TIME (SMOOTHED PID vs STATIC CLIFF)                     Peak Jump (dw): 11.77%  |
+---------------------------------------------------------------------------------------------------------+
| 100% |-----------                                                                                       |
|      |           \                                          OUTAGE INJECTED: ALPHA                      |
|  80% |   Alpha    \                                        | (Tx #51 @ 14:22:01)                        |
|      |   (Amber)   \                                       |                                            |
|  60% |              \                                      v                                            |
|      |               \                      Beta (Steel Cyan)                                           |
|  40% |                \                  .---------------------------------------                       |
|      |                 \               .'                                                               |
|  20% |                  \             /                                                                 |
|      |                   '-----------'                                                                  |
|   0% +--------------------------------------------------------------------------------------------------+
|      T-60s                      T-40s                      T-20s                                   Now  |
+---------------------------------------------------------------------------------------------------------+
```

1. **Canvas / Chart Canvas**:
   - Container background: Inset well `#070C18`.
   - Surrounding card border: Hairline `1px solid #1E3A5F`.
   - Gridlines: Horizontal gridlines at 0%, 20%, 40%, 60%, 80%, 100% rendered in `#1E3A5F` with opacity `0.4` and dashed styling (`strokeDasharray: "3 3"`).
   - Y-Axis: Fixed scale from `0%` to `100%`. Labels in clean sans `#64748B`, font size `10px`.
   - X-Axis: Rolling time window of the last $N = 60$ seconds (or last 100 transactions). Relative time markers (`-60s`, `-45s`, `-30s`, `-15s`, `0s`).

2. **Acquirer Allocation Series Lines**:
   - **Acquirer Alpha (Primary Leader)**:
     - Healthy state: Solid line in **Telemetry Amber (`#D9A441`)**, line width `2px`.
     - Outage state: Shifts to **Alert Rust (`#C1622D`)** if Alpha is undergoing an outage or health score $< 0.70$.
   - **Acquirer Beta (Secondary Backup)**:
     - Solid line in **Steel Cyan (`#38BDF8`)**, line width `2px`.
     - Ramps smoothly from the $3\%$ exploration floor up to $94\%$ during an Alpha outage.
   - **Acquirer Gamma (Tertiary Reserve)**:
     - Solid line in **Muted Slate (`#94A3B8`)**, line width `1.5px`.
     - Demonstrates the steady $3\%$ exploration floor ($w_{\text{min}} = 0.03$).

3. **PID Target Allocation Overlay (Optional Toggle)**:
   - Dotted hairline line in `#D9A441` (opacity `0.35`) showing the raw bandit target $w_i^{\text{target}}$ before PID filtering.
   - Highlights the high-frequency sampling noise that the PID engine successfully filtered out.

4. **Outage-Event Marker (Critical Demo Artifact)**:
   - When an outage is triggered (via Ticket C or detected via `HealthAlertEvent` / `decline_code == "ACQUIRER_OUTAGE"`):
     - A vertical hairline marker line is pinned to the exact transaction sequence / timestamp in **Alert Rust (`#C1622D`)**, width `1.5px`, dashed.
     - Pinned header tag at top of marker:
       `[!] OUTAGE: ACQUIRER_ALPHA (Tx #51)`
       Rendered with Rust background (`rgba(193, 98, 45, 0.2)`), Rust border (`#C1622D`), and Rust monospace text.
   - When the outage is cleared (recovery begins):
     - A vertical hairline marker line in **Telemetry Amber (`#D9A441`)** appears, tagged:
       `[*] RECOVERY INITIATED (Tx #101)`

5. **Instantaneous Smoothness Gauge ($\Delta w_{\text{max}}$)**:
   - Top-right corner readout within the chart panel:
     - Label: `PEAK STEP DELTA (dw_max)`
     - Value: `11.77%` in Amber monospace.
     - Sub-badge: `DAMPED (Target < 15.0%)` in green/amber, contrasted against `BASELINE CLIFF: 100.0%` in muted text.

### 3.3 Consumed Phase 5 Data Shape
Ticket A consumes `RoutingEvent` payloads streamed over WebSocket from Redis channel `events:routing` (conforming to `docs/schemas/routing_event.json`):

```typescript
// Data consumed per transaction for Ticket A
interface TicketARoutingInput {
  timestamp: number;              // Epoch seconds (float), e.g. 1756973000.123
  sequence_number: number;        // Monotonic sequence integer, e.g. 51
  selected_acquirer: string;      // e.g. "acquirer_alpha"
  allocation_weight: number;      // Instantaneous weight of chosen route, e.g. 0.82
  smoothed_allocation: {          // THE PRIMARY SERIES DATA SOURCE (Phase 4 PID output)
    [acquirer_id: string]: number; // e.g. {"acquirer_alpha": 0.82, "acquirer_beta": 0.15, "acquirer_gamma": 0.03}
  };
  target_allocation?: {           // Raw bandit target before PID smoothing (optional)
    [acquirer_id: string]: number;
  } | null;
  status: "AUTHORIZED" | "DECLINED" | "ERROR";
  decline_code?: string | null;   // e.g. "ACQUIRER_OUTAGE" -> triggers Outage Marker
}
```

And `HealthAlertEvent` payloads streamed from Redis channel `events:health`:

```typescript
interface TicketAAlertInput {
  timestamp: number;              // Epoch timestamp of alert
  acquirer_id: string;            // e.g. "acquirer_alpha"
  old_health: number;             // e.g. 0.95
  new_health: number;             // e.g. 0.20
  severity: "INFO" | "WARNING" | "CRITICAL"; // "CRITICAL" pins Outage Marker
  message: string;                // e.g. "Outage detected on acquirer_alpha"
}
```

---

## 4. Ticket B Specification: PSR & Health Metric Readouts

### 4.1 Objective & Scope
Render real-time numerical and categorical health telemetry for the overall routing cluster and for each individual acquirer. This ticket translates raw probability densities and EWMA scores into immediate operational visibility, highlighting the Payment Success Rate (PSR) and incorporating Phase 6's empirical benchmark results.

### 4.2 Visual & Rendering Requirements

```
+---------------------------------------------------------------------------------------------------------+
| CLUSTER PERFORMANCE TELEMETRY                                                                           |
+---------------------------------------------------------------------------------------------------------+
| [ GLOBAL PSR (LIFETIME) ]    [ ROLLING PSR (LAST 50) ]    [ STABILITY MULTIPLIER ]   [ MEAN ROUTING RTT] |
|       89.33%                        94.00%                       8.5x SMOOTHER              0.042 ms    |
|   268 AUTH / 32 FAIL            47 AUTH / 3 FAIL            dw_max: 11.8% vs 100%       p99: 0.089 ms   |
+---------------------------------------------------------------------------------------------------------+
| PHASE 6 BENCHMARK COMPARISON AUDIT (Identical 150-Tx Outage Gauntlet)                                   |
| - Standard Outage (M=3): Loom 86.00% (11.8% dw) | Baseline 92.00% (100% cliff stampede, 0 downstream cap)|
| - Sensitive Overreaction (M=1): Loom 86.00% vs Baseline 76.00% (+1000 bps PSR Lift | Cascading Collapse)|
| - Brownout Gray Failure (p=0.60): Loom Adapts Instantly vs Baseline 21-Tx Bleed & False Canary Recovery|
+---------------------------------------------------------------------------------------------------------+
| PER-ACQUIRER OPERATIONAL TELEMETRY                                                                      |
| +-----------------------------+ +-----------------------------+ +-----------------------------+         |
| | ACQUIRER ALPHA (Primary)    | | ACQUIRER BETA (Secondary)   | | ACQUIRER GAMMA (Tertiary)   |         |
| | Health (H): 0.942 [HEALTHY] | | Health (H): 0.910 [HEALTHY] | | Health (H): 0.854 [HEALTHY] |         |
| | Beta Belief: a=14.2  b=1.8  | | Beta Belief: a=8.1   b=1.2  | | Beta Belief: a=3.0   b=1.0  |         |
| | E[theta]: 88.75%  w: 82.0%  | | E[theta]: 87.10%  w: 15.0%  | | E[theta]: 75.00%  w: 3.0%   |         |
| | Latency: 21.4 ms (acquirer) | | Latency: 45.1 ms (acquirer) | | Latency: 18.2 ms (acquirer) |         |
| +-----------------------------+ +-----------------------------+ +-----------------------------+         |
+---------------------------------------------------------------------------------------------------------+
```

1. **Headline Cluster Metrics Card (`MetricReadouts.jsx`)**:
   - **Global Lifetime PSR**:
     - Monospace font in **Telemetry Amber (`#D9A441`)**, size `32px`, bold.
     - Breakdown subtext: `268 AUTH / 32 FAIL (300 Total)` in `#64748B`.
   - **Rolling Window PSR (Last 50 Transactions)**:
     - Instantaneous operational window. Amber (`#D9A441`) when $\ge 85\%$.
     - Shifts dynamically to **Alert Rust (`#C1622D`)** if rolling PSR falls below $80\%$.
   - **Stability Multiplier (Phase 6 Lift Metric)**:
     - Value: `8.5x SMOOTHER` in Amber monospace.
     - Subtext: `Peak Jump: 11.77% (PID) vs 100.0% (Cliff)`.
   - **Router Compute Latency**:
     - Average routing engine decision latency: e.g. `0.042 ms`. Confirms the bandit + PID step adds zero noticeable latency to the payment pipeline.

2. **Phase 6 Benchmark Comparison Card (`BaselineComparisonCard.jsx`)**:
   - Grounded in the empirical numbers from `docs/phase6-qa-report.md` (Table TC-QA-601 to 605):
     - Displays the three canonical test scenarios:
       1. **Overreaction ($M=1$)**: Highlights Loom's **+1000 bps PSR lift** (86.00% vs 76.00%), explaining that the static router killed 29 consecutive transactions due to cascading circuit breaker trips on routine card declines.
       2. **Standard Outage ($M=3$)**: Contextualizes the 86% vs 92% metric, explaining that the static router's score was an artifact of infinite mock capacity, whereas its 100% stampede would crash real secondary acquirers.
       3. **Gray Failure ($p=0.60$)**: Explains Loom's continuous adaptation vs the static router's 21-transaction bleed and false canary probe recovery.

3. **Per-Acquirer Operational Cards (`AcquirerHealthCard.jsx`)**:
   - One card per configured acquirer (`alpha`, `beta`, `gamma`):
   - **Status Badge**:
     - `HEALTHY`: Border `#1E3A5F`, text `#E2E8F0`, with Amber indicator dot.
     - `OUTAGE / DEGRADED`: Border `#C1622D`, background `rgba(193, 98, 45, 0.15)`, text `#C1622D`, with pulsing Rust indicator dot.
   - **EWMA Health Score ($H$)**:
     - Readout: `H: 0.942` in Amber monospace.
     - Gauge Bar: Micro-bar ($4\text{px}$ height) showing fill level from $0.00$ to $1.00$. Amber fill when $>0.70$, Rust fill when $\le 0.70$.
   - **Bayesian Posterior Beliefs**:
     - Shape parameters: $\alpha$ (decayed successes + prior), $\beta$ (decayed failures + prior).
     - Posterior Mean: $\mathbb{E}[\theta] = \frac{\alpha}{\alpha + \beta}$ rendered as percentage (e.g. `88.75%`).
   - **Current Allocation Share ($w_i$)**:
     - Displays instantaneous smoothed allocation weight: e.g. `82.0%` (Alpha), `15.0%` (Beta), `3.0%` (Gamma).
   - **Granular Latencies**:
     - Acquirer round-trip latency ($t_{\text{acquirer}}$, e.g. `21.4 ms`).

### 4.3 Consumed Phase 5 Data Shape
Ticket B consumes the `updated_state` and latency fields from `RoutingEvent`:

```typescript
// Data consumed per transaction for Ticket B
interface TicketBMetricsInput {
  status: "AUTHORIZED" | "DECLINED" | "ERROR";
  authorized: boolean;
  success: boolean;
  routing_latency_ms: number;     // Engine execution time in ms (e.g. 0.042)
  acquirer_latency_ms: number;    // External HTTP roundtrip in ms (e.g. 21.45)
  total_latency_ms: number;       // Combined end-to-end latency in ms
  updated_state: {                // Post-outcome snapshot for selected route
    alpha: number;                // e.g. 14.23
    beta: number;                 // e.g. 1.82
    expected_success_rate: number;// e.g. 0.887
    health_score: number;         // e.g. 0.942
  };
}
```

And the historical aggregate metrics bootstrapped from SQLite via Phase 5's `SQLiteMetricsStore.get_psr_metrics()`:

```typescript
interface TicketBHistoricalPSR {
  total_count: number;
  authorized_count: number;
  declined_count: number;
  error_count: number;
  psr: number;                    // authorized_count / total_count
  avg_total_latency_ms: number;
}
```

---

## 5. Ticket C Specification: Outage-Trigger Controls Per Acquirer

### 5.1 Objective & Scope
Provide a live, interactive operator deck that allows the user or demo presenter to inject synthetic failures, step outages, and partial brownouts into specific simulated acquirers in real-time, and watch Loom's control loop react autonomously.

### 5.2 Visual & Rendering Requirements (`OperatorControls.jsx`)

```
+---------------------------------------------------------------------------------------------------------+
| SIMULATION HARNESS OPERATOR DECK (ON-DEMAND OUTAGE INJECTION)                                           |
+---------------------------------------------------------------------------------------------------------+
| [ PRESET 1: STANDARD CLIFF ]    [ PRESET 2: SENSITIVE BLIP ]    [ PRESET 3: GRAY FAILURE ]   [ RESET ]  |
+---------------------------------------------------------------------------------------------------------+
| ACQUIRER ALPHA (Primary)        | ACQUIRER BETA (Secondary)     | ACQUIRER GAMMA (Tertiary)             |
| Status: OPERATIONAL             | Status: OPERATIONAL           | Status: OPERATIONAL                   |
| Base PSR: [ 95% ]               | Base PSR: [ 90% ]             | Base PSR: [ 85% ]                     |
|                                 |                               |                                       |
| [ TRIGGER OUTAGE ]              | [ TRIGGER OUTAGE ]            | [ TRIGGER OUTAGE ]                    |
| Mode: (o) DECLINE ( ) 503 ( )HANG| Mode: (o) DECLINE ( ) 503     | Mode: (o) DECLINE ( ) 503             |
|                                 |                               |                                       |
| Brownout Slider:                | Brownout Slider:              | Brownout Slider:                      |
| [=========|---------] 60%       | [=================--] 90%     | [================---] 85%             |
+---------------------------------------------------------------------------------------------------------+
```

1. **Mission-Control Trigger Button**:
   - Normal (Disarmed) State:
     - Background: `#111827`, border: `1px solid #1E3A5F`, text: `#F8FAFC`.
     - Label: `TRIGGER OUTAGE`.
     - Hover: Border shifts to Alert Rust (`#C1622D`).
   - Active (Armed Outage) State:
     - Background: `rgba(193, 98, 45, 0.25)`, border: `1px solid #C1622D`, text: `#C1622D`.
     - Label: `CLEAR OUTAGE (RECOVER)`.
     - Status badge alongside button: `[!] OUTAGE ACTIVE` in flashing or solid Rust monospace.

2. **Outage Behavior Selector (Radio Group)**:
   - Matches Phase 2 `OutageBehavior` enum (`acquirer_sim/models.py`):
     - `RETURN_DECLINE` (Default): Returns HTTP 200 with `authorized: false` and `decline_code: 'ACQUIRER_OUTAGE'`. Tests business decline classification.
     - `HTTP_503`: Returns HTTP 503 Service Unavailable. Tests transport gateway outage handling.
     - `LATENCY_SPIKE`: Injects a 500ms artificial network delay. Tests timeout and latency resilience.

3. **Brownout / Gray Failure Slider**:
   - Interactive HTML5 range input styled with hairline borders.
   - Adjusts base success rate $p \in [0.00, 1.00]$ in increments of $0.05$.
   - Readout alongside slider in monospace: `0.950` $\to$ `0.600`.
   - On change: Dispatches `POST /admin/success-rate` to simulated acquirer.

4. **One-Click Benchmark Scenario Gauntlets**:
   - Top deck provides quick presets mirroring Phase 6's QA scenarios:
     - **Preset 1: Standard Cliff**: Sets Acquirer Alpha to Outage (`effective_rate = 0.0%`) for 50 transactions.
     - **Preset 2: Sensitive Blip**: Triggers 3 consecutive declines on Acquirer Alpha, then recovers. (Demonstrates why $M=1$ static baseline crashes while Loom ignores transient blips).
     - **Preset 3: Gray Failure**: Sets Acquirer Alpha to $60\%$ PSR. (Demonstrates static router counter-reset paralysis vs Loom's proportional shedding).
     - **Global Reset Button**: Calls `POST /admin/reset` on all simulated acquirers and resets demo telemetry counters.

### 5.3 Network Contract & API Payloads
Ticket C interacts with the simulated acquirer services defined in Phase 2 (`acquirer_sim/app.py`).

To avoid browser Cross-Origin Resource Sharing (CORS) complications and eliminate hardcoded port maps in the client, **all operator commands route through the backend proxy in `router_core/app.py`**:

#### 1. Toggle Outage Contract:
- **Client Action**: `POST /api/simulator/acquirers/{id}/outage`
- **Proxy Target**: `POST http://127.0.0.1:8001/admin/outage` (or configured acquirer host)
- **Request Payload (JSON)**:
  ```json
  {
    "active": true,
    "behavior": "RETURN_DECLINE",
    "transition_seconds": 0.0
  }
  ```
- **Response Payload (JSON)** (`AdminStateResponse` from Phase 2):
  ```json
  {
    "acquirer_id": "acquirer_alpha",
    "base_success_rate": 0.95,
    "effective_success_rate": 0.0,
    "outage_active": true,
    "outage_behavior": "RETURN_DECLINE",
    "total_requests": 150,
    "authorized_count": 138,
    "declined_count": 12,
    "outage_declines": 8,
    "empirical_success_rate": 0.92,
    "uptime_seconds": 450.2
  }
  ```

#### 2. Update Success Rate (Gray Failure) Contract:
- **Client Action**: `POST /api/simulator/acquirers/{id}/success-rate`
- **Proxy Target**: `POST http://127.0.0.1:8001/admin/success-rate`
- **Request Payload (JSON)**:
  ```json
  {
    "success_rate": 0.60,
    "reason": "Operator Gray Failure Test"
  }
  ```

---

## 6. Ticket D Specification: WebSocket Wiring & Event Streaming Gateway

### 6.1 Objective & Scope
Establish the low-latency, real-time push transport connecting the React frontend to Phase 5's Redis Pub/Sub stream (`events:routing` and `events:health`). This ticket delivers both the backend WebSocket gateway in FastAPI and the resilient client-side streaming hook.

### 6.2 Backend WebSocket Gateway Bridge (`router_core/app.py`)

Per `docs/CONSTITUTION.md`:
> *"Dashboard: React, connected via native WebSocket (or FastAPI's WebSocket support on the backend side)"*

The backend WebSocket endpoint is added directly to `router_core/app.py`:

```python
# Backend WebSocket Route in router_core/app.py
@app.websocket("/ws/telemetry")
async def websocket_telemetry_endpoint(websocket: WebSocket) -> None:
    """Stream real-time RoutingEvents and HealthAlertEvents from Redis Pub/Sub to browser."""
    await websocket.accept()
    logger.info("Dashboard WebSocket client connected from %s", websocket.client)

    # 1. Send initial cold-start bootstrap snapshot
    bootstrap_payload = {
        "event_type": "BOOTSTRAP",
        "timestamp": time.time(),
        "states": active_router.get_all_states(),
        # Optional: recent transactions from SQLite if available
    }
    await websocket.send_text(json.dumps(bootstrap_payload, default=pydantic_encoder))

    # 2. Subscribe to Redis Pub/Sub channels using Phase 5's AsyncEventSubscriber
    subscriber = AsyncEventSubscriber(
        channels=["events:routing", "events:health"],
    )

    try:
        async for event in subscriber.listen():
            # Stream JSON string directly to WebSocket client
            await websocket.send_text(event.model_dump_json())
    except WebSocketDisconnect:
        logger.info("Dashboard WebSocket client disconnected")
    except Exception as exc:
        logger.warning("WebSocket streaming error: %s", exc)
    finally:
        await subscriber.aclose()
```

### 6.3 Client-Side Architecture & Resilient Hook (`useLoomTelemetry.js`)

Streaming 20 to 50 transactions per second directly into React `useState` triggers 50 state dispatches per second, causing severe virtual DOM churn, garbage collection pauses, and browser main-thread frame drops.

Ticket D mandates a **Decoupled Ring Buffer Architecture**:

```
[ WebSocket Frame Arrival ] (20-50 TPS)
           |
           v
[ JSON Parse & Validation ]
           |
           v
[ Circular In-Memory Ring Buffer ] (capacity: 200 events)
           |
           v (batched read at 30-60 Hz)
[ requestAnimationFrame / 30 FPS Ticker ]
           |
           v
[ React State Dispatch ] (Smooth 60 FPS UI Render)
```

1. **Circular Ring Buffer**:
   - Fixed capacity of $N = 200$ transaction events.
   - New events overwrite oldest events once buffer is full.
   - Guarantees constant memory consumption regardless of how long the dashboard tab remains open.

2. **Decoupled Render Ticker (`requestAnimationFrame`)**:
   - WebSocket `onmessage` handlers only append events to the mutable ring buffer without calling `setState`.
   - A single `requestAnimationFrame` loop drains buffer increments at the browser's native refresh rate (up to 60 FPS), dispatching batched updates to the chart and metric cards.

3. **Resilient Reconnection Policy**:
   - On connection drop (`onclose` / `onerror`):
     - Sets connection status indicator to `RECONNECTING` (blinking Rust badge).
     - Applies exponential backoff with jitter: $t_{\text{retry}} = \min(10\text{s}, 0.5\text{s} \times 2^{\text{attempt}}) \pm 200\text{ms}$.
   - Upon successful reconnection:
     - Automatically requests fresh bootstrap state to fill any gaps that occurred during disconnection.

4. **Connection Header Status Badge**:
   - Rendered in the top navigation bar:
     - `● LIVE STREAMING`: Solid Telemetry Amber indicator (`#D9A441`), displaying socket round-trip ping.
     - `○ RECONNECTING (Attempt 2)`: Blinking Alert Rust indicator (`#C1622D`).
     - `✕ DISCONNECTED`: Solid Alert Rust badge with manual "Reconnect" button.

### 6.4 Hybrid Cold-Start Bootstrap Pattern
Because Redis Pub/Sub is inherently at-most-once with zero persistence (Phase 5 ADR-03), a dashboard opening while traffic is already running would start with an empty chart.

**The Hybrid Bootstrap Protocol**:
1. Upon WebSocket connection open, the server sends a `BOOTSTRAP` frame containing:
   - The current `AcquirerStateSnapshot` map for all routes (`GET /state`).
   - The most recent 50 transaction records queried from SQLite `transactions` table.
2. The client hydrates the ring buffer with the recent history and immediately paints the allocation lines.
3. Subsequent live `RoutingEvent` frames append seamlessly to the tail of the chart.

---

## 7. Non-Functional Requirements & Performance Budgets

The live dashboard must operate seamlessly without degrading under sustained transaction bursts.

| Metric / Dimension | Target Budget | Upper Bound | Mechanism to Enforce Budget |
| :--- | :--- | :--- | :--- |
| **WebSocket Latency** | $< 2.0\,\text{ms}$ | $< 5.0\,\text{ms}$ | Localhost TCP socket, non-blocking Redis `asyncio` consumer. |
| **Dashboard Frame Rate**| $60\,\text{FPS}$ | $\ge 30\,\text{FPS}$ | Decoupled ring buffer with `requestAnimationFrame` rendering. |
| **Browser Memory Footprint**| $< 50\,\text{MB}$ | $< 100\,\text{MB}$ | Fixed 200-item circular ring buffer; zero unbounded arrays. |
| **Reconnection Recovery**| $< 1.0\,\text{s}$ | $< 3.0\,\text{s}$ | Exponential backoff starting at 500ms with hybrid bootstrap. |
| **Max Sustained TPS** | $100\,\text{TPS}$ | $200\,\text{TPS}$ | Batch state flushes; canvas/SVG element caching. |

---

## 8. Architecture Decision Records (ADRs)

### ADR-01: Locked Mission-Control Visual Direction
- **Context**: Loom is demonstrating a mathematically rigorous control system. Consumer SaaS designs with pastel gradients and bubbly cards dilute credibility.
- **Alternatives Considered**:
  1. *Default Tailwind UI kit*: Fast to build, but looks like a generic SaaS landing page.
  2. *Grafana embedded dashboard*: External dependency; violates PRD simplicity and lacks custom outage control triggers.
  3. *Custom Mission-Control Design System*: Navy ground (`#0B1120`), cockpit panel (`#111827`), hairline borders (`#1E3A5F`), strictly reserved Amber (`#D9A441`) and Rust (`#C1622D`).
- **Decision**: Option 3 (Custom Mission-Control Design System).
- **Trade-off**: Requires strict discipline in CSS token definitions, compensated by immediate operational gravitas and clarity during live demonstrations.

### ADR-02: FastAPI-Native WebSocket Gateway vs Dedicated Node.js Proxy
- **Context**: Connecting browser WebSockets to Redis Pub/Sub requires a gateway service.
- **Alternatives Considered**:
  1. *Standalone Node.js proxy (`ws` / `socket.io`)*: Introduces an additional runtime process and configuration overhead.
  2. *Native FastAPI WebSocket route in `router_core/app.py`*: Directly integrates with existing `AsyncEventSubscriber` in Python.
- **Decision**: Option 2 (Native FastAPI WebSocket endpoint `@app.websocket("/ws/telemetry")`).
- **Trade-off**: Slightly increases responsibility of `router_core/app.py`, compensated by eliminating an extra runtime daemon and preserving single-command execution.

### ADR-03: Decoupled Ring Buffer & `requestAnimationFrame` Throttling
- **Context**: Transaction arrival rates of 20–50 TPS cause 50 React state dispatches per second if wired naively, freezing the browser main thread.
- **Alternatives Considered**:
  1. *Direct `setState` on every message*: Simple, but causes severe UI lag and dropped frames under load.
  2. *Fixed timer throttling (`setInterval(..., 100ms)`)*: Works, but unsynchronized with browser display refresh cycles.
  3. *In-Memory Ring Buffer + `requestAnimationFrame`*: Incoming WebSocket messages append to a bounded array; a 60 FPS animation loop consumes and paints.
- **Decision**: Option 3.
- **Trade-off**: Microscopic display delay of $16\text{ms}$ (1 frame) in exchange for rock-solid 60 FPS animation without garbage collection pauses.

### ADR-04: Hybrid Bootstrap (SQLite History + Redis Live Stream)
- **Context**: Redis Pub/Sub has at-most-once delivery and no historical replay buffer. Opening the dashboard mid-demo would show an empty chart until new transactions arrive.
- **Alternatives Considered**:
  1. *Redis Streams with consumer groups*: Unnecessary broker complexity rejected in Phase 5 ADR-03.
  2. *Accept empty chart on load*: Poor user experience during live presentations.
  3. *Hybrid Bootstrap*: Send initial snapshot of recent transactions from SQLite / router memory upon WebSocket connection, then stream live Pub/Sub events.
- **Decision**: Option 3.
- **Trade-off**: Requires one SQLite read query upon client connection, compensated by instant chart population and seamless continuity.

### ADR-05: Backend Reverse Proxy for Acquirer Admin Outage Controls
- **Context**: The React client runs on `http://localhost:5173`. Acquirer simulators run on `http://127.0.0.1:8001`. Direct browser `fetch` triggers CORS restrictions and forces the frontend to maintain hardcoded acquirer port maps.
- **Alternatives Considered**:
  1. *Enable CORS on all simulated acquirers*: Couples frontend directly to internal simulator port topology.
  2. *Backend Reverse Proxy in `router_core/app.py` (`/api/simulator/...`)*: The router service proxies admin commands to candidate acquirers using its configured route table.
- **Decision**: Option 2.
- **Trade-off**: Adds lightweight proxy endpoints to FastAPI, compensated by clean frontend architecture and zero CORS configuration issues.

### ADR-06: Tabular Figures Monospace Typography
- **Context**: Real-time numerical readouts (e.g. `89.33%`, `0.042 ms`) flicker and shift horizontally if rendered in proportional sans-serif fonts as character widths vary.
- **Alternatives Considered**:
  1. *Standard sans-serif font for numbers*: Causes distracting layout jitter on every transaction update.
  2. *Tabular monospace font with `font-feature-settings: 'tnum'`*: Fixed-width numbers align perfectly in tables and gauges without jitter.
- **Decision**: Option 2.
- **Trade-off**: Visual contrast between labels and numbers, which actually enhances readability in a mission-control aesthetic.

---

## 9. Hand-Off Checklist for Frontend, Backend & QA Engineers

### Phase 7 Implementation Breakdown

#### Ticket A: Live Allocation Chart (`dashboard/src/components/AllocationChart.jsx`)
- [ ] Initialize React + Vite project under `dashboard/` configured with Tailwind CSS / CSS custom properties.
- [ ] Implement `AllocationChart.jsx` (using HTML5 Canvas, SVG, or lightweight Chart.js):
  - [ ] Fixed 0% to 100% Y-axis with hairline dashed gridlines (`#1E3A5F`).
  - [ ] 60-second rolling X-axis window.
  - [ ] Multi-line series: Alpha in Amber (`#D9A441`), Beta in Steel Cyan (`#38BDF8`), Gamma in Slate (`#94A3B8`).
  - [ ] Alpha line dynamic color shift to Alert Rust (`#C1622D`) on active outage.
  - [ ] Dynamic Outage-Event vertical marker in Rust with timestamped badge tag.
  - [ ] Recovery Marker vertical line in Amber.
  - [ ] Peak Step Delta readout ($\Delta w_{\text{max}}$) in top-right corner.

#### Ticket B: PSR & Health Metric Readouts (`dashboard/src/components/MetricReadouts.jsx`)
- [ ] Implement headline metric cards:
  - [ ] Lifetime Global PSR readout (monospace Amber, size 32px).
  - [ ] Rolling Window PSR (last 50 txs), dynamically shifting from Amber to Rust when $< 80\%$.
  - [ ] Stability Multiplier badge (`8.5x SMOOTHER` | `dw_max: 11.8% vs 100%`).
  - [ ] Routing compute latency readout ($t_{\text{routing}} < 0.1\text{ms}$).
- [ ] Implement `BaselineComparisonCard.jsx` displaying Phase 6 empirical audit numbers ($M=1$ collapse vs $M=3$ cliff vs Loom PID).
- [ ] Implement `AcquirerHealthCard.jsx` per configured route:
  - [ ] Decayed EWMA health score ($H$) with 4px micro-gauge bar.
  - [ ] Beta belief parameters ($\alpha, \beta, \mathbb{E}[\theta]$).
  - [ ] Instantaneous traffic allocation weight ($w_i$).
  - [ ] Acquirer network round-trip latency ($t_{\text{acquirer}}$).

#### Ticket C: Outage-Trigger Controls Per Acquirer (`dashboard/src/components/OperatorControls.jsx`)
- [ ] Implement per-acquirer control deck:
  - [ ] Two-state `TRIGGER OUTAGE` / `CLEAR OUTAGE` button with armed Rust state.
  - [ ] Outage behavior radio selector (`RETURN_DECLINE`, `HTTP_503`, `LATENCY_SPIKE`).
  - [ ] Brownout partial degradation slider ($p \in [0.0, 1.0]$) with monospace readout.
  - [ ] Preset buttons: Standard Cliff, Sensitive Blip ($M=1$), Gray Failure ($p=0.60$).
  - [ ] Global Reset button.
- [ ] Wire controls to backend proxy endpoints (`/api/simulator/acquirers/{id}/outage`).

#### Ticket D: WebSocket Gateway & Client Hook (`dashboard/src/hooks/useLoomTelemetry.js`)
- [ ] Implement backend WebSocket route `@app.websocket("/ws/telemetry")` in `router_core/app.py`:
  - [ ] Wire `AsyncEventSubscriber` to channels `events:routing` and `events:health`.
  - [ ] Emit initial `BOOTSTRAP` payload with current route states and recent transactions.
  - [ ] Clean subscriber teardown on client disconnect.
- [ ] Implement backend simulator proxy routes in `router_core/app.py` (`POST /api/simulator/...`).
- [ ] Implement client-side `useLoomTelemetry.js`:
  - [ ] Native WebSocket connection to `/ws/telemetry`.
  - [ ] Circular ring buffer with fixed capacity $N = 200$.
  - [ ] `requestAnimationFrame` render ticker for 60 FPS UI performance.
  - [ ] Auto-reconnect with exponential backoff and jitter.
  - [ ] Top bar connection status indicator badge (`LIVE`, `RECONNECTING`, `DISCONNECTED`).

#### QA & Verification (`tests/dashboard/`)
- [ ] Verify visual compliance: Ground `#0B1120`, Panel `#111827`, Hairline `#1E3A5F`, Amber `#D9A441`, Rust `#C1622D`.
- [ ] Verify zero box-shadows and flat/micro-radius geometry.
- [ ] Stress-test streaming at 50 TPS: verify browser memory remains $< 100\text{MB}$ and frame rate remains $\ge 50\,\text{FPS}$.
- [ ] End-to-end integration test: Start router + simulated acquirers, trigger outage via Ticket C, verify Rust outage marker appears on Ticket A chart, and verify PSR readout updates dynamically in Ticket B.

---

## 10. Phase 7 Visual & Information Architecture Revision Addendum

**Status**: Settled Revision Contract (Supersedes Section 2 & Component Layouts in Sections 3–5)
**Reference**: Architecture Decision Record (ADR) in `docs/decisions-log.md` (`[Phase 7 Revision]`)

### 10.1 Architectural Motivation & Design Rationale

During initial rehearsals, the original mission-control design (deep navy ground `#0B1120`, high-contrast amber `#D9A441`, and alert rust `#C1622D`) demonstrated three user-experience defects:
1. **Visual Fatigue & Semantic Incoherence**: Stacking multiple high-saturation warm colors (amber for primary numbers, rust for outage markers, steel cyan for secondary routes, slate for tertiary) created chromatic clutter that distracted from Loom's core mathematical claim (smooth PID damping vs discontinuous hard-switching).
2. **Sensor-Actuator Disconnect**: Placing acquirer health readouts in Ticket B and outage trigger buttons in Ticket C forced operators to look across the entire vertical viewport (800+ pixels) to correlate cause and effect.
3. **Viewport Vertical Bloat**: Rendering the Phase 6 benchmark audit cards and the full simulation preset deck inline forced the hero chart and operational gauges into a crowded scrolling page.
4. **Jumping Status Indicators**: Separate pills for verified stream, reconnecting attempts, and disconnected states caused layout jitter during connection lifecycle changes.

The revised specification establishes a **calm, industrial, high-density telemetry console**:
- Color is restricted to **one accent** (`#5B8DEF` cobalt blue) and **one alert** (`#E5484D` crimson).
- Typography shifts to `IBM Plex Sans` + `IBM Plex Mono` with strict **sentence case throughout** (eliminating shouting uppercase).
- Information architecture colocates controls directly with the telemetry they affect and folds secondary audits into collapsible disclosures.

---

### 10.2 Revised Color Palette Tokens

```
+---------------------------------------------------------------------------------------------------------+
| REVISED COLOR PALETTE MATRIX                                                                            |
+---------------------------------------------------------------------------------------------------------+
| Token             Hex Value   Semantic Discipline                                                       |
+---------------------------------------------------------------------------------------------------------+
| ground            #0F1115     Base canvas ground (industrial charcoal/slate). Zero blue saturation.    |
| panel             #16181D     Surface elevation for charts, telemetry gauges, and cards.               |
| border            #2A2D34     Razor 1px border demarcation for panels, dividers, axes, and tables.     |
| text-primary      #E4E6EB     Primary readable text (headings, labels, current values).                 |
| text-secondary    #8B8F98     Secondary muted text (units, timestamps, descriptors, inactive elements). |
| accent            #5B8DEF     SINGULAR ACCENT (Cobalt Blue): Active healthy line, nominal status dot,   |
|                               focused input ring. Strictly prohibited for generic decoration.           |
| alert             #E5484D     SINGULAR ALERT (Crimson): Active outage line, health degradation (<0.70), |
|                               outage event markers, disconnect dot. Strictly non-decorative.            |
+---------------------------------------------------------------------------------------------------------+
```

---

### 10.3 Revised Typography Contract

1. **Interface Copy Font (`IBM Plex Sans`)**:
   - Google Font: `IBM+Plex+Sans:wght@400;500;600`
   - Target: Headers, card titles, table column headers, form labels, buttons, disclosure summaries, tooltips.
   - Casing: **Strict sentence case throughout**.
     - Correct: `"Live traffic allocation"`, `"Rolling PSR (50 txs)"`, `"Diagnostics"`, `"Simulation controls"`, `"Trigger outage"`.
     - Prohibited: `"LIVE TRAFFIC ALLOCATION"`, `"ROLLING PSR (50 TXS)"`, `"// AUTONOMOUS ROUTING ENGINE"`.
2. **Numeric Telemetry Font (`IBM Plex Mono`)**:
   - Google Font: `IBM+Plex+Mono:wght@400;500;600`
   - Target: Monospace is **strictly reserved** for live numeric telemetry:
     - PSR percentages (`89.33%`, `94.0%`)
     - Health scores (`0.942`, `0.650`)
     - Round-trip latencies (`0.042 ms`, `21.4 ms`)
     - Allocation weights (`82.0%`, `15.0%`, `3.0%`)
     - Beta distribution parameters ($\alpha, \beta$)
     - Sequence counters and timestamps (`#1042`, `14:22:01.402`)
   - CSS Invariant: `font-feature-settings: "tnum" 1, "zero" 1;` for fixed character widths without layout jitter.

---

### 10.4 Revised Information Architecture & Wireframe

```
+---------------------------------------------------------------------------------------------------------+
| Loom mission control | Closed-loop routing telemetry                     Txs: 1,420 | [● Live]          |
+---------------------------------------------------------------------------------------------------------+
| HERO ALLOCATION CHART (60-second rolling window)                                                        |
| +-----------------------------------------------------------------------------------------------------+ |
| | 100% |-----------                                                                                   | |
| |      |           \                                      Outage: Acquirer Alpha                      | |
| |  80% |   Alpha    \                                    | (Tx #51 @ 14:22:01)                        | |
| |      |   (#5B8DEF) \                                   v (#E5484D)                                  | |
| |  40% |              \                      Beta (#8B8F98)                                           | |
| |      |               \                  .---------------------------------------                    | |
| |   0% +----------------------------------------------------------------------------------------------+ |
| |      -60s                      -40s                      -20s                                   Now | |
| +-----------------------------------------------------------------------------------------------------+ |
+---------------------------------------------------------------------------------------------------------+
| CLUSTER PERFORMANCE TELEMETRY                                                                           |
| [ Global PSR: 89.33% ]    [ Rolling PSR: 94.0% ]    [ Stability: 8.5x smoother ]   [ RTT: 0.042 ms ]    |
+---------------------------------------------------------------------------------------------------------+
| PER-ACQUIRER TELEMETRY & CONTROLS (Colocated Sensor + Actuator)                                         |
| +--------------------------------+ +--------------------------------+ +-------------------------------+ |
| | Acquirer Alpha (Primary)       | | Acquirer Beta (Secondary)      | | Acquirer Gamma (Tertiary)     | |
| | Status: [● Nominal]            | | Status: [● Nominal]            | | Status: [● Nominal]           | |
| | EWMA health: 0.942 [===--]     | | EWMA health: 0.910 [===--]     | | EWMA health: 0.854 [===--]    | |
| | Traffic share: 82.0%           | | Traffic share: 15.0%           | | Traffic share: 3.0%           | |
| | Beta params: 14.2 / 1.8        | | Beta params: 8.1 / 1.2         | | Beta params: 3.0 / 1.0        | |
| | ------------------------------ | | ------------------------------ | | ----------------------------- | |
| | [ Trigger outage ]             | | [ Trigger outage ]             | | [ Trigger outage ]            | |
| | Mode: (•) Decline  ( ) 503     | | Mode: (•) Decline  ( ) 503     | | Mode: (•) Decline  ( ) 503    | |
| | Brownout rate: [======-] 95%   | | Brownout rate: [======-] 90%   | | Brownout rate: [======-] 85%  | |
| +--------------------------------+ +--------------------------------+ +-------------------------------+ |
+---------------------------------------------------------------------------------------------------------+
| > Diagnostics (Click to expand Phase 6 benchmark audit: M=1 overreaction, M=3 cliff, gray failure)      |
+---------------------------------------------------------------------------------------------------------+
| > Simulation controls (Click to expand scenario presets: Standard cliff, Sensitive blip, Gray failure)  |
+---------------------------------------------------------------------------------------------------------+
```

---

### 10.5 Detailed Component Specification Updates

#### 1. Header Bar (`HeaderBar.jsx`)
- **Container**: Background `#16181D`, border-b `1px solid #2A2D34`, padding `10px 16px`.
- **Title Block**:
  - Title: `"Loom mission control"` in `IBM Plex Sans`, font size `13px`, font-weight `600`, color `#E4E6EB`.
  - Subtitle: `"Autonomous routing engine | PID-damped Thompson sampling"` in font size `11px`, color `#8B8F98`.
- **Persistent Status Element (Consolidated)**:
  - Single fixed-width container: `border border-[#2A2D34] bg-[#0F1115] px-2.5 py-1 flex items-center gap-2`.
  - State 1 (`connected`):
    - Dot: `w-2 h-2 rounded-full bg-[#5B8DEF]`.
    - Text: `"Live"` in `IBM Plex Sans`, font size `11px`, font-weight `500`, color `#E4E6EB`.
  - State 2 (`reconnecting`):
    - Dot: `w-2 h-2 rounded-full bg-[#E5484D] animate-pulse`.
    - Text: `"Reconnecting (attempt {reconnectAttempt})"` in color `#E4E6EB`.
  - State 3 (`disconnected`):
    - Dot: `w-2 h-2 rounded-full bg-[#E5484D]`.
    - Text: `"Connection lost"` in color `#E4E6EB`.
    - Inline action: `<button onClick={onReconnect} className="ml-1 text-[10px] text-[#5B8DEF] hover:underline">Reconnect</button>`.

#### 2. Hero Allocation Chart (`AllocationChart.jsx`)
- **Card Styling**: Background `#16181D`, border `1px solid #2A2D34`, box-shadow `none`.
- **Inner Canvas Well**: Background `#0F1115`.
- **Axes & Gridlines**:
  - Gridlines at 0%, 20%, 40%, 60%, 80%, 100%: Hairline `1px solid #2A2D34` with opacity `0.4`.
  - Y-Axis Labels: `"0%"`, `"20%"`, `"40%"`, `"60%"`, `"80%"`, `"100%"` in `IBM Plex Mono`, font size `10px`, color `#8B8F98`.
  - X-Axis Labels: `"-60s"`, `"-45s"`, `"-30s"`, `"-15s"`, `"Now"` in `IBM Plex Mono`, font size `10px`, color `#8B8F98`.
- **Line Color Discipline**:
  - **Primary Route (Alpha)**:
    - Nominal state: `#5B8DEF` (accent blue), line width `2px`.
    - Outage / Degraded state ($H < 0.70$ or active outage): Shifts to `#E5484D` (alert crimson).
  - **Secondary Route (Beta)**:
    - Muted line: `#8B8F98`, line width `1.5px`.
  - **Tertiary Route (Gamma)**:
    - Subdued line: `#8B8F98` with opacity `0.5`, line width `1px`.
- **Outage Event Marker**:
  - Vertical dashed line at outage sequence / timestamp: `stroke: #E5484D`, `strokeWidth: 1.5`, `strokeDasharray: "3 3"`.
  - Pinned header tag:
    `Outage: Acquirer Alpha (Tx #{seq})`
    Background `rgba(229, 72, 77, 0.15)`, border `1px solid #E5484D`, text `#E5484D` in `IBM Plex Mono` (font size `9px`).
- **Header Readout**:
  - Title: `"Live traffic allocation"` in `IBM Plex Sans`, color `#E4E6EB`.
  - Smoothness metric: `"Peak step delta: 11.77%"` (`IBM Plex Mono`, color `#5B8DEF`), subtext `"(PID damped vs 100% cliff)"` in `#8B8F98`.

#### 3. Per-Acquirer Colocated Health & Controls (`MetricReadouts.jsx`)
- **3-Column Grid**: One card per acquirer (`alpha`, `beta`, `gamma`).
- **Card Container**: Background `#16181D`, border `1px solid #2A2D34`. If active outage, border highlights to `#E5484D`.
- **Observation Block (Upper)**:
  - Header: Acquirer Name (e.g. `"Acquirer Alpha"`), Role (`"Primary leader"`).
  - Status indicator: Dot + text (`"Nominal"` with `#5B8DEF` dot, or `"Outage active"` with `#E5484D` dot).
  - EWMA health score ($H$): Readout in `IBM Plex Mono` (e.g. `0.942`). Micro-gauge bar (4px height) with fill color `#5B8DEF` ($\ge 0.70$) or `#E5484D` ($< 0.70$).
  - Belief & Allocation Subgrid:
    - Traffic share: `w_i` (e.g. `82.0%`) in `IBM Plex Mono`.
    - Beta parameters: `alpha / beta` in `IBM Plex Mono`.
    - Expected PSR: $\mathbb{E}[\theta]$ in `IBM Plex Mono`.
- **Actuation Block (Lower - Physically Adjacent)**:
  - Divided by subtle hairline divider (`border-t border-[#2A2D34]`).
  - Outage Toggle Button:
    - Inactive state: Background `#0F1115`, border `1px solid #2A2D34`, text `#E4E6EB`. Label: `"Trigger outage"`. Hover: border `#E5484D`.
    - Armed outage state: Background `rgba(229, 72, 77, 0.15)`, border `1px solid #E5484D`, text `#E5484D`. Label: `"Clear outage"`.
  - Behavior radio selectors: Inline options for `"Decline"`, `"503"`, `"Latency spike"`.
  - Gray failure slider: Range slider with live numeric readout in `IBM Plex Mono` (`"95%"`).

#### 4. Progressive Disclosures (`App.jsx`)
- **Disclosure 1: "Diagnostics"**:
  ```jsx
  <details className="mission-panel p-3 border border-[#2A2D34] group">
    <summary className="cursor-pointer font-sans font-medium text-xs text-[#E4E6EB] hover:text-[#5B8DEF] flex items-center justify-between list-none">
      <span>Diagnostics (Phase 6 benchmark comparison audit)</span>
      <span className="text-[#8B8F98] text-[10px] group-open:rotate-180 transition-transform">▼</span>
    </summary>
    <div className="pt-3 border-t border-[#2A2D34] mt-2">
      <BaselineComparisonCard />
    </div>
  </details>
  ```
- **Disclosure 2: "Simulation controls"**:
  ```jsx
  <details className="mission-panel p-3 border border-[#2A2D34] group">
    <summary className="cursor-pointer font-sans font-medium text-xs text-[#E4E6EB] hover:text-[#5B8DEF] flex items-center justify-between list-none">
      <span>Simulation controls (Benchmark scenario presets & reset)</span>
      <span className="text-[#8B8F98] text-[10px] group-open:rotate-180 transition-transform">▼</span>
    </summary>
    <div className="pt-3 border-t border-[#2A2D34] mt-2">
      <GlobalPresetDeck onSelectPreset={...} onReset={...} />
    </div>
  </details>
  ```
- Both disclosures are **closed by default** (`open={false}`), ensuring the hero chart and operational telemetry occupy 100% of the initial above-the-fold viewport.

---

### 10.6 Verification & Handoff Criteria for Frontend Engineer

- [ ] `index.html`: Update Google Fonts preconnect to load `IBM+Plex+Sans:wght@400;500;600` and `IBM+Plex+Mono:wght@400;500;600`.
- [ ] `tailwind.config.js` and `index.css`: Configure revised tokens (`ground: #0F1115`, `panel: #16181D`, `border: #2A2D34`, `text-primary: #E4E6EB`, `text-secondary: #8B8F98`, `accent: #5B8DEF`, `alert: #E5484D`).
- [ ] `HeaderBar.jsx`: Replace multiple badge states with the consolidated single status element. Convert text to sentence case.
- [ ] `AllocationChart.jsx`: Retain 60s rolling window; update primary line to `#5B8DEF` (nominal) / `#E5484D` (outage); update markers to `#E5484D`.
- [ ] `MetricReadouts.jsx`: Move per-acquirer outage trigger buttons and mode selectors directly into each acquirer's card.
- [ ] `App.jsx`: Wrap `BaselineComparisonCard` in `<details>` `"Diagnostics"` (default-closed) and scenario presets in `<details>` `"Simulation controls"` (default-closed).
- [ ] General: Verify strictly sentence case throughout, zero tracked-out caps, and monospace restricted exclusively to numeric telemetry.

---

## 11. Phase 7 Visual Revision 3: Deterministic Acquirer Color Mapping & Inviolable Alert Override

**Status**: Settled Architectural Specification (Supersedes Section 10.5 Chart Line Assignments)
**Reference**: Decisions Log (`[Phase 7 Revision 3]`)
**Scope**: Chart series line colors, cross-surface acquirer identity mapping, and emergency alert preemption rules.

### 11.1 Problem Statement & Trade-Off Rationale

Revision 2 successfully eliminated chromatic noise by adopting a single accent (`#5B8DEF`) and rendering backup routes in muted secondary text tones (`#8B8F98`). However, during live outage demonstrations, this created an **observability defect**:
- When Acquirer Alpha shed volume, secondary (Beta) and tertiary (Gamma) traffic lines crossed over while both rendering in identical or near-identical gray tones.
- Demonstration observers and operators could not discern at a glance whether volume was dampening cleanly into Beta or leaking into Gamma.

Revision 3 establishes **assigned, non-decorative color identities** per acquirer:
1. **Acquirer 1 (Alpha / Primary)**: **Accent (`#5B8DEF`)** (cobalt blue).
2. **Acquirer 2 (Beta / Secondary)**: **Accent 2 (`#C084FC`)** (soft violet). Provides clear multi-arm line discrimination without competing in saturation with the primary blue.
3. **Acquirer 3 (Gamma / Tertiary)**: **Secondary text gray (`#7C808A`)**. Avoids introducing a third saturated color (e.g. green or amber) for a route that operates primarily as a 3% exploration floor.

---

### 11.2 The Inviolable Alert Override Contract

$$\text{ActiveColor}(i, t) = \begin{cases} \text{\#E5484D (Alert Crimson)}, & \text{if Acquirer } i \text{ is in Outage or Degraded } (H_i(t) < 0.70) \\ \text{AssignedColor}(i), & \text{otherwise (Nominal / Healthy)} \end{cases}$$

- **Universal Preemption**: The exact moment an acquirer experiences an outage or health degradation below $0.70$, **Alert (`#E5484D`) immediately overrides that acquirer's assigned color**, regardless of whether it is Alpha, Beta, or Gamma.
- **Failback Hysteresis**: When the outage clears and EWMA health restores ($H \ge 0.70$), the acquirer reverts to its assigned nominal color.
- **Chart Outage Marker**: Vertical pinned marker lines and tag badges render strictly in **Alert (`#E5484D`)**.

---

### 11.3 Cross-Surface Identity Mapping Table

| Component Surface | Acquirer Alpha (Primary) | Acquirer Beta (Secondary) | Acquirer Gamma (Tertiary) | Failure / Outage State (Any Acquirer) |
| :--- | :--- | :--- | :--- | :--- |
| **Chart Allocation Line** | `#5B8DEF` (width 2px) | `#C084FC` (width 1.5px) | `#7C808A` (width 1px) | **`#E5484D`** (alert crimson) |
| **Chart Legend Indicator** | Bar `#5B8DEF`, text `#5B8DEF` | Bar `#C084FC`, text `#C084FC` | Bar `#7C808A`, text `#7C808A` | Badge `[!] Outage` in `#E5484D` |
| **Health Status Dot** | Dot `#5B8DEF` (`Nominal`) | Dot `#C084FC` (`Nominal`) | Dot `#7C808A` (`Nominal`) | Dot `#E5484D` (`Outage active`, pulsing) |
| **EWMA Health Gauge Fill** | Fill `#5B8DEF` ($H \ge 0.70$) | Fill `#C084FC` ($H \ge 0.70$) | Fill `#7C808A` ($H \ge 0.70$) | Fill `#E5484D` ($H < 0.70$) |
| **Card Active Border** | Border `#2A2D34` | Border `#2A2D34` | Border `#2A2D34` | Border `#E5484D` |

---

### 11.4 Strict Negative Invariants (Quarantine Rules)

1. **Alert (`#E5484D`) Quarantine**:
   - Alert is strictly reserved for failure conditions:
     1. Acquirer line / badge / micro-gauge currently in an outage or degraded state.
     2. Pinned vertical outage event marker on the chart.
     3. WebSocket disconnection indicator (`Connection lost` / `Reconnecting`).
   - Alert must **never** appear anywhere else on the dashboard for decorative borders, regular buttons, general badges, or healthy metrics.
2. **Accent 2 (`#C084FC`) Quarantine**:
   - Accent 2 is strictly reserved for the second acquirer's identity (Beta).
   - It must **never** be used for general UI elements, button hovers, diagnostics panels, or other telemetry labels.

---

### 11.5 Handoff Checklist for Frontend Engineer

- [ ] `AllocationChart.jsx`: Update line colors: Alpha (`#5B8DEF` $\to$ `#E5484D` on outage), Beta (`#C084FC` $\to$ `#E5484D` on outage), Gamma (`#7C808A` $\to$ `#E5484D` on outage). Update legend bar and percentage colors to match.
- [ ] `MetricReadouts.jsx`: Update per-acquirer nominal status dots and EWMA micro-gauge fills to match assigned colors (`#5B8DEF`, `#C084FC`, `#7C808A`), overriding to `#E5484D` on outage.
- [ ] `tailwind.config.js`: Add `'route-beta': '#C084FC'` and `'route-gamma': '#7C808A'` tokens if needed, ensuring `#C084FC` is quarantined from generic UI classes.

---

## 12. Phase 7 Visual Revision 4: Typography & Minimalist Text Density Contract

**Status**: Settled Architectural Specification (Supersedes Sections 10.4, 10.5, and 10.6 regarding typography and component density)
**Reference**: Decisions Log (`[Phase 7 Revision 4]`)
**Design Reference**: Approved Minimalist Cockpit Mockup
**Scope**: Complete typographic triad migration, elimination of container boxes/panels around headline figures, single-row acquirer representation, plain text-style outage controls, and zero-footprint progressive disclosure.

---

### 12.1 Problem Statement & Architectural Rationale

While Phase 7 Revision 3 achieved deterministic acquirer color mapping and alert override discipline, its component presentation suffered from **visual over-containerization and metric redundancy**:
1. **Container Fatigue**: Wrapping every metric inside a bordered `.mission-panel` card created 7 distinct rectangular boxes on screen simultaneously, visually fragmenting the telemetry instead of presenting an integrated real-time surface.
2. **Redundant Metric Representations**: An acquirer's volume and operational state were displayed simultaneously across the hero chart line, legend percentage, card header badge, 4px micro-gauge bar, and subgrid parameter row—repeating identical facts up to 4 times per acquirer.
3. **Muted Headline Impact**: Rolling PSR and Baseline Lift—the critical numbers that prove the thesis of dynamic routing—were constrained inside small, uniform cards identical in size to latency and lifetime counters.
4. **Heavy Disclosure Footprint**: Even when closed, `<details>` disclosure panels rendered heavy rectangular borders across the full viewport width, adding visual weight before being opened.

Revision 4 resolves these defects by implementing the approved mockup's **extreme text density reduction** and **typographic triad discipline**.

---

### 12.2 The Typographic Triad

All previous typography rules (`IBM Plex Sans` and `IBM Plex Mono`) are replaced with a dedicated triad:

| Typeface | Weight / Style | Strict Dedicated Role | Permitted UI Elements |
| :--- | :--- | :--- | :--- |
| **`Space Grotesk`** | **700 (Bold)** | **Wordmark & Headline Metric Magnitudes** | • Primary Wordmark (`"Loom"`)<br>• Headline Rolling PSR value (e.g. `86.0%`)<br>• Headline Baseline PSR Lift value (e.g. `+1000 bps` or `+10.0%`) |
| **`Space Mono`** | **400 (Regular) / 700 (Bold)** | **Live Telemetry Figures** | • Per-acquirer health scores / allocation shares (`0.942`, `82.0%`)<br>• Live chart Y-axis ticks (`0%`, `50%`, `100%`) & X-axis markers (`-60s`, `Now`)<br>• Peak step delta readout (`11.8%`)<br>• Live throughput and latency values (`146 TPS`, `0.042 ms`) |
| **`Inter`** | **400 / 500 / 600** | **All Interface Labels & Copy** | • Metric descriptions (`"Rolling PSR (50 txs)"`, `"PSR lift vs baseline"`)<br>• Acquirer names (`"Alpha"`, `"Beta"`, `"Gamma"`)<br>• Status state text (`"Healthy"`, `"Degraded"`, `"Connection lost"`)<br>• Text-style action buttons (`"trigger outage"`, `"clear outage"`)<br>• Section headers and diagnostic audit copy |

#### Strict Typographic Quarantine:
- `Space Grotesk` is **forbidden** from regular labels, subheads, and secondary telemetry.
- `Space Mono` is **forbidden** from descriptive text, button labels, or status copy.
- `Inter` is **forbidden** from large headline numbers or live telemetry figures.

---

### 12.3 Mockup-Aligned Density & Layout Schemas

```
+-------------------------------------------------------------------------+
| Loom                                                                    |
| * Healthy                                                               |
|                                                                         |
| Rolling PSR (50 txs)           PSR lift vs baseline                     |
| 86.0%                          +1000 bps                                |
|                                                                         |
| * Alpha   0.942   [trigger outage]                                      |
| * Beta    1.000   [trigger outage]                                      |
| * Gamma   1.000   [trigger outage]                                      |
|                                                                         |
| Diagnostics >                                                           |
|                                                                         |
| [=================== Live Traffic Allocation Chart ===================] |
+-------------------------------------------------------------------------+
```

#### 12.3.1 Header Wordmark & Single Status Line
- **Wordmark**: Left-aligned `"Loom"` rendered in `Space Grotesk` 700 (`text-2xl font-bold tracking-tight text-[#E4E6EB]`).
- **Single Status Line**: Directly beneath the wordmark (no navbar, no right-aligned widget boxes):
  - Format: `[Dot] [Word]`
  - Healthy: `w-1.5 h-1.5 rounded-full bg-[#5B8DEF]` + `"Healthy"` (`Inter`, 12px, `#8B8F98`).
  - Degraded / Outage: `w-1.5 h-1.5 rounded-full bg-[#E5484D] animate-pulse` + `"Degraded"` (`Inter`, 12px, `#E5484D`).
  - Connection lost: `w-1.5 h-1.5 rounded-full bg-[#E5484D]` + `"Connection lost"` (`Inter`, 12px, `#E5484D`) + inline action `<button className="text-[10px] text-[#8B8F98] hover:underline">Reconnect</button>`.

#### 12.3.2 Borderless Headline Metrics
- The **two largest elements on screen**, positioned side-by-side.
- Strictly **label-plus-number** with:
  - Zero surrounding border (`border-0`).
  - Zero panel background (`bg-transparent`).
  - Zero drop shadows (`box-shadow: none`).
- **Left Element (Rolling PSR)**:
  - Label: `"Rolling PSR (50 txs)"` in `Inter`, 12px, font-medium, color `#8B8F98`.
  - Value: Monospace/grotesk magnitude in `Space Grotesk` 700 (`text-5xl font-bold tracking-tight`), colored `#5B8DEF` (nominal) or `#E5484D` (danger $<80.0\%$).
- **Right Element (Baseline Lift)**:
  - Label: `"PSR lift vs baseline"` in `Inter`, 12px, font-medium, color `#8B8F98`.
  - Value: Magnitude in `Space Grotesk` 700 (`text-5xl font-bold tracking-tight`), colored `#5B8DEF`.

#### 12.3.3 Single-Row Per-Acquirer Strip
- **Complete Elimination**: Per-acquirer cards, 4px micro-gauge bars, and subgrids are completely removed.
- **Compact Row Structure**: Each acquirer is rendered as an understated, single horizontal row:
  - `Dot`: 8px circle (`w-2 h-2 rounded-full`) in assigned color (`#5B8DEF` Alpha, `#C084FC` Beta, `#7C808A` Gamma), overridden by `#E5484D` during outage.
  - `Name`: `"Alpha"`, `"Beta"`, `"Gamma"` in `Inter`, 13px, font-medium, color `#E4E6EB`.
  - `Figure`: Live health score ($H$, e.g. `0.942`) or traffic split percentage (`82.0%`) in `Space Mono`, 13px, colored per assigned identity or alert override.
  - `Actuator Action`: Inline text-style button (`[trigger outage]` / `[clear outage]`).

#### 12.3.4 Plain Text-Style Outage Controls
- Outage-trigger controls must **NOT** be bordered cards, rectangular buttons, or bulky widgets.
- Styled strictly as plain text links/buttons:
  - CSS: `text-xs text-[#8B8F98] hover:text-[#E4E6EB] hover:underline bg-transparent border-0 p-0 cursor-pointer font-sans transition-colors`.
  - Inactive State: `"trigger outage"`.
  - Active Outage State: `"clear outage"` in Alert Crimson (`text-[#E5484D]`).
  - Pending State: `"dispatching..."` (`opacity-50 pointer-events-none`).

#### 12.3.5 Zero-Footprint Progressive Disclosure (`'Diagnostics ›'`)
- The Phase 6 comparative audit and simulation deck are collapsed behind an understated inline text link: `'Diagnostics ›'`.
- **Zero Border Footprint**:
  - Closed state must **NOT** render any visible `<details>` panel borders or box containers.
  - Rendered as clean text in `Inter` (`text-xs text-[#8B8F98] hover:text-[#5B8DEF] cursor-pointer flex items-center gap-1`).
  - Expanding the link reveals the benchmark cards and simulation deck below without cluttering the initial view.

---

### 12.4 Preserved Visual & Semantic Invariants

All color mapping and safety quarantine rules from Revision 3 are preserved without modification:
1. **Base Palette**: Slate Ground (`#0F1115`), Panel (`#16181D`), Border (`#2A2D34`), Text Primary (`#E4E6EB`), Text Secondary (`#8B8F98`).
2. **Deterministic Chart Line Colors**:
   - Alpha (Leader): Accent (`#5B8DEF`), 2.0px stroke.
   - Beta (Backup): Accent 2 (`#C084FC`), 1.5px stroke.
   - Gamma (Floor): Secondary text gray (`#7C808A`), 1.0px stroke.
3. **Inviolable Alert Override**:
   $$\text{ActiveColor}(i, t) = \begin{cases} \text{\#E5484D (Alert)}, & \text{if Acquirer } i \text{ is in Outage or Degraded } (H_i(t) < 0.70) \\ \text{AssignedColor}(i), & \text{otherwise (Nominal / Healthy)} \end{cases}$$
4. **Quarantine Invariants**:
   - Alert (`#E5484D`) is strictly reserved for failures, markers, and disconnects.
   - Accent 2 (`#C084FC`) is strictly quarantined to Acquirer Beta identity.

---

### 12.5 Handoff Specification for Frontend Engineer

1. **Google Fonts Configuration (`index.html`)**:
   - Load `Space+Grotesk:wght@700`, `Space+Mono:wght@400;700`, and `Inter:wght@400;500;600`.
   - Remove `IBM+Plex+Sans` and `IBM+Plex+Mono`.
2. **Tailwind Config (`tailwind.config.js`)**:
   - Define `fontFamily`:
     - `sans`: `['Inter', 'sans-serif']`
     - `mono`: `['Space Mono', 'monospace']`
     - `headline`: `['Space Grotesk', 'sans-serif']`
3. **Component Refactoring**:
   - `HeaderBar.jsx`: Convert to wordmark (`"Loom"`, `Space Grotesk` 700) + single status line (`● Healthy`, `Inter`). Remove right-hand metric pills.
   - `MetricReadouts.jsx`:
     - Remove 4-panel telemetry card row; render Rolling PSR and Baseline Lift as borderless headline numbers side-by-side (`Space Grotesk` 700).
     - Remove 3-card acquirer grid and 4px micro-gauge bars; render each acquirer as a single horizontal row (`Dot` + `Name` + `Figure` + `[trigger outage]`).
     - Render outage buttons as plain text buttons (`bg-transparent border-0 text-xs`).
   - `App.jsx`:
     - Replace `<details className="mission-panel p-3 border ...">` with a minimal text-link disclosure: `'Diagnostics ›'`.
    - `AllocationChart.jsx`:
      - Keep chart canvas, 60s rolling history, line stroke hierarchy, and Revision 3 colors intact. Ensure axis ticks and labels use `Space Mono` and `Inter`.

---

## 13. Phase 7 Visual Revision 5: Humanized Architecture Descriptions & Minimized Simulation Harness

**Status**: Settled Architectural Specification (Supersedes Section 10.5 & Section 12 regarding footer architecture strings and simulation controls)
**Reference**: Decisions Log (`[Phase 7 Revision 5]`)
**Scope**: Footer architectural description humanization, removal of build version strings, strict spatial separation (one line each, zero dots/pipes), minimization of the simulation harness to single-row per acquirer, and progressive disclosure for advanced simulator controls (`'Simulation settings ›'`).

---

### 13.1 Motivation & Architectural Trade-offs

During executive rehearsals and technical reviews, two areas of visual friction were identified in the Revision 4 cockpit:

1. **Cognitive Inscription vs Operational Intent in Footer Diagnostics**:
   - The footer contained raw configuration formulas and library designations: `PID [Kp=0.12, Ki=0.005, Kd=0.25, M=3]`, `Decayed Thompson sampling [tau=60s]`, `Append-only SQLite ledger`, and `Real-time Redis dispatch`.
   - Concatenating these items into inline strings with middle-dots (`•`) and pipes (`|`) created horizontal visual noise and forced evaluators to parse configuration syntax rather than comprehending what the system is actively doing.
   - Including static build metadata (`Loom protocol v0.7.0`) added zero operational value to a live telemetry console.
   - *Architectural Resolution*: Replace raw configuration strings with clear, humanized statements of system behavior, format them strictly as independent lines (zero dots, zero pipes), and excise the version string entirely.

2. **Simulation Harness Structural Asymmetry & Clutter**:
   - While the primary telemetry strip was radically reduced to single rows in Revision 4, the operator actuation deck remained comparatively heavy, containing failure-mode selectors, brownout sliders, and scenario preset cards in a separate block.
   - *Architectural Resolution*: Minimize the primary simulation harness to mirror the dashboard's per-acquirer row pattern: `[Name] [Current State] [Trigger outage / Clear outage]`. Move all advanced controls (sliders, mode toggles, presets, reset) behind a single collapsed-by-default disclosure link (`'Simulation settings ›'`), preserving 100% of simulator actuation capabilities with zero resting footprint.

---

### 13.2 Humanized Architecture Copy & Layout Specification

#### 13.2.1 Plain English Architecture Copy Mapping

All configuration formulas and parameter brackets are replaced by human-readable statements of operational purpose:

| Subsystem Component | Superseded Technical String | Humanized Plain Copy Specification |
| :--- | :--- | :--- |
| **PID Damping Controller** | `Closed-loop PID controller [Kp=0.12, Ki=0.005, Kd=0.25, M=3]` | `'Smooths every reroute so traffic never jumps'` |
| **Thompson Sampling Engine** | `Decayed Thompson sampling [tau=60s]` | `'Learns which gateway is healthiest, weighted toward the last minute'` |
| **Audit Data Layer** | `Append-only SQLite ledger` | `'Every decision is logged, permanently'` |
| **Event Dispatch Layer** | `Real-time Redis dispatch` | `'Reacts to every transaction instantly'` |

#### 13.2.2 Strict Spatial Separation & Typographic Invariants

- **Strict One-Line-Each Rule**: Each of the four architectural statements must be rendered on its own dedicated line.
- **Strict Negative Invariant (Zero Middle-Dots & Zero Pipes)**:
  - Text must **NEVER** be concatenated with middle-dots (`•`), bullets, slashes, or vertical pipes (`|`).
  - Prohibited: `Smooths every reroute so traffic never jumps • Learns which gateway is healthiest...`
  - Prohibited: `Every decision is logged, permanently | Reacts to every transaction instantly`
- **Excision of Build Version**: The string `"Loom protocol v0.7.0"` (or any version artifact) is **strictly deleted** from the live cockpit view.
- **Typography & Styling**:
  - Font: `Inter` (weight 400 Regular).
  - Size: `11px` (`text-[11px]`) or `12px` (`text-xs`).
  - Color: Secondary Text Gray (`#8B8F98`).
  - Layout: Clean vertical stack with relaxed line-height (`leading-relaxed`), providing open whitespace and high scannability.

---

### 13.3 Minimized Per-Acquirer Simulation Harness

#### 13.3.1 Primary Per-Acquirer Row Pattern

The resting simulation harness matches the single-row density of the primary telemetry view:

```
+-------------------------------------------------------------------------+
| SIMULATION CONTROLS                                                     |
|                                                                         |
| Alpha   ● Nominal         [Trigger outage]                              |
| Beta    ● Nominal         [Trigger outage]                              |
| Gamma   ● Nominal         [Trigger outage]                              |
|                                                                         |
| Simulation settings ›                                                   |
+-------------------------------------------------------------------------+
```

#### 13.3.2 Row Elements & Behavioral States

Each acquirer row contains strictly three items:
1. **Acquirer Name**: `"Alpha"`, `"Beta"`, `"Gamma"` (`Inter`, 13px, font-medium, color `#E4E6EB`).
2. **Current State**:
   - Nominal: 6px dot (`#5B8DEF`) + text `"Nominal"` (`Inter`, 12px, color `#8B8F98`).
   - Active Outage: 6px pulsing dot (`#E5484D animate-pulse`) + text `"Outage active"` (`Inter`, 12px, color `#E5484D`).
3. **Single Outage Text-Button**:
   - Styling: Plain text button (`bg-transparent border-0 p-0 text-xs font-sans cursor-pointer transition-colors hover:underline`).
   - Inactive: `"Trigger outage"` in muted secondary text (`text-[#8B8F98] hover:text-[#E4E6EB]`).
   - Active: `"Clear outage"` in Alert Crimson (`text-[#E5484D]`).
   - In-Flight Transit: `"dispatching..."` (`opacity-50 pointer-events-none`).

**Zero Default Visibility**: Sliders, behavior mode selectors, and preset decks must **NOT** appear in this primary row.

---

### 13.4 Progressive Disclosure: `'Simulation settings ›'`

#### 13.4.1 Zero-Footprint Text Link

Advanced simulator configuration is housed behind a single disclosure text link:
- Label: `'Simulation settings ›'`
- Styling: Plain text link in `Inter` (`text-xs text-[#8B8F98] hover:text-[#5B8DEF] inline-flex items-center gap-1 select-none cursor-pointer transition-colors`).
- Default State: **Closed by default** (`open={false}`).
- Zero Container Footprint: Closed state must **NOT** render any rectangular boxes, borderlines, or card backgrounds.

#### 13.4.2 Tucked Advanced Controls (100% Retained)

When expanded, the disclosure reveals the complete simulation test suite:
1. **Failure-Behavior-Mode Selectors**:
   - Radios for `RETURN_DECLINE` (default decline), `HTTP_503` (service unavailable), and `LATENCY_SPIKE` (delayed response) per acquirer.
2. **Success-Rate (Brownout) Sliders**:
   - Range slider for $p \in [0.0, 1.0]$ with live tabular numeric readout in `Space Mono` (`"95%"`, `"60%"`) per acquirer.
3. **Benchmark Scenario Gauntlet Presets**:
   - *Preset 1: Standard Cliff* ($M=3$ hard outage on Alpha).
   - *Preset 2: Sensitive Blip* ($M=1$ transient dip on Alpha, 3.5s auto-recovery).
   - *Preset 3: Gray Failure* ($p=0.60$ brownout degradation on Alpha).
4. **Global Reset Action**:
   - `"Reset all routes"` button invoking `/api/simulator/admin/reset` and restoring default slider rates ($0.95, 0.90, 0.85$).

---

### 13.5 Additive Functional Guarantee & QA Verification Matrix

The architectural revision is **strictly additive and simplifying**—no capabilities are deleted or broken.

| Functional Capability | Phase Built | Revision 4 Location | Revision 5 Location | Backend Proxy Endpoint | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Outage Toggle (Step Outage)** | Phase 2 | Per-acquirer strip | Primary harness row | `POST /api/simulator/acquirers/{id}/outage` | **Active** |
| **Failure Behavior Mode** | Phase 2 | Outage payload | Inside `'Simulation settings ›'` | `POST /api/simulator/acquirers/{id}/outage` | **Active** |
| **Base Success Rate Sliders** | Phase 2 | Disabled/Hidden | Inside `'Simulation settings ›'` | `POST /api/simulator/acquirers/{id}/success-rate` | **Active** |
| **Preset 1 (Standard Cliff)** | Phase 6 / 7 | OperatorControls | Inside `'Simulation settings ›'` | Orchestrated `/outage` | **Active** |
| **Preset 2 (Sensitive Blip)** | Phase 6 / 7 | OperatorControls | Inside `'Simulation settings ›'` | Orchestrated `/outage` + timer | **Active** |
| **Preset 3 (Gray Failure)** | Phase 6 / 7 | OperatorControls | Inside `'Simulation settings ›'` | Orchestrated `/success-rate` | **Active** |
| **Global Simulator Reset** | Phase 2 / 7 | OperatorControls | Inside `'Simulation settings ›'` | `POST /api/simulator/admin/reset` | **Active** |
| **Automated Test Suites** | Phase 7 | E2E & unit tests | Fully preserved | All proxy routes | **100% Pass** |

---

### 13.6 Frontend Implementation & Handoff Checklist

#### 1. Footer Bar Refactoring (`dashboard/src/App.jsx`)
- [ ] Excise `"Loom protocol v0.7.0"` and all version strings.
- [ ] Replace config strings with the four humanized plain descriptions:
  - `'Smooths every reroute so traffic never jumps'`
  - `'Learns which gateway is healthiest, weighted toward the last minute'`
  - `'Every decision is logged, permanently'`
  - `'Reacts to every transaction instantly'`
- [ ] Render as one line each; remove all middle-dot (`•`) and pipe (`|`) separators.
- [ ] Apply `Inter` font, `text-[11px]` / `text-xs`, color `#8B8F98`, relaxed vertical spacing.

#### 2. Minimized Simulation Harness (`dashboard/src/components/OperatorControls.jsx` / `MetricReadouts.jsx`)
- [ ] Ensure the primary acquirer harness presents strictly one row per acquirer: `[Name] [Current State] [Trigger/Clear outage]`.
- [ ] Ensure sliders, mode radios, and scenario presets are not visible in the primary resting view.
- [ ] Wrap advanced sliders, behavior selectors, preset gauntlets, and global reset behind a `<details>` disclosure with summary text `'Simulation settings ›'`.
- [ ] Ensure `'Simulation settings ›'` has zero container border footprint when collapsed, matching `'Diagnostics ›'`.

#### 3. Verification & Validation
- [ ] Run `npm run build` inside `dashboard/` to confirm zero JSX/TSX or styling syntax errors.
- [ ] Run `pytest tests/dashboard/` to confirm all simulator proxy tests and scenario tests pass.
- [ ] Run `pytest tests/router_core/test_phase7_dashboard_integration.py` to confirm full integration integrity.

---

## 14. Phase 7 Visual Revision 6: Static-Baseline Reference Curve Overlay on Live Allocation Chart

**Status**: Settled Architectural Specification (Extends Hero Allocation Chart in Section 10.5 & Section 12)
**Reference**: Decisions Log (`[Phase 7 Revision 6]`)
**Scope**: Overlaying Phase 6's pre-computed static-baseline run as a passive reference curve on Loom's live allocation chart, load-bearing legend disambiguation (`Loom (live)` vs `Static baseline (recorded run)`), and timeline alignment mechanics.

---

### 14.1 Motivation & Cognitive Rationale

During technical rehearsals and executive presentations, a critical communication gap emerged:
- While Loom's live allocation chart renders a beautifully smoothed PID easing curve ($\Delta w_{\text{max}} = 11.77\%$), observers without deep knowledge of legacy payment routing lack an immediate visual benchmark of what standard static circuit breakers actually do.
- In Phase 6, the `StaticBaselineRouter` demonstrated that static thresholds suffer from an instantaneous $100.0\%$ Heaviside step drop (herd migration stampede), slamming volume off a failing gateway in 0 milliseconds and risking secondary gateway collapse.
- Confining this comparison to the collapsed `'Diagnostics ›'` audit card forced observers to cross-reference numbers mentally rather than experiencing the contrast visually.

Revision 6 closes this gap by **overlaying Phase 6's stored static-baseline run as a ghost reference curve directly on the hero chart**. Observers immediately see the two curves diverge from the same outage point: the dashed gray baseline plunges vertically off a cliff, while Loom's line eases smoothly downward.

---

### 14.2 Pre-Computed Reference Dataset Specification

The overlay is **static and pre-computed**—it is **never calculated in real-time, never re-routed live, and adds zero runtime backend compute**:

#### 14.2.1 Sourcing from Phase 6 Empirical Benchmark
In Phase 6, the static baseline was evaluated against the identical 150-transaction outage gauntlet (Seed 42, Alpha base rate 95%, Outage at Tx 51–100, Recovery at Tx 101–150, $M=3$ failure threshold, $N=30$ cooldown with canary probe).

The resulting traffic allocation for the outage-targeted leader (Acquirer Alpha) is codified as the canonical reference series:

$$\Delta k = k - k_{\text{outage\_trigger}}$$

$$w_{\text{Alpha}}^{\text{baseline}}(\Delta k) = \begin{cases}
1.00 & \text{if } \Delta k < 0 \quad \text{(Warmup: 100\% volume to Priority 1)} \\
1.00 & \text{if } 0 \le \Delta k < 3 \quad \text{(Absorbing } M=3 \text{ failures before circuit breaker trips)} \\
0.00 & \text{if } 3 \le \Delta k < 63 \quad \text{(Circuit breaker TRIPPED; 100\% volume dumped to secondary Beta)} \\
1.00 & \text{if } \Delta k \ge 64 \quad \text{(Canary probe succeeds; instantaneous 100\% snapback to Alpha)}
\end{cases}$$

#### 14.2.2 Static Client Encapsulation
- The reference profile is encapsulated directly in the frontend (`AllocationChart.jsx`) as a constant normalized step function.
- It requires **zero backend API endpoints, zero database queries, and zero WebSocket traffic**.

---

### 14.3 Visual Presentation & Styling Contract

```
+---------------------------------------------------------------------------------------------------------+
| ALLOCATION CHART WITH STATIC-BASELINE REFERENCE OVERLAY                                                 |
+---------------------------------------------------------------------------------------------------------+
| 100% |-----------                                                                                       |
|      |           \      [!] Outage Injected                                                             |
|      |   Loom     \      |                                                                              |
|  50% |   (#5B8DEF) \     |                                                                              |
|      |              \    |                                                                              |
|   0% +---------------\---+----------------------------------------------------------------------------+ |
|      | Static Baseline:  |                                                                              |
|      | (--- #8B8F98 ---) +----------------------------------------------------------------------------+ |
|      -60s                                       -20s                                                Now |
+---------------------------------------------------------------------------------------------------------+
```

1. **Stroke Style**:
   - Dashed stroke pattern: `stroke-dasharray="4 4"` (or `strokeDasharray: "4 4"`).
   - Line width: Hairline `1.5px` (or `1.0px`).
2. **Color Token**:
   - Secondary Text Gray (`#8B8F98`).
   - Opacity: Subtle ghost rendering with `strokeOpacity="0.55"` (or `0.50`).
   - **Strict Negative Invariant**: The baseline reference curve must **NEVER** use an accent color (`#5B8DEF` cobalt blue, `#C084FC` violet) and must **NEVER** use Alert Crimson (`#E5484D`). It is an archival reference, not an active or failing participant.
3. **Z-Order Layering (Painter's Algorithm Invariant)**:
   - The reference curve must be rendered **strictly behind Loom's live curves**:
     $$\text{Canvas Background} \to \text{Gridlines} \to \mathbf{Static\ Baseline\ Reference\ Line} \to \text{Live Gamma Line} \to \text{Live Beta Line} \to \text{Live Alpha Line} \to \text{Outage Markers} \to \text{Head Dots}$$
   - *Architectural Rationale*: Sinking the dashed gray line to the background ensures it provides contextual contrast without competing with or visually fracturing the live telemetry signals.

---

### 14.4 Load-Bearing Disambiguation Legend

#### 14.4.1 Legend Layout & Structure
A dedicated, compact legend block is integrated into the chart header:
- **Loom Live Series**:
  - Indicator: Solid Accent bar (`w-3 h-0.5 bg-[#5B8DEF]`).
  - Label: `'Loom (live)'` in `Inter`, 11px, font-medium, color `#E4E6EB`.
- **Static Baseline Series**:
  - Indicator: Dashed Gray bar (`w-3 h-0.5 border-b border-dashed border-[#8B8F98]`).
  - Label: `'Static baseline (recorded run)'` in `Inter`, 11px, font-normal, color `#8B8F98`.

#### 14.4.2 Load-Bearing Status
- **Strict Invariant**: This legend is **architecturally load-bearing, not decorative copy**.
- *Failure Mode Without Legend*: In payment systems, any line plotted on a real-time telemetry canvas is assumed to represent an active concurrent signal (e.g. shadow-routing, a second live router instance, or a fourth gateway arm).
- Without explicit disambiguation, reviewers would naturally ask: *"Is Loom running dual-routing live in production?"*
- The explicit `'Loom (live)'` vs `'Static baseline (recorded run)'` label establishes clear epistemological boundaries: Loom is actively controlling real-time traffic; the baseline is an archival reference run under the identical outage stimulus.

---

### 14.5 Timeline Synchronization Mechanics (X-Axis Alignment)

How a recorded, discrete 150-transaction run is mapped onto a continuous, rolling 60-second x-axis:

#### 14.5.1 Dynamic Outage Anchor Point
1. **Live Trigger Capture**:
   - When an operator triggers an outage on Acquirer Alpha (via text button or scenario preset), the system records the live trigger event index $i_{\text{outage}}$ and timestamp $t_{\text{outage}}$.
   - An Outage Event Marker is pinned at horizontal coordinate $X_{\text{outage}}$.
2. **Relative Index Mapping**:
   - Let $N$ be the number of events in the live chart ring buffer ($N \le 200$).
   - For each live event $i \in [0, N-1]$ on the canvas:
     $$\Delta i = i - i_{\text{outage}}$$
3. **State Evaluation Along Canvas**:
   - **Case A: No Outage Present in View Window** ($i_{\text{outage}} = -1$):
     $$w_{\text{Alpha}}^{\text{baseline}}(i) = 1.00 \quad (\forall i)$$
     *(Renders as a clean, resting 100% dashed reference line, showing the static baseline's normal priority allocation).*
   - **Case B: Outage Present in View Window** ($i_{\text{outage}} \ge 0$):
     - For pre-outage events ($i < i_{\text{outage}}$): $w_{\text{Alpha}}^{\text{baseline}}(i) = 1.00$.
     - For $M=3$ events immediately following outage trigger ($i_{\text{outage}} \le i < i_{\text{outage}} + 3$): $w_{\text{Alpha}}^{\text{baseline}}(i) = 1.00$ (absorbing consecutive failures).
     - At $i = i_{\text{outage}} + 3$: Allocation drops vertically down to $0.00$ at a sharp 90° angle.
     - While outage persists: Allocation remains flat at $0.00$.
     - When recovery occurs + canary probe succeeds: Allocation jumps vertically back to $1.00$.

#### 14.5.2 Visual Synergy on Screen
- Both Loom's live line and the static baseline line encounter the failure at the exact same horizontal coordinate $X_{\text{outage}}$.
- The static line drops like a stone at $X_{\text{outage}} + \Delta X_{\text{trip}}$ (0ms Heaviside drop).
- Loom's line begins its gentle exponential PID easing curve at $X_{\text{outage}}$ ($\Delta w_{\text{max}} = 11.77\%$).
- The 8.5x stability multiplier is visibly self-evident on a single coordinate plane.

---

### 14.6 Frontend Implementation & Handoff Checklist

#### 1. AllocationChart Component Refactoring (`dashboard/src/components/AllocationChart.jsx`)
- [ ] Define `pathStaticBaseline` in `useMemo` based on `events` and `outageMarkers`:
  - Locate `outageIdx` from `outageMarkers`.
  - Construct SVG path using $M$ and $L$ commands adhering to the step profile: $1.0 \to \text{drop at } i_{\text{outage}} + 3 \to 0.0$.
  - When no outage is present, generate a straight horizontal dashed path at $Y = 1.0$.
- [ ] Render static baseline path in SVG:
  - Place `<path>` element **before** `pathGamma`, `pathBeta`, and `pathAlpha` in the DOM tree (ensuring correct z-order layering).
  - Apply attributes: `stroke="#8B8F98"`, `strokeWidth="1.5"`, `strokeDasharray="4 4"`, `strokeOpacity="0.55"`, `fill="none"`.
- [ ] Add the load-bearing legend in the chart header:
  - Add `'Loom (live)'` solid bar indicator (`#5B8DEF`).
  - Add `'Static baseline (recorded run)'` dashed bar indicator (`#8B8F98`).

#### 2. Verification & Validation
- [ ] Verify `npm run build` succeeds cleanly.
- [ ] Verify `pytest tests/dashboard/` passes without regressions.
- [ ] Verify in browser: Before outage, dashed gray line rests at 100%. Upon triggering outage on Alpha, dashed gray line drops vertically at the outage marker, while Loom's blue/red line eases downward smoothly.
