import React from 'react';
import { ACQUIRERS, COLORS, isDegraded } from '../theme';

const NO_DATA = 'no data';

function Readout({ label, value, sub, tone = 'text-text', hero = false, delta = null, sparkline = null, className = '' }) {
  return (
    <div className={`flex flex-col justify-between gap-1 px-3 py-2 border-rule min-w-0 ${className}`}>
      <span className="text-[10px] uppercase tracking-[0.12em] text-dim truncate">{label}</span>
      <div className="flex items-baseline gap-2 flex-wrap">
        <span
          className={`font-mono leading-none ${tone} ${
            hero ? 'text-4xl sm:text-[44px] font-semibold glow' : 'text-xl sm:text-2xl font-medium'
          }`}
        >
          {value}
        </span>
        {delta}
      </div>
      {sparkline}
      {sub && <span className="text-[10px] text-dim truncate">{sub}</span>}
    </div>
  );
}

/**
 * KPI strip: rolling PSR (the hero readout, 2-col), routed, declined, errors, lifetime PSR,
 * and peak step delta. Every figure comes from received frames.
 * Below the primary strip is a per-acquirer rolling success rate breakdown.
 */
export function MetricReadouts({ metrics = {}, stream = null, acquirerStates = {}, activeOutages = {} }) {
  const {
    rollingPSR = null,
    previousRollingPSR = null,
    lifetimePSR = null,
    totalCount = 0,
    declinedCount = 0,
    errorCount = 0,
    peakStepDelta = null,
    rollingHistory = [],
    perAcquirerRolling = {},
  } = metrics;

  const hasPSR = typeof rollingPSR === 'number';
  const hasData = totalCount > 0;
  const droppedFrames = stream?.droppedFrames ?? 0;
  const psrTone = !hasPSR ? 'text-dim' : rollingPSR < 80 ? 'text-alert' : 'text-amber';

  let deltaElement = null;
  if (typeof rollingPSR === 'number' && typeof previousRollingPSR === 'number') {
    const diff = rollingPSR - previousRollingPSR;
    if (Math.abs(diff) >= 0.05) {
      const isUp = diff > 0;
      deltaElement = (
        <span className={`text-[12px] font-mono font-medium ${isUp ? 'text-ok' : 'text-alert'}`}>
          {isUp ? '↑ +' : '↓ '}{diff.toFixed(1)} pp
        </span>
      );
    } else {
      deltaElement = (
        <span className="text-[12px] font-mono text-dim">
          → 0.0 pp
        </span>
      );
    }
  }

  let sparklineElement = null;
  if (rollingHistory && rollingHistory.length > 1) {
    const w = 150;
    const h = 28;
    const pts = rollingHistory.map((val, idx) => {
      const x = 2 + (idx / (rollingHistory.length - 1)) * (w - 4);
      const clamped = Math.max(0, Math.min(100, val));
      const y = h - 2 - (clamped / 100) * (h - 6);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    const strokeColor = rollingPSR < 80 ? COLORS.alert : COLORS.amber;
    const fillColor = rollingPSR < 80 ? 'rgba(255, 74, 61, 0.15)' : 'rgba(255, 160, 40, 0.15)';
    const firstX = pts[0].split(',')[0];
    const lastX = pts[pts.length - 1].split(',')[0];
    const areaPath = `M ${firstX},${h} L ${pts.join(' L ')} L ${lastX},${h} Z`;

    sparklineElement = (
      <svg width={w} height={h} className="mt-1" aria-hidden="true">
        <path d={areaPath} fill={fillColor} />
        <polyline points={pts.join(' ')} fill="none" stroke={strokeColor} strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    );
  }

  return (
    <div className="flex flex-col">
      {/* Primary KPI row */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-7 divide-x divide-rule [&>*]:border-t [&>*]:border-rule lg:[&>*]:border-t-0">
        <Readout
          hero
          className="col-span-2"
          label="Rolling PSR · last 50 recv"
          value={hasPSR ? `${rollingPSR.toFixed(1)}%` : '--.-%'}
          tone={psrTone}
          delta={deltaElement}
          sparkline={sparklineElement}
          sub={
            droppedFrames > 0 ? (
              <span className="text-alert">{droppedFrames} frames dropped, not counted</span>
            ) : (
              'payment success rate'
            )
          }
        />
        <Readout
          label="Transactions routed"
          value={hasData ? totalCount.toLocaleString() : '--'}
          sub="frames received"
        />
        <Readout
          label="Declined"
          value={hasData ? declinedCount.toLocaleString() : '--'}
          tone={declinedCount > 0 ? 'text-alert' : 'text-text'}
          sub={hasData ? `${((declinedCount / totalCount) * 100).toFixed(1)}% of routed` : NO_DATA}
        />
        <Readout
          label="Errors"
          value={hasData ? errorCount.toLocaleString() : '--'}
          tone={errorCount > 0 ? 'text-alert' : 'text-text'}
          sub="acquirer unreachable"
        />
        <Readout
          label="Lifetime PSR"
          value={typeof lifetimePSR === 'number' ? `${lifetimePSR.toFixed(2)}%` : NO_DATA}
          tone={typeof lifetimePSR === 'number' ? 'text-text' : 'text-dim'}
          sub="since this page connected"
        />
        <Readout
          label="Peak step Δ"
          value={typeof peakStepDelta === 'number' ? `${peakStepDelta.toFixed(1)}%` : NO_DATA}
          tone={typeof peakStepDelta === 'number' ? 'text-amber' : 'text-dim'}
          sub="vs 100% breaker cliff"
        />
      </div>

      {/* Per-acquirer PSR sub-strip */}
      <div className="grid grid-cols-1 sm:grid-cols-3 border-t border-rule divide-y sm:divide-y-0 sm:divide-x divide-rule bg-ground/40 text-[11px] font-mono">
        {ACQUIRERS.map((acq) => {
          const bad = isDegraded(acq.id, activeOutages, acquirerStates);
          const rate = perAcquirerRolling[acq.id];
          const hasRate = typeof rate === 'number';
          const color = bad ? COLORS.alert : acq.color;
          return (
            <div key={acq.id} className="flex items-center justify-between px-3 py-1">
              <span className="flex items-center gap-1.5">
                <span
                  className={`inline-block w-2 h-2 ${bad ? 'lamp-blink hatch-degraded' : ''}`}
                  style={{ backgroundColor: color }}
                />
                <span style={{ color }} className="font-semibold uppercase tracking-[0.08em]">
                  {bad ? '✗' : '✓'} {acq.ticker}
                </span>
              </span>
              <span className={bad ? 'text-alert font-semibold' : 'text-text'}>
                {hasRate ? `${rate.toFixed(1)}%` : '—'}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default MetricReadouts;
