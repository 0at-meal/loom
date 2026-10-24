import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { AllocationChart, baselineOverlay } from '../components/AllocationChart';
import { MetricReadouts } from '../components/MetricReadouts';
import { AcquirerTable } from '../components/AcquirerTable';
import { EventTape } from '../components/EventTape';
import reference from '../data/baselineReferenceRun.json';
import { applyFrame, initialTelemetryState } from '../hooks/telemetryState';

afterEach(cleanup);

const frame = (seq, weights, extra = {}) => ({
  event_type: 'ROUTING_COMPLETED',
  stream_id: 's',
  sequence_number: seq,
  selected_acquirer: 'acquirer_alpha',
  authorized: true,
  status: 'AUTHORIZED',
  smoothed_allocation: weights,
  ...extra,
});

const stateFrom = (frames) => frames.reduce((s, f) => applyFrame(s, f, 0), initialTelemetryState);

describe('AllocationChart', () => {
  it('shows no data instead of made-up weights', () => {
    const s = stateFrom([frame(1, { acquirer_beta: 0.4 })]);
    const { container } = render(
      <AllocationChart events={s.events} outageMarkers={s.markers} stream={s.stream} />,
    );
    expect(screen.getAllByText('no data').length).toBeGreaterThanOrEqual(2); // alpha, gamma
    expect(screen.getByText('40.0%')).toBeTruthy();
    expect(container.textContent).not.toMatch(/82\.0%|15\.0%|3\.0%/);
  });

  it('shows the empty state before any frame', () => {
    render(<AllocationChart events={[]} />);
    expect(screen.getByText('Awaiting telemetry stream')).toBeTruthy();
    expect(screen.getAllByText('no data').length).toBe(3); // three weights
  });

  it('marks dropped frames and restarts', () => {
    const s = stateFrom([
      frame(1, { acquirer_alpha: 0.5 }),
      frame(4, { acquirer_alpha: 0.5 }),
      frame(1, { acquirer_alpha: 0.5 }, { stream_id: 's2' }),
    ]);
    render(<AllocationChart events={s.events} outageMarkers={s.markers} stream={s.stream} />);
    expect(screen.getByTestId('stream-gaps').textContent).toMatch(/2 frames dropped/);
    expect(screen.getByTestId('stream-gaps').textContent).toMatch(/router restarted/);
    expect(screen.getAllByTestId('stream-break')).toHaveLength(2);
    expect(screen.getAllByTestId('marker-restart')).toHaveLength(1);
  });

  it('draws only markers whose frame is in view', () => {
    const markers = [
      { id: 'old', index: 0, type: 'outage', acquirerId: 'acquirer_alpha', label: 'OLD' },
      { id: 'none', index: null, type: 'outage', acquirerId: 'acquirer_beta', label: 'NONE' },
      { id: 'new', index: 11, type: 'outage', acquirerId: 'acquirer_alpha', label: 'NEW' },
    ];
    const events = Array.from({ length: 5 }, (_, k) => ({
      index: 10 + k,
      weights: { acquirer_alpha: 0.5 },
      missingBefore: 0,
      restartBefore: false,
    }));
    render(<AllocationChart events={events} outageMarkers={markers} />);
    expect(screen.getAllByTestId('marker-outage')).toHaveLength(1);
    expect(screen.getByText('NEW')).toBeTruthy();
  });
});

describe('baselineOverlay', () => {
  const events = Array.from({ length: 30 }, (_, k) => ({ index: k }));
  const marker = { type: 'outage', acquirerId: 'acquirer_alpha', index: 10 };

  it('replays the recorded run from the outage marker', () => {
    const points = baselineOverlay(events, [marker]);
    const start = reference.outage_trigger_index;
    expect(points[0]).toEqual({ i: 6, weight: reference.transactions[start - 4].alpha_weight });
    for (const p of points) {
      expect(p.weight).toBe(reference.transactions[start + (p.i - 10)].alpha_weight);
    }
    // The recorded breaker absorbs three failures, then sends Alpha nothing
    expect(points.find((p) => p.i === 12).weight).toBe(1);
    expect(points.find((p) => p.i === 13).weight).toBe(0);
  });

  it('draws nothing without an Alpha outage marker in view', () => {
    expect(baselineOverlay(events, [])).toEqual([]);
    expect(baselineOverlay(events, [{ ...marker, acquirerId: 'acquirer_beta' }])).toEqual([]);
    expect(baselineOverlay(events, [{ ...marker, index: null }])).toEqual([]);
  });

  it('stops where the recording ends', () => {
    const long = Array.from({ length: 200 }, (_, k) => ({ index: k }));
    const points = baselineOverlay(long, [{ ...marker, index: 0 }]);
    expect(points).toHaveLength(reference.total_transactions - reference.outage_trigger_index);
  });
});

