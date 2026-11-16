import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { COLORS, tickerOf } from '../theme';

const time = (ts) => (typeof ts === 'number' ? new Date(ts * 1000).toISOString().slice(11, 19) : '--:--:--');

const FILTERS = [
  { id: 'ALL', label: 'ALL' },
  { id: 'DECLINED', label: 'DECLINED' },
  { id: 'ERROR', label: 'ERROR' },
  { id: 'acquirer_alpha', label: 'ALPHA', border: COLORS.amber },
  { id: 'acquirer_beta', label: 'BETA', border: COLORS.cyan },
  { id: 'acquirer_gamma', label: 'GAMMA', border: COLORS.cream },
];

/**
 * Event tape: received routing frames, newest first, with filter chips,
 * auto-scroll pause detection, and click-to-expand row details.
 */
export function EventTape({ events = [], limit = 60 }) {
  const [activeFilter, setActiveFilter] = useState('ALL');
  const [expandedIndex, setExpandedIndex] = useState(null);
  const [isScrolledDown, setIsScrolledDown] = useState(false);
  const [newEventCount, setNewEventCount] = useState(0);

  const containerRef = useRef(null);
  const prevEventsLengthRef = useRef(events.length);

  // Filter events
  const filteredEvents = useMemo(() => {
    if (activeFilter === 'ALL') return events;
    return events.filter((ev) => {
      if (activeFilter === 'DECLINED') return !ev.authorized;
      if (activeFilter === 'ERROR') return ev.status === 'ERROR';
      return ev.selected === activeFilter;
    });
  }, [events, activeFilter]);

  // Sliced newest first
  const rows = useMemo(() => filteredEvents.slice(-limit).reverse(), [filteredEvents, limit]);

  // Detect new frames while user is scrolled away from top
  useEffect(() => {
    if (events.length > prevEventsLengthRef.current) {
      const added = events.length - prevEventsLengthRef.current;
      if (isScrolledDown) {
        setNewEventCount((prev) => prev + added);
      }
    }
    prevEventsLengthRef.current = events.length;
  }, [events.length, isScrolledDown]);

  const handleScroll = useCallback(() => {
    if (!containerRef.current) return;
    const isDown = containerRef.current.scrollTop > 30;
    setIsScrolledDown(isDown);
    if (!isDown) {
      setNewEventCount(0);
    }
  }, []);

  const scrollToTop = useCallback(() => {
    if (containerRef.current) {
      containerRef.current.scrollTo({ top: 0, behavior: 'smooth' });
      setIsScrolledDown(false);
      setNewEventCount(0);
    }
  }, []);

  const toggleExpand = useCallback((idx) => {
    setExpandedIndex((prev) => (prev === idx ? null : idx));
  }, []);

  if (events.length === 0) {
    return <div className="px-2 py-3 text-[11px] text-dim">no data — no routing frames received yet</div>;
  }

  return (
    <div className="flex flex-col h-full">
      {/* Filter chips */}
      <div className="flex flex-wrap items-center gap-1.5 px-2 py-1.5 border-b border-rule text-[10px] uppercase tracking-[0.08em] bg-pane">
        {FILTERS.map((f) => {
          const active = activeFilter === f.id;
          return (
            <button
              key={f.id}
              type="button"
              onClick={() => {
                setActiveFilter(f.id);
                setExpandedIndex(null);
              }}
              style={f.border ? { borderLeftColor: f.border, borderLeftWidth: '2px' } : undefined}
              className={`px-1.5 py-0.5 border cursor-pointer transition-colors ${
                active
                  ? 'bg-amber text-ground border-amber font-semibold'
                  : 'bg-ground text-dim border-rule hover:border-amber hover:text-amber'
              }`}
            >
              {f.label}
            </button>
          );
        })}
      </div>

      {/* Auto-scroll unread pill */}
      {newEventCount > 0 && (
        <div className="px-2 py-1 bg-pane border-b border-rule flex justify-center">
          <button
            type="button"
            onClick={scrollToTop}
            className="px-2 py-0.5 border border-amber text-amber text-[10px] font-mono hover:bg-amber hover:text-ground cursor-pointer animate-pulse"
          >
            ↑ {newEventCount} new event{newEventCount > 1 ? 's' : ''} (click to view)
          </button>
        </div>
      )}

      {/* Tape list */}
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="overflow-auto max-h-[360px] flex-1"
      >
        <table className="w-full text-[11px] font-mono border-collapse" data-testid="event-tape">
          <thead className="sticky top-0 bg-pane z-10">
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
              const isExpanded = expandedIndex === ev.index;

              return (
                <React.Fragment key={ev.index}>
                  <tr
                    onClick={() => toggleExpand(ev.index)}
                    className={`cursor-pointer transition-colors hover:bg-amber/5 ${bad ? 'bg-alert/10' : ''}`}
                    title="Click to view weights snapshot"
                  >
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

                  {/* Expanded detail row */}
                  {isExpanded && (
                    <tr className="bg-ground border-y border-dashed border-rule">
                      <td colSpan={6} className="px-3 py-1.5 text-[10px] text-dim">
                        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                          <span className="text-amber font-semibold">Snapshot Tx #{ev.seq ?? ev.index}:</span>
                          {ev.weights ? (
                            Object.entries(ev.weights).map(([aid, w]) => (
                              <span key={aid} className="font-mono">
                                {tickerOf(aid)}: <span className="text-text">{(w * 100).toFixed(1)}%</span>
                              </span>
                            ))
                          ) : (
                            <span>no weights recorded</span>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}

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
    </div>
  );
}

export default EventTape;
