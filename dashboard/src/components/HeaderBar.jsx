import React, { useEffect, useState } from 'react';

function useUtcClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return now.toISOString().slice(11, 19);
}

function useSessionUptime() {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, []);
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  if (m >= 60) {
    const h = Math.floor(m / 60);
    const remM = m % 60;
    return `${h}h ${remM}m`;
  }
  return `${m}m ${s}s`;
}

/**
 * Signal quality indicator icon (3 tiered bars).
 */
function SignalBars({ quality = 'good' }) {
  // quality: 'good' (3 green), 'warn' (2 amber), 'poor' (1 red), 'off' (0)
  const bar1 = quality !== 'off';
  const bar2 = quality === 'good' || quality === 'warn';
  const bar3 = quality === 'good';

  const colorClass =
    quality === 'good'
      ? 'bg-ok'
      : quality === 'warn'
      ? 'bg-amber'
      : 'bg-alert';

  return (
    <span className="inline-flex items-end gap-[1.5px] h-3 w-3 mr-0.5" aria-hidden="true">
      <span className={`w-[2.5px] h-[3px] rounded-none ${bar1 ? colorClass : 'bg-rule'}`} />
      <span className={`w-[2.5px] h-[6px] rounded-none ${bar2 ? colorClass : 'bg-rule'}`} />
      <span className={`w-[2.5px] h-[10px] rounded-none ${bar3 ? colorClass : 'bg-rule'}`} />
    </span>
  );
}

/**
 * Status bar: wordmark, connection signal quality icon, stream identity,
 * session uptime, CRT toggle, keyboard shortcut trigger, and UTC clock.
 */
export function HeaderBar({
  connectionStatus = 'connected',
  reconnectAttempt = 0,
  hasActiveOutage = false,
  isStale = false,
  stream = null,
  onReconnect,
  crt = true,
  onToggleCrt,
  onOpenHelp,
}) {
  const clock = useUtcClock();
  const uptime = useSessionUptime();
  const dropped = stream?.droppedFrames ?? 0;
  const restarts = stream?.restarts ?? 0;

  let lamp;
  let signalQuality = 'good';

  if (connectionStatus === 'connected') {
    if (hasActiveOutage) {
      lamp = { text: 'DEGRADED', prefix: '✗ ', color: 'text-alert', blink: true };
      signalQuality = 'warn';
    } else if (isStale) {
      lamp = { text: 'IDLE', prefix: '• ', color: 'text-dim', blink: false };
      signalQuality = 'warn';
    } else if (dropped > 0) {
      lamp = { text: 'HEALTHY (GAPS)', prefix: '✓ ', color: 'text-amber', blink: false };
      signalQuality = 'warn';
    } else {
      lamp = { text: 'HEALTHY', prefix: '✓ ', color: 'text-ok', blink: false };
      signalQuality = 'good';
    }
  } else if (connectionStatus === 'reconnecting') {
    lamp = { text: `RECONNECTING (${reconnectAttempt})`, prefix: '✗ ', color: 'text-alert', blink: true };
    signalQuality = 'poor';
  } else if (connectionStatus === 'connecting') {
    lamp = { text: 'CONNECTING', prefix: '• ', color: 'text-dim', blink: true };
    signalQuality = 'poor';
  } else {
    lamp = { text: 'CONNECTION LOST', prefix: '✗ ', color: 'text-alert', blink: false };
    signalQuality = 'off';
  }

  return (
    <header className="w-full border-b border-rule bg-pane">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-1.5 text-[11px] uppercase tracking-[0.08em]">
        <h1 className="font-headline font-bold text-base tracking-[0.2em] text-amber m-0 leading-none">
          LOOM<span className="text-dim"> ▸ </span>
          <span className="text-text">ROUTER</span>
        </h1>

        <span
          className={`flex items-center gap-1.5 ${lamp.color}`}
          role="status"
          aria-live="polite"
        >
          <SignalBars quality={signalQuality} />
          <span className={`inline-block w-2 h-2 bg-current ${lamp.blink ? 'lamp-blink' : ''}`} />
          <span>{lamp.text}</span>
          {connectionStatus === 'disconnected' && onReconnect && (
            <button
              type="button"
              onClick={onReconnect}
              className="ml-1 px-1 border border-alert text-alert hover:bg-alert hover:text-ground uppercase cursor-pointer"
            >
              Reconnect
            </button>
          )}
        </span>

        <span className="text-dim hidden sm:inline">
          STREAM <span className="text-text font-mono normal-case">{stream?.streamId ? String(stream.streamId).slice(0, 8) : '--'}</span>
        </span>
        <span className="text-dim">
          SEQ <span className="text-text font-mono">{stream?.lastSeq ?? '--'}</span>
        </span>
        <span className={dropped > 0 ? 'text-alert' : 'text-dim'}>
          DROP <span className="font-mono">{dropped}</span>
        </span>
        {restarts > 0 && (
          <span className="text-alert">
            RESTART <span className="font-mono">{restarts}</span>
          </span>
        )}

        <span className="ml-auto flex items-center gap-3">
          <span className="text-dim font-mono hidden md:inline" title="Session connection uptime">
            ↑ {uptime}
          </span>
          {onOpenHelp && (
            <button
              type="button"
              onClick={onOpenHelp}
              title="Keyboard shortcuts (?)"
              className="px-1 border border-rule text-dim hover:text-amber hover:border-amber uppercase font-semibold cursor-pointer"
            >
              ?
            </button>
          )}
          {onToggleCrt && (
            <button
              type="button"
              onClick={onToggleCrt}
              aria-pressed={crt}
              title="Toggle CRT effect (C)"
              className="px-1 border border-rule text-dim hover:text-amber hover:border-amber uppercase cursor-pointer"
            >
              CRT {crt ? 'ON' : 'OFF'}
            </button>
          )}
          <span className="font-mono text-amber">{clock}</span>
          <span className="text-dim">UTC</span>
        </span>
      </div>
    </header>
  );
}

export default HeaderBar;
