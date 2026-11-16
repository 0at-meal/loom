import React, { useMemo, useState, useRef, useCallback } from 'react';
import BASELINE_REFERENCE_RUN from '../data/baselineReferenceRun.json';
import { useChartSize } from '../hooks/useChartSize';
import { ACQUIRERS, ACQUIRER_IDS, COLORS, acquirerColor, isDegraded } from '../theme';

// Recorded frames drawn before the outage so the overlay starts from its pre-outage level
const BASELINE_LEAD_IN = 4;

/**
 * Static baseline overlay: the recorded run in dashboard/src/data/baselineReferenceRun.json
 * (written by `python scripts/compare_psr.py --out-reference-json ...`), aligned so its
 * outage starts at the latest Alpha outage marker in view.
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
const DRAW_ORDER = ['acquirer_gamma', 'acquirer_beta', 'acquirer_alpha'];
const STROKE = { acquirer_alpha: 2, acquirer_beta: 1.5, acquirer_gamma: 1.25 };

/**
 * Live Traffic Allocation Chart with outage markers, translucent area fills,
 * crosshair tooltip with keyboard navigation, alert banner, and phase timeline.
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
  const containerRef = useRef(null);
  const measuredSize = useChartSize(containerRef);
  const width = Math.max(600, measuredSize.width || 800);
  const height = Math.max(260, measuredSize.height || 300);

  const [crosshairIndex, setCrosshairIndex] = useState(null);
  const [bannerDismissed, setBannerDismissed] = useState(false);

  const padding = { top: 32, right: 52, bottom: 32, left: 40 };
  const chartWidth = Math.max(100, width - padding.left - padding.right);
  const chartHeight = Math.max(100, height - padding.top - padding.bottom);

  const colorOf = (aid) => acquirerColor(aid, activeOutages, acquirerStates);

  const degradedAcquirers = ACQUIRER_IDS.filter((aid) => isDegraded(aid, activeOutages, acquirerStates));
  const hasActiveOutage = degradedAcquirers.length > 0;

  const { paths, areaPaths, pathStaticBaseline, currentWeights, markerPositions, breaks, outageShades, phases } = useMemo(() => {
    const empty = {
      paths: {},
      areaPaths: {},
      pathStaticBaseline: '',
      currentWeights: {},
      markerPositions: [],
      breaks: [],
      outageShades: [],
      phases: [],
    };
    if (!events || events.length === 0) return empty;

    const n = events.length;
    const getX = (i) => padding.left + (n === 1 ? chartWidth / 2 : (i / (n - 1)) * chartWidth);
    const getY = (val) => padding.top + chartHeight - Math.max(0, Math.min(1, val)) * chartHeight;
    const zeroY = padding.top + chartHeight;

    // One path and area path per acquirer
    const paths = {};
    const areaPaths = {};
    for (const aid of ACQUIRER_IDS) {
      const parts = [];
      const areaParts = [];
      let penDown = false;
      let segStart = null;
      let lastPoint = null;

      events.forEach((ev, i) => {
        const w = ev.weights ? ev.weights[aid] : undefined;
        if (ev.missingBefore > 0 || ev.restartBefore) {
          if (penDown && segStart && lastPoint) {
            areaParts.push(`L ${lastPoint.x} ${zeroY} L ${segStart.x} ${zeroY} Z`);
          }
          penDown = false;
          segStart = null;
        }
        if (typeof w !== 'number') {
          if (penDown && segStart && lastPoint) {
            areaParts.push(`L ${lastPoint.x} ${zeroY} L ${segStart.x} ${zeroY} Z`);
          }
          penDown = false;
          segStart = null;
          return;
        }
        const xStr = getX(i).toFixed(1);
        const yStr = getY(w).toFixed(1);
        parts.push(`${penDown ? 'L' : 'M'} ${xStr} ${yStr}`);
        if (!penDown) {
          areaParts.push(`M ${xStr} ${zeroY} L ${xStr} ${yStr}`);
          segStart = { x: xStr, y: yStr };
        } else {
          areaParts.push(`L ${xStr} ${yStr}`);
        }
        lastPoint = { x: xStr, y: yStr };
        penDown = true;
      });

      if (penDown && segStart && lastPoint) {
        areaParts.push(`L ${lastPoint.x} ${zeroY} L ${segStart.x} ${zeroY} Z`);
      }

      paths[aid] = parts.join(' ');
      areaPaths[aid] = areaParts.join(' ');
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
      .map((m) => ({ ...m, x: getX(byIndex.get(m.index)), eventIdx: byIndex.get(m.index) }));

    // Outage shading zones
    const outageShades = [];
    markerPositions.forEach((m) => {
      if (m.type === 'outage') {
        const nextRec = markerPositions.find((r) => r.type === 'recovery' && r.x > m.x);
        const endX = nextRec ? nextRec.x : getX(n - 1);
        outageShades.push({
          key: `shade-${m.id}`,
          x: m.x,
          width: Math.max(4, endX - m.x),
        });
      }
    });

    // Timeline phases below x-axis
    const phases = [];
    const firstOutage = markerPositions.find((m) => m.type === 'outage');
    if (firstOutage) {
      if (firstOutage.x > padding.left) {
        phases.push({
          label: 'WARMUP',
          x1: padding.left,
          x2: firstOutage.x,
          color: COLORS.dim,
        });
      }
      const firstRecovery = markerPositions.find((m) => m.type === 'recovery' && m.x > firstOutage.x);
      const outEndX = firstRecovery ? firstRecovery.x : getX(n - 1);
      phases.push({
        label: 'OUTAGE',
        x1: firstOutage.x,
        x2: outEndX,
        color: COLORS.alert,
        fill: 'rgba(255, 74, 61, 0.08)',
      });
      if (firstRecovery) {
        phases.push({
          label: 'RECOVERY',
          x1: firstRecovery.x,
          x2: getX(n - 1),
          color: COLORS.ok,
        });
      }
    }

    const overlay = baselineOverlay(events, outageMarkers || []);
    const pathStaticBaseline = overlay
      .map((p, k) => {
        const x = getX(p.i).toFixed(1);
        const y = getY(p.weight).toFixed(1);
        const prev = overlay[k - 1];
        if (!prev) return `M ${x} ${y}`;
        return `L ${x} ${getY(prev.weight).toFixed(1)} L ${x} ${y}`;
      })
      .join(' ');

    const latest = events[n - 1].weights || {};
    const currentWeights = {};
    for (const aid of ACQUIRER_IDS) {
      if (typeof latest[aid] === 'number') currentWeights[aid] = latest[aid];
    }

    return { paths, areaPaths, pathStaticBaseline, currentWeights, markerPositions, breaks, outageShades, phases };
  }, [events, outageMarkers, chartWidth, chartHeight, padding.left, padding.top]);

  const yOf = (w) => padding.top + chartHeight - w * chartHeight;
  const weightText = (aid) =>
    typeof currentWeights[aid] === 'number' ? `${(currentWeights[aid] * 100).toFixed(1)}%` : 'no data';
  const droppedFrames = stream?.droppedFrames ?? 0;
  const restarts = stream?.restarts ?? 0;

  // Crosshair interactions
  const handleMouseMove = useCallback(
    (e) => {
      if (!events || events.length === 0) return;
      const rect = e.currentTarget.getBoundingClientRect();
      const mouseX = e.clientX - rect.left;
      const effectiveX = Math.max(padding.left, Math.min(width - padding.right, mouseX));
      const fraction = (effectiveX - padding.left) / chartWidth;
      const nearestIdx = Math.round(fraction * (events.length - 1));
      setCrosshairIndex(Math.max(0, Math.min(events.length - 1, nearestIdx)));
    },
    [events, chartWidth, padding.left, padding.right, width]
  );

  const handleKeyDown = useCallback(
    (e) => {
      if (!events || events.length === 0) return;
      if (e.key === 'ArrowLeft') {
        e.preventDefault();
        setCrosshairIndex((prev) => (prev === null ? events.length - 1 : Math.max(0, prev - 1)));
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        setCrosshairIndex((prev) => (prev === null ? events.length - 1 : Math.min(events.length - 1, prev + 1)));
      } else if (e.key === 'Escape') {
        setCrosshairIndex(null);
      }
    },
    [events]
  );

  const selectedEvent = crosshairIndex !== null && events[crosshairIndex] ? events[crosshairIndex] : null;
  const crosshairX =
    crosshairIndex !== null && events.length > 0
      ? padding.left + (events.length === 1 ? chartWidth / 2 : (crosshairIndex / (events.length - 1)) * chartWidth)
      : null;

  return (
    <div className="flex flex-col h-full" ref={containerRef}>
      {/* Legend: live weights per acquirer, and which line is the recorded baseline */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-2 py-1.5 border-b border-rule text-[11px]">
        {ACQUIRERS.map((acq) => {
          const color = colorOf(acq.id);
          const bad = isDegraded(acq.id, activeOutages, acquirerStates);
          return (
            <span key={acq.id} className="flex items-center gap-1.5">
              <span
                className={`inline-block w-3 h-1 ${bad ? 'hatch-degraded' : ''}`}
                style={{ backgroundColor: color }}
              />
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
      <div
        className="relative flex-1 min-h-[260px] lg:min-h-[300px] flex items-center justify-center overflow-hidden bg-well outline-none"
        tabIndex={0}
        onKeyDown={handleKeyDown}
        aria-label="Live traffic allocation chart. Use Left and Right arrow keys to inspect frames."
      >
        {/* Transient alert banner */}
        {hasActiveOutage && !bannerDismissed && (
          <div
            role="alert"
            aria-live="assertive"
            className="absolute top-0 inset-x-0 z-30 bg-alert/15 border-b border-alert px-3 py-1 flex items-center gap-2 text-[11px] font-mono text-alert"
          >
            <span className="font-semibold">⚠ DEGRADED:</span>
            <span className="text-text">
              {degradedAcquirers.map((aid) => aid.replace('acquirer_', '').toUpperCase()).join(', ')} health or outage threshold breached
            </span>
            <button
              type="button"
              onClick={() => setBannerDismissed(true)}
              className="ml-auto text-dim hover:text-text cursor-pointer"
            >
              [dismiss]
            </button>
          </div>
        )}

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
          <svg
            viewBox={`0 0 ${width} ${height}`}
            className="absolute inset-0 w-full h-full cursor-crosshair select-none"
            preserveAspectRatio="xMidYMid meet"
            onMouseMove={handleMouseMove}
            onMouseLeave={() => setCrosshairIndex(null)}
          >
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

            {/* Outage background shading */}
            {outageShades.map((shade) => (
              <rect
                key={shade.key}
                x={shade.x}
                y={padding.top}
                width={shade.width}
                height={chartHeight}
                fill={COLORS.alert}
                opacity={0.06}
              />
            ))}

            {/* Translucent area fills under lines */}
            {DRAW_ORDER.map((aid) =>
              areaPaths[aid] ? (
                <path
                  key={`area-${aid}`}
                  d={areaPaths[aid]}
                  fill={colorOf(aid)}
                  opacity={0.07}
                />
              ) : null
            )}

            {/* Dropped frames / router restarts */}
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

            {/* Static baseline (recorded run), behind live lines */}
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
              ) : null
            )}

            {/* Vertical outage, recovery and restart markers with shapes */}
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
                  {isOutage && (
                    <polygon
                      points={`${marker.x - 4},${padding.top - 26} ${marker.x + 4},${padding.top - 26} ${marker.x},${padding.top - 22}`}
                      fill={markerColor}
                    />
                  )}
                  {marker.type === 'recovery' && (
                    <polygon
                      points={`${marker.x},${padding.top - 26} ${marker.x - 4},${padding.top - 22} ${marker.x + 4},${padding.top - 22}`}
                      fill={markerColor}
                    />
                  )}
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

            {/* Live head: square marker and last value */}
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

            {/* Interactive Crosshair and Tooltip */}
            {crosshairX !== null && selectedEvent && (
              <g className="crosshair-group">
                <line
                  x1={crosshairX}
                  y1={padding.top}
                  x2={crosshairX}
                  y2={padding.top + chartHeight}
                  stroke={COLORS.amber}
                  strokeWidth="1"
                  strokeOpacity="0.6"
                />
                {ACQUIRER_IDS.map((aid) => {
                  const w = selectedEvent.weights ? selectedEvent.weights[aid] : undefined;
                  if (typeof w !== 'number') return null;
                  return (
                    <circle
                      key={`cross-${aid}`}
                      cx={crosshairX}
                      cy={yOf(w)}
                      r={3.5}
                      fill={colorOf(aid)}
                      stroke={COLORS.ground}
                      strokeWidth="1"
                    />
                  );
                })}

                {/* Floating Tooltip Card */}
                {(() => {
                  const tipWidth = 150;
                  const tipHeight = 84;
                  const isRightHalf = crosshairX > width / 2;
                  const tipX = isRightHalf ? crosshairX - tipWidth - 8 : crosshairX + 8;
                  const tipY = Math.max(padding.top + 4, Math.min(height - padding.bottom - tipHeight - 4, padding.top + 10));
                  const timeStr = typeof selectedEvent.timestamp === 'number'
                    ? new Date(selectedEvent.timestamp * 1000).toISOString().slice(11, 19)
                    : '--:--:--';

                  return (
                    <g>
                      <rect
                        x={tipX}
                        y={tipY}
                        width={tipWidth}
                        height={tipHeight}
                        fill={COLORS.pane}
                        stroke={COLORS.rule}
                        strokeWidth="1"
                      />
                      <text x={tipX + 6} y={tipY + 12} fill={COLORS.dim} fontSize="8" className="font-mono">
                        Seq #{selectedEvent.seq ?? '--'} · {timeStr}
                      </text>
                      {ACQUIRERS.map((acq, k) => {
                        const w = selectedEvent.weights ? selectedEvent.weights[acq.id] : undefined;
                        const wStr = typeof w === 'number' ? `${(w * 100).toFixed(1)}%` : '--';
                        const isSelected = selectedEvent.selected === acq.id;
                        return (
                          <text
                            key={acq.id}
                            x={tipX + 6}
                            y={tipY + 26 + k * 12}
                            fill={colorOf(acq.id)}
                            fontSize="9"
                            className="font-mono font-medium"
                          >
                            ■ {acq.ticker.padEnd(6, ' ')} {wStr} {isSelected ? `[${selectedEvent.status || ''}]` : ''}
                          </text>
                        );
                      })}
                      <text x={tipX + 6} y={tipY + 76} fill={COLORS.dim} fontSize="8" className="font-mono truncate">
                        {selectedEvent.declineCode ? `${selectedEvent.declineCode} · ` : ''}
                        {typeof selectedEvent.latencyMs === 'number' ? `${selectedEvent.latencyMs.toFixed(1)}ms` : ''}
                      </text>
                    </g>
                  );
                })()}
              </g>
            )}

            {/* Timeline Phase Bar (below x-axis) */}
            {phases.length > 0 &&
              phases.map((ph, idx) => (
                <g key={`phase-${idx}`}>
                  <rect
                    x={ph.x1}
                    y={padding.top + chartHeight + 2}
                    width={Math.max(2, ph.x2 - ph.x1)}
                    height={10}
                    fill={ph.fill || 'transparent'}
                    stroke={ph.color}
                    strokeWidth="0.5"
                  />
                  {ph.x2 - ph.x1 > 35 && (
                    <text
                      x={(ph.x1 + ph.x2) / 2}
                      y={padding.top + chartHeight + 10}
                      fill={ph.color}
                      fontSize="7"
                      fontWeight="600"
                      textAnchor="middle"
                      className="font-mono"
                    >
                      {ph.label}
                    </text>
                  )}
                </g>
              ))}

            {/* Time axis */}
            <text x={padding.left} y={height - 6} fill={COLORS.dim} fontSize="9" className="font-mono">
              {events.length} frames ago
            </text>
            <text x={width / 2} y={height - 6} fill={COLORS.dim} fontSize="9" textAnchor="middle" className="font-mono">
              ← / → to inspect · hover for details
            </text>
            <text x={width - padding.right} y={height - 6} fill={COLORS.amber} fontSize="9" textAnchor="end" className="font-mono">
              Now
            </text>
          </svg>
        )}
      </div>
    </div>
  );
}

export default AllocationChart;
