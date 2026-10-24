import React from 'react';

const NO_DATA = 'no data';

function Readout({ label, value, sub, tone = 'text-text', hero = false }) {
  return (
    <div className="flex flex-col justify-between gap-1 px-3 py-2 border-rule min-w-0">
      <span className="text-[10px] uppercase tracking-[0.12em] text-dim truncate">{label}</span>
      <span
        className={`font-mono leading-none ${tone} ${
          hero ? 'text-4xl sm:text-[42px] font-semibold glow' : 'text-xl sm:text-2xl font-medium'
        }`}
      >
        {value}
      </span>
      {sub && <span className="text-[10px] text-dim truncate">{sub}</span>}
    </div>
  );
}

/**
 * KPI strip: rolling PSR (the hero readout), lifetime PSR, routed, declined, errors and the
 * peak allocation step. Every figure comes from received frames; before the first frame
 * they read "--" or "no data", never a made-up value.
 */
export function MetricReadouts({ metrics = {}, stream = null }) {
  const {
    rollingPSR = null,
    lifetimePSR = null,
    totalCount = 0,
    declinedCount = 0,
    errorCount = 0,
    peakStepDelta = null,
  } = metrics;
  const hasPSR = typeof rollingPSR === 'number';
  const hasData = totalCount > 0;
  const droppedFrames = stream?.droppedFrames ?? 0;
  const psrTone = !hasPSR ? 'text-dim' : rollingPSR < 80 ? 'text-alert' : 'text-amber';

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 divide-x divide-rule [&>*]:border-t [&>*]:border-rule lg:[&>*]:border-t-0">
      <Readout
        hero
        label="Rolling PSR · last 50 recv"
        value={hasPSR ? `${rollingPSR.toFixed(1)}%` : '--.-%'}
        tone={psrTone}
        sub={
          droppedFrames > 0 ? (
            <span className="text-alert">{droppedFrames} frames dropped, not counted</span>
          ) : (
            'payment success rate'
          )
        }
      />
      <Readout
        label="Lifetime PSR"
        value={typeof lifetimePSR === 'number' ? `${lifetimePSR.toFixed(2)}%` : NO_DATA}
        tone={typeof lifetimePSR === 'number' ? 'text-text' : 'text-dim'}
        sub="since this page connected"
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
        label="Peak step Δ"
        value={typeof peakStepDelta === 'number' ? `${peakStepDelta.toFixed(1)}%` : NO_DATA}
        tone={typeof peakStepDelta === 'number' ? 'text-amber' : 'text-dim'}
        sub="vs 100% breaker cliff"
      />
    </div>
  );
}

export default MetricReadouts;
