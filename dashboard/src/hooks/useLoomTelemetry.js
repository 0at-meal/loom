import { useState, useEffect, useRef, useCallback, useReducer } from 'react';
import { initialTelemetryState, telemetryReducer } from './telemetryState';

const STALE_AFTER_MS = 5000;
const PING_EVERY_MS = 15000;

export function telemetryUrl(location = window.location) {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  // Under the Vite dev server (port 5173) talk to the router on 8000 directly
  const host = location.port === '5173' ? '127.0.0.1:8000' : location.host;
  return `${protocol}//${host}/ws/telemetry`;
}

/** Reconnect delay: 0.5 s growing 1.5x per attempt, capped at 10 s, plus up to 0.3 s jitter. */
export function reconnectDelayMs(attempt, random = Math.random) {
  return Math.min(10000, 500 * Math.pow(1.5, attempt)) + random() * 300;
}

/**
 * WebSocket connection to the router's /ws/telemetry stream.
 *
 * Frames go through telemetryReducer (pure, see telemetryState.js), so handlers never read
 * React state from a closure. On unmount the socket's handlers are detached and every
 * timer is cleared, so nothing reconnects after the component is gone.
 */
export function useLoomTelemetry({ WebSocketImpl, url } = {}) {
  const [connectionStatus, setConnectionStatus] = useState('connecting');
  const [reconnectAttempt, setReconnectAttempt] = useState(0);
  const [lastEventTime, setLastEventTime] = useState(null);
  const [isStale, setIsStale] = useState(false);
  const [state, dispatch] = useReducer(telemetryReducer, initialTelemetryState);

  const wsRef = useRef(null);
  const reconnectTimerRef = useRef(null);
  const pingTimerRef = useRef(null);
  const attemptRef = useRef(0);
  const mountedRef = useRef(false);

  // Stale stream detection: connected but no frame for STALE_AFTER_MS
  useEffect(() => {
    const interval = setInterval(() => {
      if (connectionStatus !== 'connected') {
        setIsStale(true);
      } else if (lastEventTime) {
        setIsStale(Date.now() - lastEventTime > STALE_AFTER_MS);
      }
    }, 1000);
    return () => clearInterval(interval);
  }, [lastEventTime, connectionStatus]);

  const clearTimers = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (pingTimerRef.current) {
      clearInterval(pingTimerRef.current);
      pingTimerRef.current = null;
    }
  }, []);

  const detachSocket = useCallback(() => {
    const ws = wsRef.current;
    wsRef.current = null;
    if (ws) {
      ws.onopen = null;
      ws.onmessage = null;
      ws.onclose = null;
      ws.onerror = null;
      try {
        ws.close();
      } catch {
        // already closed
      }
    }
  }, []);

  const connect = useCallback(() => {
    if (!mountedRef.current) return;
    const Impl = WebSocketImpl || window.WebSocket;
    const current = wsRef.current;
    if (current && (current.readyState === Impl.OPEN || current.readyState === Impl.CONNECTING)) {
      return;
    }

    let ws;
    try {
      ws = new Impl(url || telemetryUrl());
    } catch {
      setConnectionStatus('disconnected');
      return;
    }
    wsRef.current = ws;

    ws.onopen = () => {
      if (wsRef.current !== ws) return;
      attemptRef.current = 0;
      setConnectionStatus('connected');
      setReconnectAttempt(0);
      setIsStale(false);
      if (pingTimerRef.current) clearInterval(pingTimerRef.current);
      pingTimerRef.current = setInterval(() => {
        if (ws.readyState === Impl.OPEN) ws.send('ping');
      }, PING_EVERY_MS);
    };

    ws.onmessage = (messageEvent) => {
      if (wsRef.current !== ws) return;
      let frame;
      try {
        frame = JSON.parse(messageEvent.data);
      } catch (err) {
        console.warn('Failed to parse WebSocket telemetry frame:', err);
        return;
      }
      const now = Date.now();
      setLastEventTime(now);
      setIsStale(false);
      dispatch({ type: 'frame', frame, receivedAt: now });
    };

    ws.onclose = () => {
      if (wsRef.current !== ws || !mountedRef.current) return;
      wsRef.current = null;
      if (pingTimerRef.current) {
        clearInterval(pingTimerRef.current);
        pingTimerRef.current = null;
      }
      setConnectionStatus('reconnecting');
      setIsStale(true);
      const attempt = attemptRef.current;
      attemptRef.current = attempt + 1;
      setReconnectAttempt(attempt + 1);
      reconnectTimerRef.current = setTimeout(() => {
        reconnectTimerRef.current = null;
        connect();
      }, reconnectDelayMs(attempt));
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [WebSocketImpl, url]);

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      clearTimers();
      detachSocket();
    };
  }, [connect, clearTimers, detachSocket]);

  const reconnect = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    attemptRef.current = 0;
    connect();
  }, [connect]);

  // Outage flag set by the operator controls, after the router confirmed the change
  const setOutageActiveState = useCallback((acquirerId, isActive) => {
    dispatch({ type: 'manual_outage', acquirerId, active: isActive });
  }, []);

  return {
    connectionStatus,
    reconnectAttempt,
    lastEventTime,
    isStale,
    events: state.events,
    outageMarkers: state.markers,
    activeOutages: state.activeOutages,
    acquirerStates: state.acquirerStates,
    metrics: state.metrics,
    stream: state.stream,
    setOutageActiveState,
    reconnect,
  };
}
