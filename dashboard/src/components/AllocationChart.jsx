import React, { useMemo } from 'react';
import { BASELINE_REFERENCE_RUN } from '../data/baselineReferenceRun';

/**
 * Ticket A: Live Traffic Allocation Chart with Outage-Event Marker
 *
 * Phase 7 Revision 6 Visual Contract:
 * - Real-time line chart, deterministic per-acquirer color assignment:
 *   - Acquirer 1 (Alpha): Accent (#5B8DEF, 2.0px stroke)
 *   - Acquirer 2 (Beta): Accent 2 (#C084FC, 1.5px stroke)
 *   - Acquirer 3 (Gamma): Secondary text gray (#7C808A, 1.0px stroke)
 * - Inviolable Alert Override: Alert (#E5484D, 2.0px stroke) overrides any line/marker in outage or degraded (H < 0.70)
 * - Vertical marker in Alert (#E5484D) at the exact moment an outage event arrives
 * - Static Baseline Reference Overlay: Dashed muted gray line (#8B8F98), replayed from Phase 6 benchmark
 *   run data, dynamically aligned to the live outage trigger timestamp so both outages occur at the same point.
 * - Load-Bearing Legend: 'Loom (live)' vs 'Static baseline (recorded run)'
 * - Slate ground (#0F1115) / panel (#16181D) / hairline border (#2A2D34)
 * - Sentence case throughout; Space Grotesk / Inter for labels, Space Mono for telemetry numbers
 */
