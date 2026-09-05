# Loom Live Mission-Control Dashboard (Phase 7)

Real-time telemetry and control cockpit for Loom's dynamic payment routing engine.

---

## 1. Specification & Architectural Contract

The complete, binding architecture contract for this module is defined in:
**[`docs/phase7-dashboard-spec.md`](../docs/phase7-dashboard-spec.md)**

All component development, styling, and networking MUST conform to the decisions and interfaces recorded in:
- `docs/CONSTITUTION.md` (React, native WebSocket, component naming conventions)
- `docs/decisions-log.md` (Phase 7 Decisions, Interface Contracts, and Open Risks)
- `docs/schemas/routing_event.json` & `docs/schemas/health_alert_event.json` (Phase 5 Pub/Sub telemetry contracts)

---

## 2. Visual Direction & Design Tokens (Phase 7 Revision 3)

Per Section 11 of `docs/phase7-dashboard-spec.md` and Decisions Log (`[Phase 7 Revision 3]`):
- **Ground**: Base Canvas Ground (`#0F1115`)
- **Panels**: Cockpit Slate Panel (`#16181D`)
- **Data Wells**: Inset Well (`#0F1115`)
- **Hairline Borders**: `1px solid #2A2D34`
- **Text Primary**: `#E4E6EB`
- **Text Secondary**: `#8B8F98`
- **Deterministic Acquirer Color Mapping**:
  - **Acquirer 1 (Alpha)**: Accent (`#5B8DEF`) — 2.0px chart stroke, status dot/badge, EWMA bar fill
  - **Acquirer 2 (Beta)**: Accent 2 (`#C084FC`) — 1.5px chart stroke, status dot/badge, EWMA bar fill
  - **Acquirer 3 (Gamma)**: Secondary text gray (`#7C808A`) — 1.0px chart stroke, status dot/badge, EWMA bar fill
- **Inviolable Alert Override**: Alert (`#E5484D`) overrides any acquirer's assigned color across all surfaces the instant that acquirer enters an outage or degraded state ($H < 0.70$), reverting immediately upon recovery ($H \ge 0.70$).
- **Semantic Quarantine**: Alert (`#E5484D`) is strictly an emergency signal; Accent 2 (`#C084FC`) is strictly reserved for Acquirer Beta identity.
- **Typography**: IBM Plex Sans for all interface text, labels, and headings in strict sentence case; IBM Plex Mono (with `tnum` tabular alignment) strictly reserved for live numeric telemetry (PSR%, health scores, RTT, sequence numbers).
- **Geometry**: Sharp corners (`border-radius: 0px` or max `2px`); **zero rounded-card-kit drop shadows** (`box-shadow: none`).

---

## 3. Implementation Structure & Information Architecture

1. **Top Header (`HeaderBar.jsx`)**
   - Brand title and system metadata in clean sentence case.
   - Live transaction counter, router latency, and UTC telemetry clock.
   - **Consolidated Single Status Element**: Persistent indicator transitioning dynamically across `Live` (accent `#5B8DEF`), `Reconnecting (n)` (pulsing alert `#E5484D`), and `Connection lost` (alert `#E5484D` with inline Reconnect button).

2. **Ticket A: Live Allocation Chart (`AllocationChart.jsx`)**
   - Hero centerpiece: rolling 60s time series of smoothed multi-acquirer traffic allocation ($w_i \in [0.0, 1.0]$).
   - Demonstrates Phase 4 PID dampening curve vs static cliff jumps.
   - Pinned vertical hairline alert marker in `#E5484D` on outage injection (`ACQUIRER_OUTAGE`).
   - Peak step delta readout ($\Delta w_{\text{max}} = 11.77\%$ vs $100.0\%$).

3. **Ticket B: Cluster Readouts & Colocated Acquirer Controls (`MetricReadouts.jsx`)**
   - Headline lifetime & rolling 50-tx PSR (accent `#5B8DEF` $\to$ alert `#E5484D` when $<80\%$).
   - Sensor-Actuator Colocation: Each acquirer's outage control (Trigger/Clear outage button, failure mode selector, and brownout slider) is physically colocated adjacent to that acquirer's own EWMA health readout and Beta belief parameters.

4. **Progressive Disclosure: Diagnostics (`BaselineComparisonCard.jsx`)**
   - Collapsed behind a default-closed `<details>` disclosure labeled `"Diagnostics"`.
   - Displays Phase 6 empirical comparison audit ($M=1$ overreaction, $M=3$ standard cliff, gray failure).

5. **Progressive Disclosure: Simulation Controls (`OperatorControls.jsx`)**
   - Collapsed behind a default-closed `<details>` disclosure labeled `"Simulation controls"`.
   - Benchmark scenario gauntlet presets (Standard Cliff, Sensitive Blip $M=1$, Gray Failure $p=0.60$, and Reset All).

6. **Ticket D: WebSocket Gateway & Streaming Hook (`useLoomTelemetry.js`)**
   - Native browser WebSocket connection to `@app.websocket("/ws/telemetry")`.
   - Circular ring buffer ($N = 200$) with `requestAnimationFrame` 60 FPS animation loop.
   - Hybrid cold-start bootstrap (SQLite recent transactions + Redis live streaming).
   - Resilient auto-reconnect with exponential backoff.

---

## 4. Directory Structure (Vite + React)

```
dashboard/
├── index.html                   # HTML entry point with dark background and font links
├── package.json                 # Dependencies: react, react-dom, tailwindcss, etc.
├── vite.config.js               # Vite configuration with proxy to backend port 8000
├── src/
│   ├── main.jsx                 # Application mount
│   ├── App.jsx                  # Main Mission-Control layout grid
│   ├── index.css                # Pinned design tokens, CSS variables, typography
│   ├── components/
│   │   ├── HeaderBar.jsx        # Navigation, connection status badge, live clock
│   │   ├── AllocationChart.jsx  # Ticket A: Live allocation chart with outage marker
│   │   ├── MetricReadouts.jsx   # Ticket B: Global & rolling PSR, latency readouts
│   │   ├── BaselineComparisonCard.jsx # Ticket B: Phase 6 empirical comparison audit
│   │   ├── AcquirerHealthCard.jsx     # Ticket B: Per-acquirer EWMA and Beta belief cards
│   │   └── OperatorControls.jsx # Ticket C: Outage buttons, sliders, presets
│   └── hooks/
│       └── useLoomTelemetry.js  # Ticket D: WebSocket hook, ring buffer, requestAnimationFrame
└── README.md
```
