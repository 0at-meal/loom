import { describe, expect, it } from 'vitest';
import {
  MAX_EVENTS,
  applyFrame,
  initialTelemetryState,
  telemetryReducer,
  trackSequence,
  initialStream,
} from '../hooks/telemetryState';

const routing = (seq, extra = {}) => ({
  event_type: 'ROUTING_COMPLETED',
  stream_id: 'run-1',
  sequence_number: seq,
  timestamp: 1000 + seq,
  selected_acquirer: 'acquirer_alpha',
  status: 'AUTHORIZED',
  authorized: true,
  decline_code: null,
  smoothed_allocation: { acquirer_alpha: 0.9, acquirer_beta: 0.1 },
  updated_state: { health_score: 0.98, alpha: 10, beta: 1 },
  ...extra,
});

const feed = (frames, state = initialTelemetryState) =>
  frames.reduce((s, f) => applyFrame(s, f, 0), state);

describe('no fabricated values', () => {
  it('starts with nothing to show', () => {
    const s = initialTelemetryState;
    expect(s.acquirerStates).toEqual({});
    expect(s.metrics.rollingPSR).toBeNull();
    expect(s.metrics.lifetimePSR).toBeNull();
    expect(s.metrics.peakStepDelta).toBeNull();
    expect(JSON.stringify(s)).not.toMatch(/0\.82|0\.15|0\.042|22\.5/);
  });

  it('keeps a missing weight missing', () => {
    const s = feed([routing(1, { smoothed_allocation: { acquirer_beta: 0.2 } })]);
    expect(s.events[0].weights).toEqual({ acquirer_beta: 0.2 });
    expect(s.acquirerStates.acquirer_alpha.weight).toBeUndefined();
    const none = feed([routing(1, { smoothed_allocation: null })]);
    expect(none.events[0].weights).toBeNull();
  });

  it('computes PSR only from received frames', () => {
    const s = feed([routing(1), routing(2, { authorized: false, status: 'DECLINED' })]);
    expect(s.metrics.totalCount).toBe(2);
    expect(s.metrics.lifetimePSR).toBe(50);
    expect(s.metrics.rollingPSR).toBe(50);
    expect(s.metrics.declinedCount).toBe(1);
  });
});

describe('sequence numbers', () => {
  it('treats the first frame as the start, not a gap', () => {
    const r = trackSequence(initialStream, 57, 'run-1');
    expect(r.missing).toBe(0);
    expect(r.stream.lastSeq).toBe(57);
    expect(r.stream.droppedFrames).toBe(0);
  });

  it('counts skipped numbers as dropped frames and marks the gap', () => {
    const s = feed([routing(1), routing(2), routing(6), routing(7), routing(10)]);
    expect(s.stream.droppedFrames).toBe(5);
    expect(s.stream.gaps).toBe(2);
    expect(s.events.map((e) => e.missingBefore)).toEqual([0, 0, 3, 0, 2]);
  });

  it('does not compute a step delta across a gap', () => {
    const s = feed([
      routing(1, { smoothed_allocation: { acquirer_alpha: 0.9, acquirer_beta: 0.1 } }),
      routing(5, { smoothed_allocation: { acquirer_alpha: 0.2, acquirer_beta: 0.8 } }),
    ]);
    expect(s.metrics.peakStepDelta).toBeNull();
    const t = feed([routing(6, { smoothed_allocation: { acquirer_alpha: 0.1, acquirer_beta: 0.9 } })], s);
    expect(t.metrics.peakStepDelta).toBeCloseTo(10, 6);
  });

  it('detects a restart from a new stream id', () => {
    const s = feed([routing(40), routing(41), routing(1, { stream_id: 'run-2' })]);
    expect(s.stream.restarts).toBe(1);
    expect(s.stream.droppedFrames).toBe(0);
    expect(s.stream.lastSeq).toBe(1);
    expect(s.events[2].restartBefore).toBe(true);
    expect(s.markers.some((m) => m.type === 'restart' && m.index === 2)).toBe(true);
  });

  it('detects a restart when the number goes backwards without a stream id', () => {
    const frames = [routing(40), routing(41), routing(3)].map((f) => ({ ...f, stream_id: undefined }));
    const s = feed(frames);
    expect(s.stream.restarts).toBe(1);
    expect(s.stream.droppedFrames).toBe(0);
  });

  it('counts frames missed while disconnected from the bootstrap number', () => {
    const boot = (seq, id = 'run-1') => ({
      event_type: 'BOOTSTRAP',
      stream_id: id,
      sequence_number: seq,
      states: { acquirer_alpha: { health_score: 0.5 } },
    });
    let s = feed([boot(0), routing(1), routing(2)]);
    s = feed([boot(9), routing(10)], s);
    expect(s.stream.droppedFrames).toBe(7);
    expect(s.stream.restarts).toBe(0);
    expect(s.acquirerStates.acquirer_alpha.health_score).toBe(0.98);

    const restarted = feed([boot(0, 'run-2'), routing(1, { stream_id: 'run-2' })], s);
    expect(restarted.stream.restarts).toBe(1);
    expect(restarted.stream.droppedFrames).toBe(7);
  });
});

describe('markers', () => {
  it('anchor HEALTH_ALERT markers to the newest frame, not index 0', () => {
    let s = feed([routing(1), routing(2), routing(3)]);
    s = applyFrame(s, {
      event_type: 'HEALTH_ALERT',
      acquirer_id: 'acquirer_alpha',
      severity: 'CRITICAL',
      old_health: 1,
      new_health: 0,
    });
    expect(s.activeOutages.acquirer_alpha).toBe(true);
    expect(s.markers).toHaveLength(1);
    expect(s.markers[0].index).toBe(2);
  });

  it('merge an operator trigger and its HEALTH_ALERT into one marker', () => {
    let s = feed([routing(1)]);
    s = telemetryReducer(s, { type: 'manual_outage', acquirerId: 'acquirer_alpha', active: true });
    s = applyFrame(s, {
      event_type: 'HEALTH_ALERT',
      acquirer_id: 'acquirer_alpha',
      severity: 'CRITICAL',
    });
    expect(s.markers.filter((m) => m.type === 'outage')).toHaveLength(1);
  });

  it('are dropped once their frame scrolls out of view', () => {
    let s = feed([routing(1, { decline_code: 'ACQUIRER_OUTAGE', authorized: false, status: 'DECLINED' })]);
    expect(s.markers).toHaveLength(1);
    const more = Array.from({ length: MAX_EVENTS }, (_, k) => routing(k + 2));
    s = feed(more, s);
    expect(s.events).toHaveLength(MAX_EVENTS);
    expect(s.markers).toHaveLength(0);
  });

  it('have no position before any frame has arrived', () => {
    const s = telemetryReducer(initialTelemetryState, {
      type: 'manual_outage',
      acquirerId: 'acquirer_beta',
      active: true,
    });
    expect(s.activeOutages.acquirer_beta).toBe(true);
    expect(s.markers[0].index).toBeNull();
  });
});
