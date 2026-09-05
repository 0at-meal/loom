import React from 'react';

/**
 * Header Bar Component: Masthead Wordmark & Single Minimal Status Line
 *
 * Phase 7 Revision 4 Contract:
 * - Wordmark: Space Grotesk (weight 700) for "Loom"
 * - Single status line directly beneath wordmark: dot + word (e.g. 'Healthy'), NOT a banner or box
 * - Inter for the status text
 * - Eliminates right-aligned pill boxes, RTT gadget, and UTC clock to achieve extreme density reduction
 */
export function HeaderBar({
  connectionStatus = 'connected',
  reconnectAttempt = 0,
  hasActiveOutage = false,
  onReconnect,
}) {
  return (
    <header className="w-full font-sans">
      <div className="flex flex-col gap-0.5">
        <h1 className="font-headline font-bold text-2xl sm:text-3xl text-[#E4E6EB] tracking-tight">
          Loom
        </h1>
        <div className="flex items-center gap-1.5 text-xs text-[#8B8F98]">
          {connectionStatus === 'connected' ? (
            hasActiveOutage ? (
              <>
                <span className="w-1.5 h-1.5 rounded-full bg-[#E5484D] inline-block animate-pulse" />
                <span className="text-[#E5484D]">Degraded</span>
              </>
            ) : (
              <>
                <span className="w-1.5 h-1.5 rounded-full bg-[#5B8DEF] inline-block" />
                <span>Healthy</span>
              </>
            )
          ) : connectionStatus === 'reconnecting' ? (
            <>
              <span className="w-1.5 h-1.5 rounded-full bg-[#E5484D] inline-block animate-pulse" />
              <span className="text-[#E5484D]">Reconnecting ({reconnectAttempt})</span>
            </>
          ) : (
            <div className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-[#E5484D] inline-block" />
              <span className="text-[#E5484D]">Connection lost</span>
              {onReconnect && (
                <button
                  type="button"
                  onClick={onReconnect}
                  className="text-xs text-[#8B8F98] hover:text-[#E4E6EB] underline ml-1 cursor-pointer bg-transparent border-0 p-0"
                >
                  Reconnect
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </header>
  );
}

export default HeaderBar;