describe('MetricReadouts', () => {
  it('shows placeholders, not numbers, before telemetry arrives', () => {
    render(<MetricReadouts metrics={initialTelemetryState.metrics} />);
    expect(screen.getByText('--.-%')).toBeTruthy();
    // lifetime PSR, declined share, peak step
    expect(screen.getAllByText('no data')).toHaveLength(3);
    expect(screen.getAllByText('--')).toHaveLength(3); // routed, declined, errors
    expect(screen.queryByText('0')).toBeNull();
  });

  it('shows live figures and flags dropped frames next to PSR', () => {
    render(
      <MetricReadouts
        metrics={{ rollingPSR: 92.5, lifetimePSR: 90, totalCount: 40, declinedCount: 4, errorCount: 0, peakStepDelta: 6.2 }}
        stream={{ droppedFrames: 3 }}
      />,
    );
    expect(screen.getByText('92.5%')).toBeTruthy();
    expect(screen.getByText('90.00%')).toBeTruthy();
    expect(screen.getByText('10.0% of routed')).toBeTruthy();
    expect(screen.getByText('6.2%')).toBeTruthy();
    expect(screen.getByText(/3 frames dropped/)).toBeTruthy();
  });
});

describe('AcquirerTable', () => {
  it('shows no data for every cell before telemetry arrives', () => {
    render(<AcquirerTable acquirerStates={{}} />);
    // 3 acquirers x 6 data columns
    expect(screen.getAllByText('no data')).toHaveLength(18);
    expect(screen.queryByText('1.000')).toBeNull();
  });

  it('shows live state from frames and only that', () => {
    const s = stateFrom([
      frame(1, { acquirer_alpha: 0.82 }, {
        thompson_samples: { acquirer_alpha: 0.913 },
        target_allocation: { acquirer_alpha: 0.9 },
        updated_state: { health_score: 0.731, expected_success_rate: 0.95, alpha: 19, beta: 1 },
      }),
    ]);
    render(<AcquirerTable acquirerStates={s.acquirerStates} />);
    expect(screen.getByText('0.731')).toBeTruthy();
    expect(screen.getByText('0.913')).toBeTruthy();
    expect(screen.getByText('90.0%')).toBeTruthy();
    expect(screen.getByText('82.0%')).toBeTruthy();
    expect(screen.getByText('95.0%')).toBeTruthy();
    // beta and gamma were not in the frame: 2 rows x 6 columns
    expect(screen.getAllByText('no data')).toHaveLength(12);
  });

  it('labels the outage control for a degraded acquirer', () => {
    render(
      <AcquirerTable
        acquirerStates={{ acquirer_beta: { health_score: 0.5 } }}
        activeOutages={{ acquirer_alpha: true }}
      />,
    );
    expect(screen.getAllByText('clear outage')).toHaveLength(2);
    expect(screen.getAllByText('trigger outage')).toHaveLength(1);
  });
});

describe('EventTape', () => {
  it('says no data before any frame', () => {
    render(<EventTape events={[]} />);
    expect(screen.getByText(/no data/)).toBeTruthy();
  });

  it('lists frames newest first and marks gaps and restarts', () => {
    const s = stateFrom([
      frame(1, {}, { total_latency_ms: 12.34 }),
      frame(4, {}, { authorized: false, status: 'DECLINED', decline_code: 'ACQUIRER_OUTAGE' }),
      frame(1, {}, { stream_id: 's2' }),
    ]);
    render(<EventTape events={s.events} />);
    const rows = screen.getByTestId('event-tape').querySelectorAll('tbody tr');
    expect(rows[0].textContent).toMatch(/AUTHORIZED/);
    expect(rows[1].textContent).toMatch(/router restarted/);
    expect(rows[2].textContent).toMatch(/DECLINED.*ACQUIRER_OUTAGE/);
    expect(rows[3].textContent).toMatch(/2 frames dropped/);
    expect(rows[4].textContent).toMatch(/12\.3/);
    expect(screen.getAllByTestId('tape-break')).toHaveLength(2);
  });
});
