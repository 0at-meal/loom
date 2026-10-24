import React from 'react';
import { COLORS, tickerOf } from '../theme';

const time = (ts) => (typeof ts === 'number' ? new Date(ts * 1000).toISOString().slice(11, 19) : '--:--:--');

/**
 * Event tape: the received routing frames, newest first, like a trade blotter. Declines and
 * errors are red; dropped frames and restarts get their own line where they happened.
 */
export function EventTape({ events = [], limit = 40 }) {
  const rows = events.slice(-limit).reverse();

  if (rows.length === 0) {
    return <div className="px-2 py-3 text-[11px] text-dim">no data — no routing frames received yet</div>;
  }

  return (
    <div className="overflow-auto max-h-[260px]">
      <table className="w-full text-[11px] font-mono border-collapse" data-testid="event-tape">
        <thead className="sticky top-0 bg-pane">
          <tr className="text-[10px] uppercase tracking-[0.1em] text-dim border-b border-rule">
            <th className="px-2 py-1 text-left font-normal">Time</th>
            <th className="px-2 py-1 text-right font-normal">Seq</th>
            <th className="px-2 py-1 text-left font-normal">Acq</th>
            <th className="px-2 py-1 text-left font-normal">Status</th>
            <th className="px-2 py-1 text-left font-normal hidden sm:table-cell">Code</th>
            <th className="px-2 py-1 text-right font-normal">ms</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((ev) => {
            const bad = !ev.authorized;
            const tone = bad ? COLORS.alert : COLORS.text;
            return (
              <React.Fragment key={ev.index}>
                <tr className={bad ? 'bg-alert/10' : ''}>
                  <td className="px-2 py-0.5 text-dim">{time(ev.timestamp)}</td>
                  <td className="px-2 py-0.5 text-right text-dim">{ev.seq ?? '--'}</td>
                  <td className="px-2 py-0.5" style={{ color: tone }}>{tickerOf(ev.selected)}</td>
                  <td className="px-2 py-0.5" style={{ color: bad ? COLORS.alert : COLORS.ok }}>
                    {ev.status ?? '--'}
                  </td>
                  <td className="px-2 py-0.5 text-dim truncate max-w-[10rem] hidden sm:table-cell">{ev.declineCode ?? ''}</td>
                  <td className="px-2 py-0.5 text-right text-dim">
                    {typeof ev.latencyMs === 'number' ? ev.latencyMs.toFixed(1) : '--'}
                  </td>
                </tr>
                {(ev.missingBefore > 0 || ev.restartBefore) && (
                  <tr data-testid="tape-break">
                    <td colSpan={6} className="px-2 py-0.5 text-dim border-y border-dashed border-rule">
                      ┄ {ev.restartBefore ? 'router restarted' : `${ev.missingBefore} frames dropped`} ┄
                    </td>
                  </tr>
                )}
              </React.Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default EventTape;
