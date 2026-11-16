import React, { useState } from 'react';
import { ACQUIRERS, COLORS } from '../theme';

const PRESETS = [
  {
    key: 'cliff',
    code: '1',
    title: 'Standard cliff',
    tag: 'M=3 gauntlet',
    handler: 'onPresetStandardCliff',
    copy: 'Hard step outage on Alpha. Watch the PID ramp the live weights while the static baseline replays its recorded cliff.',
  },
  {
    key: 'blip',
    code: '2',
    title: 'Sensitive blip',
    tag: 'M=1 overreaction',
    handler: 'onPresetSensitiveBlip',
    copy: '3.5 s transient outage on Alpha. A breaker that trips on one failure (M=1) overreacts to short dips.',
  },
  {
    key: 'gray',
    code: '3',
    title: 'Gray failure',
    tag: 'p=0.60 brownout',
    handler: 'onPresetGrayFailure',
    copy: 'Degrades Alpha to 60% success. A consecutive-failure breaker rarely trips on this.',
  },
];

const BEHAVIORS = ['RETURN_DECLINE', 'HTTP_503', 'LATENCY_SPIKE'];

/**
 * Scenario presets, advanced simulator controls (gray failure rate sliders and failure
 * behavior selectors), and global route reset.
 */
export function OperatorControls(props) {
  const {
    lastActionStatus,
    onClearStatus,
    onPresetGlobalReset,
    rates = {},
    behaviors = {},
    onSetRate,
    onRateCommit,
    onSetBehavior,
    submitting = {},
  } = props;

  const [showAdvanced, setShowAdvanced] = useState(false);

  const statusTone =
    lastActionStatus?.type === 'success'
      ? 'text-ok'
      : lastActionStatus?.type === 'warning'
      ? 'text-alert'
      : 'text-dim';

  return (
    <div className="flex flex-col">
      {/* Preset buttons */}
      <div className="grid grid-cols-1 md:grid-cols-3 divide-y md:divide-y-0 md:divide-x divide-rule">
        {PRESETS.map((p) => (
          <button
            key={p.key}
            type="button"
            onClick={props[p.handler]}
            className="group text-left p-2 flex gap-2 hover:bg-amber/10 focus-visible:bg-amber/10 outline-none"
          >
            <span className="shrink-0 h-5 min-w-[2.25rem] px-1 border border-amber text-amber text-[10px] font-semibold flex items-center justify-center group-hover:bg-amber group-hover:text-ground">
              ⇧{p.code}
            </span>
            <span className="flex flex-col gap-0.5 min-w-0">
              <span className="flex items-baseline gap-2">
                <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-text">
                  Trigger {p.title.toLowerCase()}
                </span>
                <span className="text-[10px] text-dim font-mono">{p.tag}</span>
              </span>
              <span className="text-[10.5px] text-dim leading-snug">{p.copy}</span>
            </span>
          </button>
        ))}
      </div>

      {/* Advanced simulator controls (sliders & failure modes) */}
      <div className="border-t border-rule bg-ground/50">
        <button
          type="button"
          onClick={() => setShowAdvanced((v) => !v)}
          className="w-full text-left px-2 py-1 flex items-center gap-1.5 text-[10px] uppercase tracking-[0.08em] text-dim hover:text-amber cursor-pointer"
        >
          <span>{showAdvanced ? '▾' : '▸'}</span>
          <span>Advanced acquirer controls (rate sliders & failure behaviors)</span>
        </button>

        {showAdvanced && (
          <div className="px-3 py-2 flex flex-col gap-2 border-t border-rule/50">
            {ACQUIRERS.map((acq) => {
              const currentRate = rates[acq.id] ?? 0.95;
              const ratePct = Math.round(currentRate * 100);
              const currentBehavior = behaviors[acq.id] || 'RETURN_DECLINE';
              const isBusy = !!submitting[`rate_${acq.id}`];

              return (
                <div key={acq.id} className="flex flex-wrap items-center gap-3 text-[11px] font-mono">
                  <span className="w-16 font-semibold uppercase tracking-[0.08em]" style={{ color: acq.color }}>
                    {acq.ticker}
                  </span>
                  <span className="text-dim text-[10px] uppercase">Base rate:</span>
                  <input
                    type="range"
                    min="0"
                    max="100"
                    step="1"
                    disabled={isBusy}
                    value={ratePct}
                    onChange={(e) => onSetRate && onSetRate(acq.id, Number(e.target.value) / 100)}
                    onMouseUp={(e) => onRateCommit && onRateCommit(acq.id, Number(e.target.value) / 100)}
                    onTouchEnd={(e) => onRateCommit && onRateCommit(acq.id, Number(e.target.value) / 100)}
                    className="flex-1 min-w-[120px] max-w-xs accent-amber cursor-pointer"
                    aria-label={`${acq.ticker} base success rate`}
                  />
                  <span className="w-10 text-right font-mono" style={{ color: ratePct < 70 ? COLORS.alert : COLORS.text }}>
                    {ratePct}%
                  </span>
                  <span className="text-dim text-[10px] uppercase ml-2">Mode:</span>
                  <select
                    value={currentBehavior}
                    onChange={(e) => onSetBehavior && onSetBehavior(acq.id, e.target.value)}
                    className="bg-pane border border-rule px-1 py-0.5 text-[10px] text-text font-mono uppercase"
                    aria-label={`${acq.ticker} failure behavior`}
                  >
                    {BEHAVIORS.map((b) => (
                      <option key={b} value={b}>
                        {b}
                      </option>
                    ))}
                  </select>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Reset & action status bar */}
      <div className="flex flex-wrap items-center gap-2 px-2 py-1.5 border-t border-rule text-[11px]">
        {onPresetGlobalReset && (
          <button
            type="button"
            onClick={onPresetGlobalReset}
            className="px-1.5 border border-rule text-text uppercase tracking-[0.08em] hover:border-alert hover:text-alert"
          >
            <span className="text-amber">R</span> Reset all routes
          </button>
        )}
        {lastActionStatus ? (
          <span className="flex items-center gap-2 font-mono min-w-0">
            <span className={`${statusTone}`}>■</span>
            <span className="text-text truncate">{lastActionStatus.text}</span>
            {onClearStatus && (
              <button type="button" onClick={onClearStatus} className="text-dim hover:text-text cursor-pointer">
                [close]
              </button>
            )}
          </span>
        ) : (
          <span className="text-dim">Inject a scenario to compare Loom with static circuit-breaker fallbacks.</span>
        )}
      </div>
    </div>
  );
}

export default OperatorControls;
