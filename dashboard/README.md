# Loom Terminal (dashboard)

Live, Bloomberg-style terminal over Loom's routing telemetry: one screen of labelled panes,
keyboard-driven, fed by the router's WebSocket. The original Phase 7 contract is in
[`docs/phase7-dashboard-spec.md`](../docs/phase7-dashboard-spec.md); this README describes
what the code does today.

## Run

```bash
python scripts/run_demo.py --tps 15   # simulator :8001, router :8000, synthetic traffic
cd dashboard && npm install && npm run dev   # http://localhost:5173
```

`npm test` runs the Vitest suite; `npm run build` builds to `dist/`.

## Design principles

- **Calm until something breaks** (ISA-101 high-performance HMI). The screen is cream on warm
  black; amber marks focus and Loom's own numbers; red, motion and blinking are kept for
  abnormal states (outage, health < 0.70, dropped frames, lost connection).
- **Density is value per unit of space.** Overview first (status bar, key figures), then
  allocation and acquirer detail, then the event tape, benchmark and controls.
- **Panels, not cards.** Every pane has a hairline rule, an amber function-key tag and an
  uppercase title. No radius, no shadows.
- **Honest data** (AUDIT F-27). Every figure comes from a received frame or a recorded run.
  Missing values read `no data` or `--`; dropped frames and restarts break the chart lines
  and appear in the tape.
- **Subtle CRT.** Faint scanlines and phosphor glow on the hero readout and live lines;
  toggle with `C` or the status-bar button; off under `prefers-reduced-motion`.

## Tokens (`src/index.css`, `tailwind.config.js`, `src/theme.js`)

| Token | Hex | Use |
| :--- | :--- | :--- |
| ground / well | `#0A0A09` | page and chart well |
| pane | `#111110` | pane background |
| rule | `#2B2924` | borders and gridlines |
| text | `#E8E2D0` | primary text |
| dim | `#8A8473` | labels, secondary text |
| amber | `#FFA028` | accent, Alpha, Loom's figures |
| cyan | `#5CC8E0` | Beta |
| cream | `#D8D2BE` | Gamma |
| alert | `#FF4A3D` | abnormal states only; overrides an acquirer's color in outage or below 0.70 health |
| ok | `#4FD18B` | healthy lamp, authorized frames, significant benchmark wins |

Type: IBM Plex Mono (tabular, slashed zero) for everything; IBM Plex Sans Condensed for the
wordmark and pane titles.

## Panes

| Key | Pane | Component |
| :--- | :--- | :--- |
| — | Status bar: link state, stream id, last seq, dropped, restarts, CRT toggle, UTC clock | `HeaderBar.jsx` |
| F1 | Key figures: rolling PSR (last 50), lifetime PSR, routed, declined, errors, peak step Δ | `MetricReadouts.jsx` |
| F2 | Live traffic allocation chart with outage markers and recorded static baseline | `AllocationChart.jsx` |
| F3 | Acquirers: health, E[SR], α/β, latest Thompson draw, PID target, weight, outage toggle | `AcquirerTable.jsx` |
| F4 | Event tape: received routing frames, newest first | `EventTape.jsx` |
| F5 | Benchmark vs static baseline (from `src/data/baselineComparison.json`) | `BaselineComparisonCard.jsx` |
| F6 | Simulation presets and reset | `OperatorControls.jsx` |

All panes share `Pane.jsx`.

## Keyboard

`F1`–`F6` or `1`–`6` focus a pane · `Shift+1/2/3` run the standard cliff, sensitive blip and
gray failure presets · `R` resets all routes (asks first) · `C` toggles the CRT effect.

## Data

- `src/hooks/useLoomTelemetry.js`: native WebSocket to `/ws/telemetry` (direct to
  `127.0.0.1:8000` under the dev server), 15 s ping, reconnect backoff 0.5 s × 1.5 up to
  10 s, stale after 5 s without a frame.
- `src/hooks/telemetryState.js`: pure reducer. Keeps the last 120 frames, a 50-frame PSR
  window, per-acquirer state (bootstrap snapshot, `updated_state`, latest Thompson sample,
  PID target, smoothed weight), outage/recovery/restart markers, and sequence-gap accounting.
- `src/hooks/useSimulatorControls.js`: outage toggles, success-rate commits and presets,
  proxied through the router to the simulator.
