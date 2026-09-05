import React from 'react';

/**
 * Ticket C Component: Simulation Controls & Scenario Presets Deck
 *
 * Housed behind the default-closed "Simulation controls" disclosure.
 * Provides on-demand benchmark scenario presets and global simulator reset.
 *
 * Revised contract:
 * - Palette: Ground (#0F1115), Panel (#16181D), Border (#2A2D34), Accent (#5B8DEF), Alert (#E5484D)
 * - Strict sentence case throughout; zero uppercase shouting
 * - IBM Plex Sans for copy, IBM Plex Mono for status codes and telemetry
 */
export function OperatorControls({
  lastActionStatus,
  onClearStatus,
  onPresetStandardCliff,
  onPresetSensitiveBlip,
  onPresetGrayFailure,
  onPresetGlobalReset,
}) {
  return (
    <div className="flex flex-col gap-3 font-sans">
      {/* Header & Presets Summary */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-2.5 pb-2 border-b border-[#2A2D34]">
        <div className="flex flex-col">
          <span className="text-xs font-semibold text-[#E4E6EB]">
            Benchmark scenario gauntlet presets
          </span>
          <span className="text-[11px] text-[#8B8F98]">
            Inject pre-calibrated test scenarios to evaluate closed-loop PID routing against static circuit-breaker fallbacks.
          </span>
        </div>

        {/* Global Reset Action */}
        {onPresetGlobalReset && (
          <button
            type="button"
            onClick={onPresetGlobalReset}
            className="px-2.5 py-1 text-[11px] font-medium bg-[#0F1115] hover:border-[#E5484D] border border-[#2A2D34] text-[#E4E6EB] cursor-pointer transition-colors self-start md:self-auto"
          >
            Reset all routes
          </button>
        )}
      </div>

      {/* Operator Status Notification Bar */}
      {lastActionStatus && (
        <div className="text-[11px] font-mono px-2.5 py-1.5 border border-[#2A2D34] bg-[#0F1115] flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span
              className="w-1.5 h-1.5 rounded-full inline-block"
              style={{
                backgroundColor:
                  lastActionStatus.type === 'success'
                    ? '#5B8DEF'
                    : lastActionStatus.type === 'warning'
                    ? '#E5484D'
                    : '#8B8F98',
              }}
            />
            <span className="text-[#E4E6EB]">{lastActionStatus.text}</span>
          </div>
          {onClearStatus && (
            <button
              type="button"
              onClick={onClearStatus}
              className="text-[#8B8F98] hover:text-[#E4E6EB] cursor-pointer text-xs"
            >
              [close]
            </button>
          )}
        </div>
      )}

      {/* Presets Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-2.5 text-[11px]">
        {/* Preset 1 */}
        <div className="p-3 bg-[#0F1115] border border-[#2A2D34] flex flex-col justify-between gap-2.5">
          <div className="flex flex-col gap-1">
            <div className="flex items-center justify-between">
              <span className="font-semibold text-xs text-[#E4E6EB]">
                Preset 1: Standard cliff
              </span>
              <span className="text-[9px] font-mono text-[#8B8F98]">M=3 gauntlet</span>
            </div>
            <p className="text-[10.5px] text-[#8B8F98] leading-relaxed">
              Triggers hard step outage on Acquirer Alpha. Demonstrates Loom's smooth PID 11.8% max step vs static router 100% stampede.
            </p>
          </div>
          <button
            type="button"
            onClick={onPresetStandardCliff}
            className="w-full py-1.5 px-2 text-[11px] font-medium bg-[#16181D] hover:border-[#5B8DEF] border border-[#2A2D34] text-[#E4E6EB] cursor-pointer transition-colors text-center"
          >
            Trigger standard cliff
          </button>
        </div>

        {/* Preset 2 */}
        <div className="p-3 bg-[#0F1115] border border-[#2A2D34] flex flex-col justify-between gap-2.5">
          <div className="flex flex-col gap-1">
            <div className="flex items-center justify-between">
              <span className="font-semibold text-xs text-[#E4E6EB]">
                Preset 2: Sensitive blip
              </span>
              <span className="text-[9px] font-mono text-[#8B8F98]">M=1 overreaction</span>
            </div>
            <p className="text-[10.5px] text-[#8B8F98] leading-relaxed">
              Fires a 3.5s transient dip on Alpha. Demonstrates why M=1 static baseline collapsed by 1000 bps while Loom absorbed the blip cleanly.
            </p>
          </div>
          <button
            type="button"
            onClick={onPresetSensitiveBlip}
            className="w-full py-1.5 px-2 text-[11px] font-medium bg-[#16181D] hover:border-[#5B8DEF] border border-[#2A2D34] text-[#E4E6EB] cursor-pointer transition-colors text-center"
          >
            Trigger sensitive blip
          </button>
        </div>

        {/* Preset 3 */}
        <div className="p-3 bg-[#0F1115] border border-[#2A2D34] flex flex-col justify-between gap-2.5">
          <div className="flex flex-col gap-1">
            <div className="flex items-center justify-between">
              <span className="font-semibold text-xs text-[#E4E6EB]">
                Preset 3: Gray failure
              </span>
              <span className="text-[9px] font-mono text-[#8B8F98]">p=0.60 brownout</span>
            </div>
            <p className="text-[10.5px] text-[#8B8F98] leading-relaxed">
              Degrades Alpha success rate to 60%. Demonstrates Loom continuous traffic shedding vs static counter-reset bleed.
            </p>
          </div>
          <button
            type="button"
            onClick={onPresetGrayFailure}
            className="w-full py-1.5 px-2 text-[11px] font-medium bg-[#16181D] hover:border-[#5B8DEF] border border-[#2A2D34] text-[#E4E6EB] cursor-pointer transition-colors text-center"
          >
            Trigger gray failure
          </button>
        </div>
      </div>
    </div>
  );
}

export default OperatorControls;
