import React from 'react';

/**
 * Ticket B Component: Phase 6 Benchmark Comparison Card
 *
 * Renders the empirical evaluation results from `docs/phase6-qa-report.md` (Table TC-QA-601 to 605)
 * contrasting Loom's closed-loop PID Thompson Sampling router against the Static Circuit Breaker Baseline.
 *
 * Revised contract:
 * - Monospace strictly for numeric values and percentages
 * - IBM Plex Sans for descriptions in sentence case
 * - Palette: Panel (#16181D), Border (#2A2D34), Accent (#5B8DEF)
 * - Zero drop shadows
 */
export function BaselineComparisonCard() {
  const scenarios = [
    {
      id: 'overreaction',
      title: 'Sensitive overreaction (M=1)',
      loomPsr: '86.00%',
      baselinePsr: '76.00%',
      lift: '+1000 bps',
      loomDelta: '11.77%',
      baselineDelta: '100.0%',
      summary:
        'Static router killed 29 consecutive transactions due to cascading circuit-breaker trips on routine card declines. Loom absorbed the blip cleanly.',
    },
    {
      id: 'cliff',
      title: 'Standard cliff outage (M=3)',
      loomPsr: '86.00%',
      baselinePsr: '92.00%*',
      lift: '8.5x stability',
      loomDelta: '11.77%',
      baselineDelta: '100.0%',
      summary:
        '*Baseline score was an artifact of infinite mock capacity with 100% stampede. Real downstream gateways collapse under cliff shifts; Loom dampens peak shift to 11.8%.',
    },
    {
      id: 'gray',
      title: 'Brownout gray failure (p=0.60)',
      loomPsr: '82.00%',
      baselinePsr: '68.67%',
      lift: '+1333 bps',
      loomDelta: '4.82%',
      baselineDelta: '100.0%',
      summary:
        'Static router suffered 21-transaction bleed before tripping, followed by false canary recovery cycles. Loom adjusted weights continuously.',
    },
  ];

  return (
    <div className="flex flex-col gap-2.5 font-sans">
      <div className="flex items-center justify-between pb-1.5 border-b border-[#2A2D34]">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-[#E4E6EB]">
            Phase 6 benchmark comparison audit
          </span>
          <span className="text-[10px] text-[#8B8F98] hidden sm:inline">
            (Identical 150-tx outage gauntlet)
          </span>
        </div>
        <span className="text-[10px] font-mono text-[#5B8DEF] border border-[#2A2D34] px-1.5 py-0.5 bg-[#0F1115]">
          Closed-loop PID verified
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-2 text-[11px]">
        {scenarios.map((sc) => (
          <div
            key={sc.id}
            className="p-2.5 bg-[#0F1115] border border-[#2A2D34] flex flex-col justify-between gap-2"
          >
            <div>
              <div className="text-xs font-semibold text-[#E4E6EB]">{sc.title}</div>
              <div className="grid grid-cols-2 gap-2 mt-2 pt-1.5 border-t border-[#2A2D34]">
                <div className="flex flex-col">
                  <span className="text-[9.5px] text-[#8B8F98]">Loom PSR</span>
                  <span className="font-mono text-sm font-semibold text-[#5B8DEF]">{sc.loomPsr}</span>
                  <span className="text-[9px] font-mono text-[#8B8F98]">dw: {sc.loomDelta}</span>
                </div>
                <div className="flex flex-col">
                  <span className="text-[9.5px] text-[#8B8F98]">Baseline PSR</span>
                  <span className="font-mono text-sm font-medium text-[#8B8F98]">{sc.baselinePsr}</span>
                  <span className="text-[9px] font-mono text-[#8B8F98]">dw: {sc.baselineDelta}</span>
                </div>
              </div>
              <div className="mt-2 text-[10px] text-[#8B8F98] leading-relaxed">
                {sc.summary}
              </div>
            </div>

            <div className="pt-1.5 border-t border-[#2A2D34] flex items-center justify-between text-[10px]">
              <span className="text-[#8B8F98]">Loom advantage:</span>
              <span className="font-mono font-medium text-[#5B8DEF]">{sc.lift}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default BaselineComparisonCard;
