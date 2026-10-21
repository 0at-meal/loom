import React from 'react';
import { command, comparisons, configs, n_seeds as nSeeds } from '../data/baselineComparison.json';

/**
 * Benchmark vs static baseline, as a dense table (the body of the F5 pane).
 *
 * Every number is read from `dashboard/src/data/baselineComparison.json`, which
 * `scripts/compare_psr.py` writes:
 *
 *   python scripts/compare_psr.py --n-seeds 100 --out-json dashboard/src/data/baselineComparison.json
 */
const SCENARIOS = [
  { key: 'loom_vs_m1', title: 'Hard outage', baseline: 'M=1' },
  { key: 'loom_vs_m3', title: 'Hard outage', baseline: 'M=3' },
  { key: 'loom_vs_m3_gray', title: 'Gray failure 60%', baseline: 'M=3' },
];

const pct = (value) => `${value.toFixed(2)}%`;
const signed = (value) => `${value >= 0 ? '+' : '−'}${Math.abs(value).toFixed(2)}`;

export function BaselineComparisonCard() {
  return (
    <div className="flex flex-col">
      <div className="overflow-x-auto">
        <table className="w-full text-[11px] border-collapse">
          <thead>
            <tr className="text-[10px] uppercase tracking-[0.1em] text-dim border-b border-rule">
              <th className="px-2 py-1 text-left font-normal">Scenario</th>
              <th className="px-2 py-1 text-right font-normal">Loom</th>
              <th className="px-2 py-1 text-right font-normal">Static</th>
              <th className="px-2 py-1 text-right font-normal">Δ pp</th>
              <th className="px-2 py-1 text-right font-normal">95% CI</th>
              <th className="px-2 py-1 text-right font-normal" title="Loom wins / ties / losses">W/T/L</th>
            </tr>
          </thead>
          <tbody>
            {SCENARIOS.map(({ key, title, baseline }) => {
              const cmp = comparisons[key];
              const loom = configs[cmp.first];
              const stat = configs[cmp.second];
              const tone = cmp.ci95_low_pp > 0 ? 'text-ok' : cmp.ci95_high_pp < 0 ? 'text-alert' : 'text-text';
              return (
                <tr key={key} className="border-b border-rule last:border-b-0">
                  <td className="px-2 py-1.5 whitespace-nowrap">
                    <span className="text-text">{title}</span>
                    <span className="text-dim"> vs {baseline}</span>
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-amber">{pct(loom.mean_psr_pct)}</td>
                  <td className="px-2 py-1.5 text-right font-mono text-text">{pct(stat.mean_psr_pct)}</td>
                  <td className={`px-2 py-1.5 text-right font-mono ${tone}`}>{signed(cmp.mean_diff_pp)}</td>
                  <td className="px-2 py-1.5 text-right font-mono text-dim whitespace-nowrap">
                    [{signed(cmp.ci95_low_pp)}, {signed(cmp.ci95_high_pp)}]
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-text whitespace-nowrap">
                    {cmp.wins}/{cmp.ties}/{cmp.losses}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="px-2 py-1.5 border-t border-rule text-[10px] text-dim leading-relaxed">
        Mean PSR over {nSeeds} paired seeds, 150-tx outage scenario. Simulated acquirers with
        unlimited backup capacity; both routers count issuer declines as acquirer failures.
        Source: <span className="font-mono text-cream break-all">{command}</span>
      </p>
    </div>
  );
}

export default BaselineComparisonCard;
