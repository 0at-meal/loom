import React, { useMemo } from 'react';
import BASELINE_REFERENCE_RUN from '../data/baselineReferenceRun.json';

const ACQUIRER_IDS = ['acquirer_alpha', 'acquirer_beta', 'acquirer_gamma'];
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

/**
 * Live Traffic Allocation Chart with outage markers.
 *
 * Phase 7 Revision 6 visual contract (colors, alert override, legend), with the AUDIT F-27
 * honesty rules:
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
  peakStepDelta = null,
  stream = null,
}) {
  const width = 800;
  const height = 300;
  const padding = { top: 35, right: 30, bottom: 35, left: 45 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;

  // Deterministic acquirer color assignment & inviolable alert override (Phase 7 Revision 3):
  // Alpha #5B8DEF, Beta #C084FC, Gamma #7C808A; Alert #E5484D when in outage or H < 0.70.
  const isDegraded = (aid) =>
    !!activeOutages[aid] ||
    (typeof acquirerStates[aid]?.health_score === 'number' &&
      acquirerStates[aid].health_score < 0.7);
  const isAlphaDegraded = isDegraded('acquirer_alpha');
  const isBetaDegraded = isDegraded('acquirer_beta');
  const isGammaDegraded = isDegraded('acquirer_gamma');

  const alphaColor = isAlphaDegraded ? '#E5484D' : '#5B8DEF';
  const betaColor = isBetaDegraded ? '#E5484D' : '#C084FC';
  const gammaColor = isGammaDegraded ? '#E5484D' : '#7C808A';

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

  const pathAlpha = paths.acquirer_alpha;
  const pathBeta = paths.acquirer_beta;
  const pathGamma = paths.acquirer_gamma;
  const weightText = (aid) =>
    typeof currentWeights[aid] === 'number' ? `${(currentWeights[aid] * 100).toFixed(1)}%` : 'no data';
  const droppedFrames = stream?.droppedFrames ?? 0;
  const restarts = stream?.restarts ?? 0;

  return (
    <div className="mission-panel p-4 flex flex-col gap-3 relative font-sans">
      {/* Panel Header */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#2A2D34] pb-3">
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-xs font-semibold text-[#E4E6EB]">
            Live traffic allocation
          </span>

          {/* Load-Bearing System Disambiguation Legend (Phase 7 Revision 6) */}
          <div className="flex items-center gap-3 pl-2.5 sm:border-l sm:border-[#2A2D34] text-[11px]">
            <div className="flex items-center gap-1.5">
              <span className="inline-block w-3 h-0.5 bg-[#5B8DEF]" />
              <span className="text-[#E4E6EB] font-medium">Loom (live)</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="inline-block w-3 h-0.5 border-b-2 border-dashed border-[#8B8F98]" />
              <span className="text-[#8B8F98]">Static baseline (recorded run)</span>
            </div>
          </div>
        </div>

        {/* Legend and Metrics Readout */}
        <div className="flex items-center gap-4 text-xs">
          {/* Acquirer Alpha Indicator */}
          <div className="flex items-center gap-1.5">
            <span
              className="inline-block w-2.5 h-0.5"
              style={{ backgroundColor: alphaColor }}
            />
            <span className="text-[#8B8F98]">Alpha:</span>
            <span
              className="font-mono text-[11px]"
              style={{ color: alphaColor }}
            >
              {weightText('acquirer_alpha')}
            </span>
            {isAlphaDegraded && (
              <span className="text-[9px] px-1 py-0.2 bg-[#E5484D]/15 text-[#E5484D] border border-[#E5484D]/40 font-mono">
                Outage
              </span>
            )}
          </div>

          {/* Acquirer Beta Indicator */}
          <div className="flex items-center gap-1.5">
            <span
              className="inline-block w-2.5 h-0.5"
              style={{ backgroundColor: betaColor }}
            />
            <span className="text-[#8B8F98]">Beta:</span>
            <span
              className="font-mono text-[11px]"
              style={{ color: betaColor }}
            >
              {weightText('acquirer_beta')}
            </span>
            {isBetaDegraded && (
              <span className="text-[9px] px-1 py-0.2 bg-[#E5484D]/15 text-[#E5484D] border border-[#E5484D]/40 font-mono">
                Outage
              </span>
            )}
          </div>

          {/* Acquirer Gamma Indicator */}
          <div className="flex items-center gap-1.5">
            <span
              className="inline-block w-2.5 h-0.5"
              style={{ backgroundColor: gammaColor }}
            />
            <span className="text-[#8B8F98]">Gamma:</span>
            <span
              className="font-mono text-[11px]"
              style={{ color: gammaColor }}
            >
              {weightText('acquirer_gamma')}
            </span>
            {isGammaDegraded && (
              <span className="text-[9px] px-1 py-0.2 bg-[#E5484D]/15 text-[#E5484D] border border-[#E5484D]/40 font-mono">
                Outage
              </span>
            )}
          </div>

          {/* Peak Jump Delta */}
          <div className="pl-3 border-l border-[#2A2D34] hidden md:flex items-center gap-1.5">
            <span className="text-[10px] text-[#8B8F98]">Peak step delta:</span>
            <span className="font-mono text-[11px] text-[#5B8DEF] font-medium">
              {typeof peakStepDelta === 'number' ? `${peakStepDelta.toFixed(1)}%` : 'no data'}
            </span>
            <span className="text-[10px] text-[#8B8F98]">(vs 100% cliff)</span>
          </div>
        </div>
      </div>

      {/* Main Chart Canvas / Well */}
      <div className="mission-well w-full relative h-[300px] flex items-center justify-center overflow-hidden bg-[#0F1115]">
        {/* Disconnected / Stale honest status banner */}
        {connectionStatus !== 'connected' && (
          <div className="absolute top-2 right-3 z-20 px-2 py-1 bg-[#16181D] border border-[#E5484D] text-[#E5484D] text-[10px] font-mono flex items-center gap-1.5 shadow-none">
            <span className="w-1.5 h-1.5 bg-[#E5484D] inline-block" />
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
            className="absolute top-2 left-14 z-20 px-2 py-1 bg-[#16181D] border border-[#E5484D] text-[#E5484D] text-[10px] font-mono"
          >
            {droppedFrames > 0 && `${droppedFrames} frame${droppedFrames === 1 ? '' : 's'} dropped`}
            {droppedFrames > 0 && restarts > 0 && ' · '}
            {restarts > 0 && `router restarted${restarts > 1 ? ` ${restarts}×` : ''}`}
          </div>
        )}

        {connectionStatus === 'connected' && isStale && events.length > 0 && (
          <div className="absolute top-2 right-3 z-20 px-2 py-1 bg-[#16181D] border border-[#2A2D34] text-[#8B8F98] text-[10px] font-mono flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 bg-[#5B8DEF] inline-block" />
            <span>Idle stream (awaiting new transactions)</span>
          </div>
        )}

        {/* Empty State */}
        {events.length === 0 ? (
          <div className="text-center px-4 py-8 flex flex-col items-center gap-2">
            <div className="w-6 h-6 border border-[#2A2D34] flex items-center justify-center text-[#5B8DEF] font-mono text-xs mb-1">
              [ ]
            </div>
            <span className="text-xs font-semibold text-[#E4E6EB]">
              Awaiting telemetry stream
            </span>
            <p className="text-[11px] text-[#8B8F98] max-w-sm">
              No live transaction events received from Phase 5 yet. Start the transaction generator
              or trigger a test transaction to see the live allocation.
            </p>
          </div>
        ) : (
          /* SVG Real-Time Chart */
          <svg
            viewBox={`0 0 ${width} ${height}`}
            className="w-full h-full"
            preserveAspectRatio="none"
          >
            {/* Subtle Baseline at 0%, 50%, and 100% */}
            {/* 100% baseline */}
            <line
              x1={padding.left}
              y1={padding.top}
              x2={width - padding.right}
              y2={padding.top}
              stroke="#2A2D34"
              strokeWidth="1"
              strokeDasharray="2 2"
              opacity="0.5"
            />
            {/* 50% midpoint guideline */}
            <line
              x1={padding.left}
              y1={padding.top + chartHeight / 2}
              x2={width - padding.right}
              y2={padding.top + chartHeight / 2}
              stroke="#2A2D34"
              strokeWidth="1"
              strokeDasharray="4 4"
              opacity="0.3"
            />
            {/* 0% subtle baseline */}
            <line
              x1={padding.left}
              y1={padding.top + chartHeight}
              x2={width - padding.right}
              y2={padding.top + chartHeight}
              stroke="#2A2D34"
              strokeWidth="1.5"
            />

            {/* Y-Axis tick labels (monospace, compact) */}
            <text
              x={padding.left - 8}
              y={padding.top + 4}
              fill="#8B8F98"
              fontSize="9"
              textAnchor="end"
              className="font-mono"
            >
              100%
            </text>
            <text
              x={padding.left - 8}
              y={padding.top + chartHeight / 2 + 3}
              fill="#8B8F98"
              fontSize="9"
              textAnchor="end"
              className="font-mono"
            >
              50%
            </text>
            <text
              x={padding.left - 8}
              y={padding.top + chartHeight + 3}
              fill="#8B8F98"
              fontSize="9"
              textAnchor="end"
              className="font-mono"
            >
              0%
            </text>

            {/* Vertical Outage & Recovery Markers */}
            {markerPositions.map((marker) => {
              const isOutage = marker.type === 'outage';
              const markerColor =
                marker.type === 'restart' ? '#8B8F98' : isOutage ? '#E5484D' : '#5B8DEF';

              return (
                <g key={marker.id} data-testid={`marker-${marker.type}`}>
                  {/* Vertical hairline marker line */}
                  <line
                    x1={marker.x}
                    y1={padding.top}
                    x2={marker.x}
                    y2={padding.top + chartHeight}
                    stroke={markerColor}
                    strokeWidth="1.5"
                    strokeDasharray="4 3"
                  />
                  {/* Marker Tag Badge at top */}
                  <rect
                    x={Math.max(padding.left, Math.min(width - padding.right - 140, marker.x - 70))}
                    y={padding.top - 18}
                    width="140"
                    height="16"
                    fill="#16181D"
                    stroke={markerColor}
                    strokeWidth="1"
                  />
                  <text
                    x={Math.max(padding.left, Math.min(width - padding.right - 140, marker.x - 70)) + 70}
                    y={padding.top - 7}
                    fill={markerColor}
                    fontSize="8.5"
                    fontWeight="500"
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
                  stroke="#8B8F98"
                  strokeWidth="1"
                  strokeDasharray="1 3"
                />
                <text
                  x={b.x + 3}
                  y={padding.top + chartHeight - 4}
                  fill="#8B8F98"
                  fontSize="8"
                  className="font-mono"
                >
                  {b.label}
                </text>
              </g>
            ))}

            {/* Static Baseline Reference Line (Phase 6 Recorded Run) */}
            {/* Rendered strictly behind Loom's live lines in z-order so it never competes with them */}
            {pathStaticBaseline && (
              <path
                d={pathStaticBaseline}
                fill="none"
                stroke="#8B8F98"
                strokeWidth="1.5"
                strokeDasharray="4 4"
                strokeOpacity="0.55"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            )}

            {/* Line Series 1: Gamma (Tertiary floor - #7C808A or Alert #E5484D) */}
            {pathGamma && (
              <path
                d={pathGamma}
                fill="none"
                stroke={gammaColor}
                strokeWidth={isGammaDegraded ? '2' : '1'}
                strokeOpacity={isGammaDegraded ? '1' : '0.75'}
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            )}

            {/* Line Series 2: Beta (Secondary backup - #C084FC or Alert #E5484D) */}
            {pathBeta && (
              <path
                d={pathBeta}
                fill="none"
                stroke={betaColor}
                strokeWidth={isBetaDegraded ? '2' : '1.5'}
                strokeOpacity="1"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            )}

            {/* Line Series 3: Alpha (Primary leader - #5B8DEF or Alert #E5484D) */}
            {pathAlpha && (
              <path
                d={pathAlpha}
                fill="none"
                stroke={alphaColor}
                strokeWidth="2"
                strokeOpacity="1"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            )}

            {/* Live Head Indicators (end dots) */}
            {events.length > 0 && (
              <>
                {typeof currentWeights.acquirer_alpha === 'number' && (
                  <circle
                    cx={width - padding.right}
                    cy={padding.top + chartHeight - currentWeights.acquirer_alpha * chartHeight}
                    r="3.5"
                    fill={alphaColor}
                  />
                )}
                {typeof currentWeights.acquirer_beta === 'number' && (
                  <circle
                    cx={width - padding.right}
                    cy={padding.top + chartHeight - currentWeights.acquirer_beta * chartHeight}
                    r="2.5"
                    fill={betaColor}
                  />
                )}
                {typeof currentWeights.acquirer_gamma === 'number' && (
                  <circle
                    cx={width - padding.right}
                    cy={padding.top + chartHeight - currentWeights.acquirer_gamma * chartHeight}
                    r="2"
                    fill={gammaColor}
                    fillOpacity={isGammaDegraded ? '1' : '0.75'}
                  />
                )}
              </>
            )}

            {/* Time Axis Markers */}
            <text
              x={padding.left}
              y={height - 10}
              fill="#8B8F98"
              fontSize="9"
              className="font-mono"
            >
              {events.length} frames ago
            </text>
            <text
              x={width / 2}
              y={height - 10}
              fill="#8B8F98"
              fontSize="9"
              textAnchor="middle"
              className="font-sans"
            >
              One point per received frame
            </text>
            <text
              x={width - padding.right}
              y={height - 10}
              fill="#8B8F98"
              fontSize="9"
              textAnchor="end"
              className="font-mono"
            >
              Now
            </text>
          </svg>
        )}
      </div>
    </div>
  );
}

export default AllocationChart;
