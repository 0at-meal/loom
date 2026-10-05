import React from 'react';
import { command, comparisons, configs, n_seeds as nSeeds } from '../data/baselineComparison.json';

/**
 * Baseline Comparison Card
 *
 * Shows the paired multi-seed benchmark from `scripts/compare_psr.py`. Every number is read
 * from `dashboard/src/data/baselineComparison.json`, which that script writes:
 *
 *   python scripts/compare_psr.py --n-seeds 100 --out-json dashboard/src/data/baselineComparison.json
 *
 * Revised contract:
 * - Monospace strictly for numeric values and percentages
 * - IBM Plex Sans for descriptions in sentence case
 * - Palette: Panel (#16181D), Border (#2A2D34), Accent (#5B8DEF)
 * - Zero drop shadows
 */
const SCENARIOS = [
  { key: 'loom_vs_m1', title: 'Hard outage, static M=1' },
  { key: 'loom_vs_m3', title: 'Hard outage, static M=3' },
  { key: 'loom_vs_m3_gray', title: 'Gray failure (Alpha at 60%), static M=3' },
];

const pct = (value) => `${value.toFixed(2)}%`;
const signed = (value) => `${value >= 0 ? '+' : '−'}${Math.abs(value).toFixed(2)}`;
const pp = (value) => `${signed(value)} pp`;

export function BaselineComparisonCard() {
  return (
    <div className="flex flex-col gap-2.5 font-sans">
      <div className="flex items-center justify-between pb-1.5 border-b border-[#2A2D34]">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-[#E4E6EB]">Benchmark vs static baseline</span>
          <span className="text-[10px] text-[#8B8F98] hidden sm:inline">
            (150-tx outage scenario, {nSeeds} paired seeds)
          </span>
        </div>
        <span className="text-[10px] font-mono text-[#8B8F98] border border-[#2A2D34] px-1.5 py-0.5 bg-[#0F1115]">
          n={nSeeds}
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-2 text-[11px]">
        {SCENARIOS.map(({ key, title }) => {
          const cmp = comparisons[key];
          const loom = configs[cmp.first];
          const baseline = configs[cmp.second];
          return (
            <div
              key={key}
              className="p-2.5 bg-[#0F1115] border border-[#2A2D34] flex flex-col justify-between gap-2"
            >
              <div>
                <div className="text-xs font-semibold text-[#E4E6EB]">{title}</div>
                <div className="grid grid-cols-2 gap-2 mt-2 pt-1.5 border-t border-[#2A2D34]">
                  <div className="flex flex-col">
                    <span className="text-[9.5px] text-[#8B8F98]">Loom mean PSR</span>
                    <span className="font-mono text-sm font-medium text-[#E4E6EB]">
                      {pct(loom.mean_psr_pct)}
                    </span>
                  </div>
                  <div className="flex flex-col">
                    <span className="text-[9.5px] text-[#8B8F98]">Static mean PSR</span>
                    <span className="font-mono text-sm font-medium text-[#E4E6EB]">
                      {pct(baseline.mean_psr_pct)}
                    </span>
                  </div>
                </div>
              </div>

              <div className="pt-1.5 border-t border-[#2A2D34] flex flex-col gap-0.5 text-[10px]">
                <div className="flex items-center justify-between">
                  <span className="text-[#8B8F98]">Loom − static, mean</span>
                  <span className="font-mono text-[#E4E6EB]">{pp(cmp.mean_diff_pp)}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-[#8B8F98]">95% CI</span>
                  <span className="font-mono text-[#E4E6EB]">
                    [{signed(cmp.ci95_low_pp)}, {signed(cmp.ci95_high_pp)}] pp
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-[#8B8F98]">Loom wins / ties / losses</span>
                  <span className="font-mono text-[#E4E6EB]">
                    {cmp.wins} / {cmp.ties} / {cmp.losses}
                  </span>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <p className="text-[10px] text-[#8B8F98] leading-relaxed">
        Simulated acquirers with unlimited backup capacity; both routers count issuer declines
        as acquirer failures. Source: <span className="font-mono">{command}</span>
      </p>
    </div>
  );
}

export default BaselineComparisonCard;
