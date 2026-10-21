import React, { useMemo } from 'react';
import BASELINE_REFERENCE_RUN from '../data/baselineReferenceRun.json';
import { ACQUIRERS, ACQUIRER_IDS, COLORS, acquirerColor, isDegraded } from '../theme';

// Recorded frames drawn before the outage so the overlay starts from its pre-outage level
const BASELINE_LEAD_IN = 4;

/**
 * Static baseline overlay: the recorded run in dashboard/src/data/baselineReferenceRun.json
 * (written by `python scripts/compare_psr.py --out-reference-json ...`), aligned so its
 * outage starts at the latest Alpha outage marker in view. Each live frame after the
 * marker is paired with the recorded transaction at the same offset; frames past the end
 * of the recording get no point. Returns [] when there is no Alpha outage marker in view.
 */
export function baselineOverlay(events, markers, reference = BASELINE_REFERENCE_RUN) {
  if (!events.length) return [];
  const firstIndex = events[0].index;
  const lastIndex = events[events.length - 1].index;
  const outage = [...markers]
    .reverse()
    .find(
      (m) =>
        m.type === 'outage' &&
        m.acquirerId === 'acquirer_alpha' &&
        m.index !== null &&
        m.index >= firstIndex &&
        m.index <= lastIndex,
    );
  if (!outage) return [];
  const txs = reference.transactions;
  const points = [];
  events.forEach((ev, i) => {
    const offset = ev.index - outage.index;
    const recorded = txs[reference.outage_trigger_index + offset];
    if (offset >= -BASELINE_LEAD_IN && recorded) {
      points.push({ i, weight: recorded.alpha_weight });
    }
  });
  return points;
}

const GRID = [0, 0.25, 0.5, 0.75, 1];
// Draw order: Gamma at the back, Alpha (the usual leader) in front
const DRAW_ORDER = ['acquirer_gamma', 'acquirer_beta', 'acquirer_alpha'];
const STROKE = { acquirer_alpha: 2, acquirer_beta: 1.5, acquirer_gamma: 1.25 };

/**
 * Live Traffic Allocation Chart with outage markers (the body of the F2 pane).
 *
 * AUDIT F-27 honesty rules:
 * - Lines are drawn only from weights the router sent. A frame without a weight for an
 *   acquirer leaves a hole in that line; nothing is filled in.
 * - Dropped frames (skipped sequence numbers) and router restarts break every line and are
 *   marked on the chart.
 * - Markers are placed at the frame they belong to and disappear when it scrolls out of
 *   view; they are never moved to the left edge.
 * - The static baseline line replays a recorded run (see baselineOverlay).
 */
