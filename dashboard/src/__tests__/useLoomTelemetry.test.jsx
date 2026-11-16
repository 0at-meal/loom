import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useLoomTelemetry } from '../hooks/useLoomTelemetry';

class FakeSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances = [];

  constructor(url) {
    this.url = url;
    this.readyState = FakeSocket.CONNECTING;
    this.sent = [];
    this.closeCalls = 0;
    FakeSocket.instances.push(this);
  }

  send(data) {
    this.sent.push(data);
  }

  close() {
    this.closeCalls += 1;
    this.readyState = FakeSocket.CLOSED;
    // Browsers fire the close event later, to whatever handler is attached then
    queueMicrotask(() => this.onclose?.());
  }

  // Test helpers: what the server does
  open() {
    this.readyState = FakeSocket.OPEN;
    this.onopen?.();
  }

  message(frame) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }

  drop() {
    this.readyState = FakeSocket.CLOSED;
    this.onclose?.();
  }
}

const render = () =>
  renderHook(() => useLoomTelemetry({ WebSocketImpl: FakeSocket, url: 'ws://test/ws/telemetry' }));

describe('useLoomTelemetry lifecycle', () => {
  beforeEach(() => {
    FakeSocket.instances = [];
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('reconnects after the socket drops', () => {
    const { result } = render();
    const first = FakeSocket.instances[0];
    act(() => first.open());
    expect(result.current.connectionStatus).toBe('connected');

    act(() => first.drop());
    expect(result.current.connectionStatus).toBe('reconnecting');
    expect(result.current.reconnectAttempt).toBe(1);
    act(() => vi.advanceTimersByTime(1000));
    expect(FakeSocket.instances).toHaveLength(2);
  });

  it('schedules nothing after unmount and detaches the socket handlers', async () => {
    const { unmount } = render();
    const ws = FakeSocket.instances[0];
    act(() => ws.open());
    unmount();
    await Promise.resolve();
    expect(ws.closeCalls).toBe(1);
    expect(ws.onclose).toBeNull();
    expect(ws.onmessage).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
    act(() => vi.advanceTimersByTime(60000));
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it('cancels a pending reconnect on unmount', () => {
    const { unmount } = render();
    act(() => FakeSocket.instances[0].drop());
    expect(vi.getTimerCount()).toBeGreaterThan(0);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
    act(() => vi.advanceTimersByTime(60000));
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it('places HEALTH_ALERT markers at the latest frame (no stale closure)', () => {
    const { result } = render();
    const ws = FakeSocket.instances[0];
    act(() => ws.open());
    act(() => {
      for (let seq = 1; seq <= 5; seq += 1) {
        ws.message({
          event_type: 'ROUTING_COMPLETED',
          stream_id: 's',
          sequence_number: seq,
          selected_acquirer: 'acquirer_alpha',
          authorized: true,
          status: 'AUTHORIZED',
          smoothed_allocation: { acquirer_alpha: 0.9, acquirer_beta: 0.1 },
        });
      }
      ws.message({ event_type: 'HEALTH_ALERT', acquirer_id: 'acquirer_alpha', severity: 'CRITICAL' });
    });
    expect(result.current.events).toHaveLength(5);
    expect(result.current.outageMarkers).toHaveLength(1);
    expect(result.current.outageMarkers[0].index).toBe(4);
  });

  it('reports dropped frames from skipped sequence numbers', () => {
    const { result } = render();
    const ws = FakeSocket.instances[0];
    act(() => ws.open());
    act(() => {
      for (const seq of [1, 2, 5]) {
        ws.message({
          event_type: 'ROUTING_COMPLETED',
          stream_id: 's',
          sequence_number: seq,
          selected_acquirer: 'acquirer_beta',
          authorized: true,
          status: 'AUTHORIZED',
          smoothed_allocation: {},
        });
      }
    });
    expect(result.current.stream.droppedFrames).toBe(2);
  });
});
