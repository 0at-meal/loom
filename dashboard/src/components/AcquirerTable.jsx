import React from 'react';
import { ACQUIRERS, COLORS, isDegraded } from '../theme';

const num = (v, digits) => (typeof v === 'number' ? v.toFixed(digits) : null);
const pct = (v) => (typeof v === 'number' ? `${(v * 100).toFixed(1)}%` : null);

function Cell({ value, color, className = '' }) {
  return (
    <td className={`px-2 py-1.5 text-right font-mono whitespace-nowrap ${className}`} style={{ color: value === null ? COLORS.dim : color }}>
      {value === null ? 'no data' : value}
    </td>
  );
}

/**
 * Acquirer board: one row per acquirer with what the router believes about it (health,
 * expected success rate, Beta posterior alpha/beta), its latest Thompson draw, the PID
 * target and the smoothed weight actually used, plus the outage toggle. Cells the stream
 * has not provided read "no data".
 */
export function AcquirerTable({ acquirerStates = {}, activeOutages = {}, submitting = {}, onToggleOutage }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[11px] border-collapse">
        <thead>
          <tr className="text-[10px] uppercase tracking-[0.1em] text-dim border-b border-rule">
            <th className="px-2 py-1 text-left font-normal">Acq</th>
            <th className="px-2 py-1 text-right font-normal" title="Health score">Hlth</th>
            <th className="px-2 py-1 text-right font-normal" title="Expected success rate">E[SR]</th>
            <th className="px-2 py-1 text-right font-normal hidden xl:table-cell" title="Posterior alpha / beta">α / β</th>
            <th className="px-2 py-1 text-right font-normal" title="Latest Thompson sample">θ</th>
            <th className="px-2 py-1 text-right font-normal" title="PID target allocation">Tgt</th>
            <th className="px-2 py-1 text-right font-normal" title="Smoothed allocation in use">Wt</th>
            <th className="px-2 py-1 text-right font-normal">Ctl</th>
          </tr>
        </thead>
        <tbody>
          {ACQUIRERS.map((acq) => {
            const s = acquirerStates[acq.id] || {};
            const bad = isDegraded(acq.id, activeOutages, acquirerStates);
            const color = bad ? COLORS.alert : acq.color;
            const pending = !!submitting[acq.id];
            const ab =
              typeof s.alpha === 'number' && typeof s.beta === 'number'
                ? `${s.alpha.toFixed(1)} / ${s.beta.toFixed(1)}`
                : null;
            return (
              <tr key={acq.id} className={`border-b border-rule last:border-b-0 ${bad ? 'bg-alert/10' : ''}`}>
                <td className="px-2 py-1.5 whitespace-nowrap">
                  <span className="inline-flex items-center gap-1.5">
                    <span className={`inline-block w-2 h-2 ${bad ? 'lamp-blink' : ''}`} style={{ backgroundColor: color }} />
                    <span className="font-semibold tracking-[0.08em]" style={{ color }}>
                      {acq.ticker}
                    </span>
                  </span>
                </td>
                <Cell value={num(s.health_score, 3)} color={color} />
                <Cell value={pct(s.expected_success_rate)} color={COLORS.text} />
                <Cell value={ab} color={COLORS.dim} className="hidden xl:table-cell" />
                <Cell value={num(s.sample, 3)} color={COLORS.text} />
                <Cell value={pct(s.target)} color={COLORS.dim} />
                <Cell value={pct(s.weight)} color={color} />
                <td className="px-2 py-1.5 text-right">
                  <button
                    type="button"
                    disabled={pending}
                    onClick={() => onToggleOutage && onToggleOutage(acq.id)}
                    className={`px-1.5 border uppercase text-[10px] tracking-[0.08em] disabled:opacity-50 disabled:cursor-not-allowed ${
                      bad
                        ? 'border-alert text-alert hover:bg-alert hover:text-ground'
                        : 'border-rule text-dim hover:border-amber hover:text-amber'
                    }`}
                  >
                    {pending ? 'dispatching...' : bad ? 'clear outage' : 'trigger outage'}
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default AcquirerTable;