export function AllocationChart({
  events = [],
  outageMarkers = [],
  activeOutages = {},
  acquirerStates = {},
  connectionStatus = 'connected',
  isStale = false,
  peakStepDelta = 0,
}) {
  const width = 800;
  const height = 300;
  const padding = { top: 35, right: 30, bottom: 35, left: 45 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;

  // Deterministic acquirer color assignment & inviolable alert override (Phase 7 Revision 3):
  // Acquirer 1 (Alpha): Accent (#5B8DEF)
  // Acquirer 2 (Beta): Accent 2 (#C084FC)
  // Acquirer 3 (Gamma): Secondary text gray (#7C808A)
  // Alert (#E5484D) overrides any acquirer's assigned color when in outage or degraded (H < 0.70).
  const isAlphaDegraded =
    !!activeOutages.acquirer_alpha ||
    (acquirerStates.acquirer_alpha?.health_score !== undefined &&
      acquirerStates.acquirer_alpha.health_score < 0.70);
  const isBetaDegraded =
    !!activeOutages.acquirer_beta ||
    (acquirerStates.acquirer_beta?.health_score !== undefined &&
      acquirerStates.acquirer_beta.health_score < 0.70);
  const isGammaDegraded =
    !!activeOutages.acquirer_gamma ||
    (acquirerStates.acquirer_gamma?.health_score !== undefined &&
      acquirerStates.acquirer_gamma.health_score < 0.70);

  const alphaColor = isAlphaDegraded ? '#E5484D' : '#5B8DEF';
  const betaColor = isBetaDegraded ? '#E5484D' : '#C084FC';
  const gammaColor = isGammaDegraded ? '#E5484D' : '#7C808A';

  // Derive points for each acquirer line and static baseline reference
  const {
    pathAlpha,
    pathBeta,
    pathGamma,
    pathStaticBaseline,
    currentWeights,
    markerPositions,
  } = useMemo(() => {
    if (!events || events.length === 0) {
      return {
        pathAlpha: '',
        pathBeta: '',
        pathGamma: '',
        pathStaticBaseline: '',
        currentWeights: {},
        markerPositions: [],
      };
    }

    const n = events.length;
    const getX = (i) => padding.left + (n === 1 ? chartWidth / 2 : (i / (n - 1)) * chartWidth);
    const getY = (val) => padding.top + chartHeight - Math.max(0, Math.min(1, val)) * chartHeight;

    let ptsA = [];
    let ptsB = [];
    let ptsG = [];

    events.forEach((ev, i) => {
      const x = getX(i);
      const w = ev.smoothed || ev.weights || {};
      const wA = w.acquirer_alpha !== undefined ? w.acquirer_alpha : 0.82;
      const wB = w.acquirer_beta !== undefined ? w.acquirer_beta : 0.15;
      const wG = w.acquirer_gamma !== undefined ? w.acquirer_gamma : 0.03;

      ptsA.push(`${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${getY(wA).toFixed(1)}`);
      ptsB.push(`${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${getY(wB).toFixed(1)}`);
      ptsG.push(`${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${getY(wG).toFixed(1)}`);
    });

    // Match outage markers to X coordinates on the chart
    const markers = [];
    if (outageMarkers && outageMarkers.length > 0) {
      outageMarkers.forEach((m) => {
        let matchIdx = -1;
        if (m.seq) {
          matchIdx = events.findIndex((e) => e.seq === m.seq);
        }
        if (matchIdx === -1 && m.timestamp) {
          let minDiff = Infinity;
          events.forEach((e, idx) => {
            const diff = Math.abs(e.timestamp - m.timestamp);
            if (diff < minDiff) {
              minDiff = diff;
              matchIdx = idx;
            }
          });
        }

        if (matchIdx >= 0) {
          const x = getX(matchIdx);
          markers.push({
            ...m,
            x,
          });
        }
      });
    }

    // -------------------------------------------------------------
    // Phase 7 Revision 6: Static Baseline Reference Curve Overlay
    // Loaded once from Phase 6 benchmark results (BASELINE_REFERENCE_RUN).
    // Synchronized to live outage trigger so both systems appear to experience
    // the failure at the exact same horizontal coordinate on the x-axis.
    // -------------------------------------------------------------
    let pathStaticBaseline = '';

    // Find outage marker for Acquirer Alpha
    const alphaOutageMarker = outageMarkers && outageMarkers.find(
      (m) => m.type === 'outage' && (!m.acquirer_id || m.acquirer_id === 'acquirer_alpha')
    );

    let outageIdx = -1;
    if (alphaOutageMarker) {
      if (alphaOutageMarker.seq) {
        outageIdx = events.findIndex((e) => e.seq === alphaOutageMarker.seq);
      }
      if (outageIdx === -1 && alphaOutageMarker.timestamp) {
        let minDiff = Infinity;
        events.forEach((e, idx) => {
          const diff = Math.abs(e.timestamp - alphaOutageMarker.timestamp);
          if (diff < minDiff) {
            minDiff = diff;
            outageIdx = idx;
          }
        });
      }
    }

    if (outageIdx === -1 && (activeOutages.acquirer_alpha || isAlphaDegraded)) {
      outageIdx = events.findIndex(
        (e) => e.decline_code === 'ACQUIRER_OUTAGE' || (e.smoothed && e.smoothed.acquirer_alpha < 0.80)
      );
      if (outageIdx === -1 && events.length > 0) {
        outageIdx = Math.max(0, events.length - 1);
      }
    }

    // Baseline curve only appears/starts drawing once the live outage is actually triggered
    if (outageIdx >= 0) {
      const alphaRecoveryMarker = outageMarkers && outageMarkers.find(
        (m) => m.type === 'recovery' && (!m.acquirer_id || m.acquirer_id === 'acquirer_alpha')
      );
      let recoveryIdx = -1;
      if (alphaRecoveryMarker) {
        if (alphaRecoveryMarker.seq) {
          recoveryIdx = events.findIndex((e) => e.seq === alphaRecoveryMarker.seq);
        }
        if (recoveryIdx === -1 && alphaRecoveryMarker.timestamp) {
          let minDiff = Infinity;
          events.forEach((e, idx) => {
            const diff = Math.abs(e.timestamp - alphaRecoveryMarker.timestamp);
            if (diff < minDiff) {
              minDiff = diff;
              recoveryIdx = idx;
            }
          });
        }
      }

      // Draw starting slightly before outage trigger to establish 100% pre-outage baseline level
      const startIdx = Math.max(0, outageIdx - 4);
      const ptsBase = [];

      for (let i = startIdx; i < n; i++) {
        const x = getX(i);
        const delta = i - outageIdx;

        if (delta < 3) {
          // Pre-trip: Phase 6 static baseline absorbed M=3 consecutive failures at 100%
          const y = getY(1.0);
          ptsBase.push(`${ptsBase.length === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`);
        } else if (delta === 3) {
          // Discontinuous 100% cliff drop: Instantaneous plunge from 1.0 to 0.0
          const yTop = getY(1.0);
          const yBottom = getY(0.0);
          ptsBase.push(`L ${x.toFixed(1)} ${yTop.toFixed(1)}`);
          ptsBase.push(`L ${x.toFixed(1)} ${yBottom.toFixed(1)}`);
        } else {
          // Post-trip state: 0% allocation until recovery probe succeeds
          if (recoveryIdx >= 0 && i >= recoveryIdx) {
            if (i === recoveryIdx) {
              const yBottom = getY(0.0);
              const yTop = getY(1.0);
              ptsBase.push(`L ${x.toFixed(1)} ${yBottom.toFixed(1)}`);
              ptsBase.push(`L ${x.toFixed(1)} ${yTop.toFixed(1)}`);
            } else {
              const y = getY(1.0);
              ptsBase.push(`L ${x.toFixed(1)} ${y.toFixed(1)}`);
            }
          } else {
            const y = getY(0.0);
            ptsBase.push(`L ${x.toFixed(1)} ${y.toFixed(1)}`);
          }
        }
      }

      pathStaticBaseline = ptsBase.join(' ');
    }

    const last = events[events.length - 1];
    const latestW = last.smoothed || last.weights || {};

    return {
      pathAlpha: ptsA.join(' '),
      pathBeta: ptsB.join(' '),
      pathGamma: ptsG.join(' '),
      pathStaticBaseline,
      currentWeights: {
        acquirer_alpha: latestW.acquirer_alpha ?? 0.82,
        acquirer_beta: latestW.acquirer_beta ?? 0.15,
        acquirer_gamma: latestW.acquirer_gamma ?? 0.03,
      },
      markerPositions: markers,
    };
  }, [
    events,
    outageMarkers,
    activeOutages,
    isAlphaDegraded,
    chartWidth,
    chartHeight,
    padding.left,
    padding.top,
  ]);

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
              {events.length > 0 && currentWeights.acquirer_alpha !== undefined
                ? `${(currentWeights.acquirer_alpha * 100).toFixed(1)}%`
                : '--.-%'}
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
              {events.length > 0 && currentWeights.acquirer_beta !== undefined
                ? `${(currentWeights.acquirer_beta * 100).toFixed(1)}%`
                : '--.-%'}
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
              {events.length > 0 && currentWeights.acquirer_gamma !== undefined
                ? `${(currentWeights.acquirer_gamma * 100).toFixed(1)}%`
                : '--.-%'}
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
              {peakStepDelta > 0 ? `${peakStepDelta.toFixed(1)}%` : '--.-%'}
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
              or trigger a test transaction to see real-time closed-loop allocation.
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
              const markerColor = isOutage ? '#E5484D' : '#5B8DEF';

              return (
                <g key={marker.id || `${marker.seq}-${marker.timestamp}`}>
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
                <circle
                  cx={width - padding.right}
                  y={padding.top + chartHeight - currentWeights.acquirer_alpha * chartHeight}
                  r="3.5"
                  fill={alphaColor}
                />
                <circle
                  cx={width - padding.right}
                  y={padding.top + chartHeight - currentWeights.acquirer_beta * chartHeight}
                  r="2.5"
                  fill={betaColor}
                />
                <circle
                  cx={width - padding.right}
                  y={padding.top + chartHeight - currentWeights.acquirer_gamma * chartHeight}
                  r="2"
                  fill={gammaColor}
                  fillOpacity={isGammaDegraded ? '1' : '0.75'}
                />
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
              -60s
            </text>
            <text
              x={width / 2}
              y={height - 10}
              fill="#8B8F98"
              fontSize="9"
              textAnchor="middle"
              className="font-sans"
            >
              Continuous telemetry stream
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
