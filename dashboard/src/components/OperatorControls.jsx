import React from 'react';

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

/**
 * Scenario presets and global reset, as function-key style buttons (the body of the F6
 * pane). Shift+1/2/3 run the presets; R resets (see App.jsx).
 */
export function OperatorControls(props) {
  const { lastActionStatus, onClearStatus, onPresetGlobalReset } = props;
  const statusTone =
    lastActionStatus?.type === 'success'
      ? 'text-ok'
      : lastActionStatus?.type === 'warning'
      ? 'text-alert'
      : 'text-dim';

  return (
    <div className="flex flex-col">
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
              <button type="button" onClick={onClearStatus} className="text-dim hover:text-text">
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