export function AllocationChart({
  events = [],
  outageMarkers = [],
  activeOutages = {},
  acquirerStates = {},
  connectionStatus = 'connected',
  isStale = false,
  stream = null,
}) {
  const width = 800;
  const height = 300;
  const padding = { top: 30, right: 52, bottom: 26, left: 40 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;

  const colorOf = (aid) => acquirerColor(aid, activeOutages, acquirerStates);

  const { paths, pathStaticBaseline, currentWeights, markerPositions, breaks } = useMemo(() => {
    const empty = {
      paths: {},
      pathStaticBaseline: '',
      currentWeights: {},
      markerPositions: [],
      breaks: [],
    };
    if (!events || events.length === 0) return empty;

    const n = events.length;
    const getX = (i) => padding.left + (n === 1 ? chartWidth / 2 : (i / (n - 1)) * chartWidth);
    const getY = (val) => padding.top + chartHeight - Math.max(0, Math.min(1, val)) * chartHeight;

    // One path per acquirer; a missing weight, a gap or a restart starts a new segment
    const paths = {};
    for (const aid of ACQUIRER_IDS) {
      const parts = [];
      let penDown = false;
      events.forEach((ev, i) => {
        const w = ev.weights ? ev.weights[aid] : undefined;
        if (ev.missingBefore > 0 || ev.restartBefore) penDown = false;
        if (typeof w !== 'number') {
          penDown = false;
          return;
        }
        parts.push(`${penDown ? 'L' : 'M'} ${getX(i).toFixed(1)} ${getY(w).toFixed(1)}`);
        penDown = true;
      });
      paths[aid] = parts.join(' ');
    }

    const breaks = [];
    events.forEach((ev, i) => {
      if (i > 0 && (ev.missingBefore > 0 || ev.restartBefore)) {
        breaks.push({
          key: `break-${ev.index}`,
          x: (getX(i - 1) + getX(i)) / 2,
          label: ev.restartBefore ? 'restart' : `${ev.missingBefore} dropped`,
        });
      }
    });

    const firstIndex = events[0].index;
    const byIndex = new Map(events.map((ev, i) => [ev.index, i]));
    const markerPositions = (outageMarkers || [])
      .filter((m) => m.index !== null && m.index >= firstIndex && byIndex.has(m.index))
      .map((m) => ({ ...m, x: getX(byIndex.get(m.index)) }));

    const overlay = baselineOverlay(events, outageMarkers || []);
    const pathStaticBaseline = overlay
      .map((p, k) => {
        const x = getX(p.i).toFixed(1);
        const y = getY(p.weight).toFixed(1);
        const prev = overlay[k - 1];
        if (!prev) return `M ${x} ${y}`;
        // Step shape: the recorded router switches between 0 and 1 at once
        return `L ${x} ${getY(prev.weight).toFixed(1)} L ${x} ${y}`;
      })
      .join(' ');

    const latest = events[n - 1].weights || {};
    const currentWeights = {};
    for (const aid of ACQUIRER_IDS) {
      if (typeof latest[aid] === 'number') currentWeights[aid] = latest[aid];
    }

    return { paths, pathStaticBaseline, currentWeights, markerPositions, breaks };
  }, [events, outageMarkers, chartWidth, chartHeight, padding.left, padding.top]);

  const yOf = (w) => padding.top + chartHeight - w * chartHeight;
  const weightText = (aid) =>
    typeof currentWeights[aid] === 'number' ? `${(currentWeights[aid] * 100).toFixed(1)}%` : 'no data';
  const droppedFrames = stream?.droppedFrames ?? 0;
  const restarts = stream?.restarts ?? 0;

  return (
    <div className="flex flex-col h-full">
      {/* Legend: live weights per acquirer, and which line is the recorded baseline */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-2 py-1.5 border-b border-rule text-[11px]">
        {ACQUIRERS.map((acq) => {
          const color = colorOf(acq.id);
          const bad = isDegraded(acq.id, activeOutages, acquirerStates);
          return (
            <span key={acq.id} className="flex items-center gap-1.5">
              <span className="inline-block w-3 h-0.5" style={{ backgroundColor: color }} />
              <span className="text-dim uppercase tracking-[0.08em]">{acq.ticker}</span>
              <span className="font-mono" style={{ color: typeof currentWeights[acq.id] === 'number' ? color : COLORS.dim }}>
                {weightText(acq.id)}
              </span>
              {bad && (
                <span className="text-[9px] px-1 bg-alert text-ground font-semibold uppercase">Outage</span>
              )}
            </span>
          );
        })}
        <span className="ml-auto flex items-center gap-3 text-dim">
          <span className="flex items-center gap-1.5">
            <span className="inline-block w-3 h-0.5 bg-amber" />
            Loom (live)
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block w-3 border-b border-dashed border-dim" />
            Static baseline (recorded run)
          </span>
        </span>
      </div>

      {/* Chart well */}
      <div className="relative flex-1 min-h-[260px] lg:min-h-[300px] flex items-center justify-center overflow-hidden bg-well">
        {connectionStatus !== 'connected' && (
          <div className="absolute top-2 right-2 z-20 px-2 py-0.5 bg-ground border border-alert text-alert text-[10px] uppercase tracking-[0.08em] flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 bg-alert inline-block lamp-blink" />
            <span>
              {connectionStatus === 'reconnecting'
                ? 'Connection lost — stream reconnecting'
                : 'Disconnected — telemetry paused'}
            </span>
          </div>
        )}

        {(droppedFrames > 0 || restarts > 0) && (
          <div
            data-testid="stream-gaps"
            className="absolute top-2 left-12 z-20 px-2 py-0.5 bg-ground border border-alert text-alert text-[10px] font-mono"
          >
            {droppedFrames > 0 && `${droppedFrames} frame${droppedFrames === 1 ? '' : 's'} dropped`}
            {droppedFrames > 0 && restarts > 0 && ' · '}
            {restarts > 0 && `router restarted${restarts > 1 ? ` ${restarts}×` : ''}`}
          </div>
        )}

        {connectionStatus === 'connected' && isStale && events.length > 0 && (
          <div className="absolute top-2 right-2 z-20 px-2 py-0.5 bg-ground border border-rule text-dim text-[10px] uppercase tracking-[0.08em]">
            Idle stream (awaiting new transactions)
          </div>
        )}

        {events.length === 0 ? (
          <div className="text-center px-4 py-8 flex flex-col items-center gap-2">
            <span className="font-mono text-amber text-sm">
              &gt; awaiting_telemetry<span className="lamp-blink">_</span>
            </span>
            <span className="text-xs font-semibold text-text uppercase tracking-[0.12em]">
              Awaiting telemetry stream
            </span>
            <p className="text-[11px] text-dim max-w-sm">
              No live transaction events received yet. Start the transaction generator or trigger
              a test transaction to see the live allocation.
            </p>
          </div>
        ) : (
          <svg viewBox={`0 0 ${width} ${height}`} className="absolute inset-0 w-full h-full" preserveAspectRatio="none">
            {/* Gridlines every 25% */}
            {GRID.map((g) => (
              <g key={g}>
                <line
                  x1={padding.left}
                  y1={yOf(g)}
                  x2={width - padding.right}
                  y2={yOf(g)}
                  stroke={COLORS.rule}
                  strokeWidth={g === 0 ? 1.5 : 1}
                  strokeDasharray={g === 0 || g === 1 ? undefined : '2 4'}
                  vectorEffect="non-scaling-stroke"
                />
                <text x={padding.left - 6} y={yOf(g) + 3} fill={COLORS.dim} fontSize="9" textAnchor="end" className="font-mono">
                  {Math.round(g * 100)}%
                </text>
              </g>
            ))}

            {/* Vertical outage, recovery and restart markers */}
            {markerPositions.map((marker) => {
              const isOutage = marker.type === 'outage';
              const markerColor =
                marker.type === 'restart' ? COLORS.dim : isOutage ? COLORS.alert : COLORS.ok;
              const tagX = Math.max(padding.left, Math.min(width - padding.right - 140, marker.x - 70));
              return (
                <g key={marker.id} data-testid={`marker-${marker.type}`}>
                  <line
                    x1={marker.x}
                    y1={padding.top}
                    x2={marker.x}
                    y2={padding.top + chartHeight}
                    stroke={markerColor}
                    strokeWidth="1"
                    strokeDasharray="3 3"
                    vectorEffect="non-scaling-stroke"
                  />
                  <rect x={tagX} y={padding.top - 22} width="140" height="15" fill={markerColor} />
                  <text
                    x={tagX + 70}
                    y={padding.top - 11}
                    fill={COLORS.ground}
                    fontSize="9"
                    fontWeight="600"
                    textAnchor="middle"
                    className="font-mono"
                  >
                    {marker.label || (isOutage ? 'Outage injected' : 'Recovery initiated')}
                  </text>
                </g>
              );
            })}

            {/* Dropped frames / router restarts: every line is broken here */}
            {breaks.map((b) => (
              <g key={b.key} data-testid="stream-break">
                <line
                  x1={b.x}
                  y1={padding.top}
                  x2={b.x}
                  y2={padding.top + chartHeight}
                  stroke={COLORS.dim}
                  strokeWidth="1"
                  strokeDasharray="1 3"
                  vectorEffect="non-scaling-stroke"
                />
                <text x={b.x + 3} y={padding.top + chartHeight - 4} fill={COLORS.dim} fontSize="8" className="font-mono">
                  {b.label}
                </text>
              </g>
            ))}

            {/* Static baseline (recorded run), behind the live lines */}
            {pathStaticBaseline && (
              <path
                d={pathStaticBaseline}
                fill="none"
                stroke={COLORS.dim}
                strokeWidth="1.25"
                strokeDasharray="4 4"
                strokeOpacity="0.8"
                vectorEffect="non-scaling-stroke"
              />
            )}

            {/* Live lines */}
            {DRAW_ORDER.map((aid) =>
              paths[aid] ? (
                <path
                  key={aid}
                  d={paths[aid]}
                  fill="none"
                  stroke={colorOf(aid)}
                  color={colorOf(aid)}
                  className="glow-line"
                  strokeWidth={isDegraded(aid, activeOutages, acquirerStates) ? 2 : STROKE[aid]}
                  strokeLinecap="square"
                  strokeLinejoin="miter"
                  vectorEffect="non-scaling-stroke"
                />
              ) : null,
            )}

            {/* Live head: square marker and last value, Bloomberg-style price tag */}
            {DRAW_ORDER.map((aid) => {
              const w = currentWeights[aid];
              if (typeof w !== 'number') return null;
              const y = yOf(w);
              const c = colorOf(aid);
              return (
                <g key={`head-${aid}`}>
                  <rect x={width - padding.right - 3} y={y - 3} width="6" height="6" fill={c} />
                  <rect x={width - padding.right + 4} y={y - 6} width={padding.right - 6} height="12" fill={c} />
                  <text x={width - padding.right + 6} y={y + 3} fill={COLORS.ground} fontSize="9" fontWeight="600" className="font-mono">
                    {(w * 100).toFixed(1)}
                  </text>
                </g>
              );
            })}

            {/* Time axis */}
            <text x={padding.left} y={height - 8} fill={COLORS.dim} fontSize="9" className="font-mono">
              {events.length} frames ago
            </text>
            <text x={width / 2} y={height - 8} fill={COLORS.dim} fontSize="9" textAnchor="middle" className="font-mono">
              One point per received frame
            </text>
            <text x={width - padding.right} y={height - 8} fill={COLORS.amber} fontSize="9" textAnchor="end" className="font-mono">
              Now
            </text>
          </svg>
        )}
      </div>
    </div>
  );
}

export default AllocationChart;
