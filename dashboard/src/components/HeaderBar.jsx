import React, { useEffect, useState } from 'react';

function useUtcClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return now.toISOString().slice(11, 19);
}

/**
 * Status bar: one terminal line across the top. Wordmark, link state, stream identity and
 * frame accounting, UTC clock. Only abnormal states get color (and a blinking lamp).
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
}) {
  const clock = useUtcClock();
  const dropped = stream?.droppedFrames ?? 0;
  const restarts = stream?.restarts ?? 0;

  let lamp;
  if (connectionStatus === 'connected') {
    lamp = hasActiveOutage
      ? { text: 'DEGRADED', color: 'text-alert', blink: true }
      : isStale
      ? { text: 'IDLE', color: 'text-dim', blink: false }
      : { text: 'HEALTHY', color: 'text-ok', blink: false };
  } else if (connectionStatus === 'reconnecting') {
    lamp = { text: `RECONNECTING (${reconnectAttempt})`, color: 'text-alert', blink: true };
  } else if (connectionStatus === 'connecting') {
    lamp = { text: 'CONNECTING', color: 'text-dim', blink: true };
  } else {
    lamp = { text: 'CONNECTION LOST', color: 'text-alert', blink: false };
  }

  return (
    <header className="w-full border-b border-rule bg-pane">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-1.5 text-[11px] uppercase tracking-[0.08em]">
        <h1 className="font-headline font-bold text-base tracking-[0.2em] text-amber m-0 leading-none">
          LOOM<span className="text-dim"> ▸ </span>
          <span className="text-text">ROUTER</span>
        </h1>

        <span className={`flex items-center gap-1.5 ${lamp.color}`} role="status">
          <span className={`inline-block w-2 h-2 bg-current ${lamp.blink ? 'lamp-blink' : ''}`} />
          {lamp.text}
          {connectionStatus === 'disconnected' && onReconnect && (
            <button
              type="button"
              onClick={onReconnect}
              className="ml-1 px-1 border border-alert text-alert hover:bg-alert hover:text-ground uppercase"
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
          {onToggleCrt && (
            <button
              type="button"
              onClick={onToggleCrt}
              aria-pressed={crt}
              title="Toggle CRT effect (C)"
              className="px-1 border border-rule text-dim hover:text-amber hover:border-amber uppercase"
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
