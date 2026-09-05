import { useState, useEffect, useRef, useCallback } from 'react';

/**
 * Custom hook managing the real-time WebSocket connection to Phase 5's Redis Pub/Sub stream.
 *
 * Invariants:
 * - Matches real update cadence without artificial throttling
 * - Explicit visible reconnecting / disconnected states
 * - Stale data detection after 5 seconds of silence
 * - Tracks rolling PSR, lifetime PSR, peak allocation delta (dw_max), and outage markers
 */
export function useLoomTelemetry() {
  const [connectionStatus, setConnectionStatus] = useState('connecting'); // 'connected' | 'connecting' | 'reconnecting' | 'disconnected'
  const [reconnectAttempt, setReconnectAttempt] = useState(0);
  const [lastEventTime, setLastEventTime] = useState(null);
  const [isStale, setIsStale] = useState(false);

  // Time-series history for Ticket A allocation chart (max 120 items)
  const [events, setEvents] = useState([]);

  // Pinned outage/recovery event markers
  const [outageMarkers, setOutageMarkers] = useState([]);

  // Active outage flags per acquirer (acquirer_id -> boolean)
  const [activeOutages, setActiveOutages] = useState({
    acquirer_alpha: false,
    acquirer_beta: false,
    acquirer_gamma: false,
  });

  // Current per-acquirer belief and health snapshots
  const [acquirerStates, setAcquirerStates] = useState({
    acquirer_alpha: { health_score: 1.0, alpha: 1.0, beta: 1.0, expected_success_rate: 0.95, weight: 0.82 },
    acquirer_beta: { health_score: 1.0, alpha: 1.0, beta: 1.0, expected_success_rate: 0.90, weight: 0.15 },
    acquirer_gamma: { health_score: 1.0, alpha: 1.0, beta: 1.0, expected_success_rate: 0.85, weight: 0.03 },
  });

  // Running metrics
  const [metrics, setMetrics] = useState({
    totalCount: 0,
    authorizedCount: 0,
    declinedCount: 0,
    errorCount: 0,
    rollingPSR: 100.0,
    lifetimePSR: 100.0,
    peakStepDelta: 0.0,
    avgRoutingLatency: 0.042,
    avgAcquirerLatency: 22.5,
  });

  const wsRef = useRef(null);
  const reconnectTimerRef = useRef(null);
  const pingTimerRef = useRef(null);
  const prevAllocRef = useRef({});
  const rollingWindowRef = useRef([]); // sliding window of boolean success for last 50 txs

  // Stale stream detection: checks if connected but no event received in 5s
  useEffect(() => {
    const interval = setInterval(() => {
      if (lastEventTime && connectionStatus === 'connected') {
        const elapsed = Date.now() - lastEventTime;
        setIsStale(elapsed > 5000);
      } else if (connectionStatus !== 'connected') {
        setIsStale(true);
      }
    }, 1000);
    return () => clearInterval(interval);
  }, [lastEventTime, connectionStatus]);

  // Connect / Reconnect function
  const connect = useCallback(() => {
    if (wsRef.current && (wsRef.current.readyState === WebSocket.OPEN || wsRef.current.readyState === WebSocket.CONNECTING)) {
      return;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    // When running under Vite dev server on port 5173, point directly to backend 8000 or use Vite proxy
    const host = window.location.port === '5173' ? '127.0.0.1:8000' : window.location.host;
    const wsUrl = `${protocol}//${host}/ws/telemetry`;

    try {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnectionStatus('connected');
        setReconnectAttempt(0);
        setIsStale(false);

        // Periodic ping to keep socket alive
        if (pingTimerRef.current) clearInterval(pingTimerRef.current);
        pingTimerRef.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send('ping');
          }
        }, 15000);
      };

      ws.onmessage = (messageEvent) => {
        try {
          const data = JSON.parse(messageEvent.data);
          const now = Date.now();
          setLastEventTime(now);
          setIsStale(false);

          if (data.event_type === 'BOOTSTRAP') {
            if (data.states) {
              setAcquirerStates((prev) => ({
                ...prev,
                ...data.states,
              }));
            }
          } else if (data.event_type === 'ROUTING_COMPLETED') {
            // 1. Process routing transaction
            const seq = data.sequence_number || 0;
            const smoothed = data.smoothed_allocation || {};
            const selected = data.selected_acquirer;
            const authorized = data.authorized === true || data.status === 'AUTHORIZED';
            const isOutage = data.decline_code === 'ACQUIRER_OUTAGE';

            // Calculate peak allocation delta (|w_t - w_t-1|)
            let stepDelta = 0;
            if (prevAllocRef.current[selected] !== undefined && smoothed[selected] !== undefined) {
              stepDelta = Math.abs(smoothed[selected] - prevAllocRef.current[selected]);
            }
            prevAllocRef.current = { ...smoothed };

            // Update rolling 50-tx window
            rollingWindowRef.current.push(authorized ? 1 : 0);
            if (rollingWindowRef.current.length > 50) {
              rollingWindowRef.current.shift();
            }
            const rollingSuccess = rollingWindowRef.current.reduce((a, b) => a + b, 0);
            const currentRollingPSR = rollingWindowRef.current.length > 0
              ? (rollingSuccess / rollingWindowRef.current.length) * 100
              : 100.0;

            // Update metrics
            setMetrics((prev) => {
              const newTotal = prev.totalCount + 1;
              const newAuth = prev.authorizedCount + (authorized ? 1 : 0);
              const newDeclined = prev.declinedCount + (!authorized && data.status === 'DECLINED' ? 1 : 0);
              const newError = prev.errorCount + (data.status === 'ERROR' ? 1 : 0);
              const newLifetimePSR = (newAuth / newTotal) * 100;
              const newPeakJump = Math.max(prev.peakStepDelta, stepDelta * 100);

              return {
                totalCount: newTotal,
                authorizedCount: newAuth,
                declinedCount: newDeclined,
                errorCount: newError,
                rollingPSR: currentRollingPSR,
                lifetimePSR: newLifetimePSR,
                peakStepDelta: Math.round(newPeakJump * 100) / 100,
                avgRoutingLatency: data.routing_latency_ms || prev.avgRoutingLatency,
                avgAcquirerLatency: data.acquirer_latency_ms || prev.avgAcquirerLatency,
              };
            });

            // Update per-acquirer state
            if (data.updated_state && selected) {
              setAcquirerStates((prev) => ({
                ...prev,
                [selected]: {
                  ...prev[selected],
                  ...data.updated_state,
                  weight: smoothed[selected] !== undefined ? smoothed[selected] : prev[selected]?.weight,
                },
              }));
            }

            // Append to events history (max 120 points for live chart)
            const eventPoint = {
              seq,
              timestamp: data.timestamp || now / 1000,
              selected,
              authorized,
              status: data.status,
              decline_code: data.decline_code,
              smoothed: smoothed,
              weights: {
                acquirer_alpha: smoothed.acquirer_alpha ?? 0.82,
                acquirer_beta: smoothed.acquirer_beta ?? 0.15,
                acquirer_gamma: smoothed.acquirer_gamma ?? 0.03,
              },
            };

            setEvents((prev) => {
              const next = [...prev, eventPoint];
              return next.length > 120 ? next.slice(next.length - 120) : next;
            });

            // If an outage decline was observed on an acquirer, pin an outage marker if not already pinned recently
            if (isOutage) {
              setActiveOutages((prev) => ({ ...prev, [selected]: true }));
              setOutageMarkers((prev) => {
                const alreadyPinned = prev.some((m) => m.acquirer_id === selected && m.type === 'outage' && Math.abs(m.seq - seq) < 15);
                if (!alreadyPinned) {
                  return [
                    ...prev,
                    {
                      id: `outage-${selected}-${seq}`,
                      seq,
                      timestamp: data.timestamp || now / 1000,
                      acquirer_id: selected,
                      type: 'outage',
                      label: `OUTAGE: ${selected.replace('acquirer_', '').toUpperCase()} (Tx #${seq})`,
                    },
                  ];
                }
                return prev;
              });
            }
          } else if (data.event_type === 'HEALTH_ALERT') {
            const acquirerId = data.acquirer_id;
            const isCritical = data.severity === 'CRITICAL' || (data.new_health < 0.70 && data.old_health >= 0.70);
            const isRecovery = data.severity === 'INFO' || (data.new_health >= 0.70 && data.old_health < 0.70);

            if (isCritical) {
              setActiveOutages((prev) => ({ ...prev, [acquirerId]: true }));
              setOutageMarkers((prev) => [
                ...prev,
                {
                  id: `alert-outage-${acquirerId}-${Date.now()}`,
                  seq: events.length > 0 ? events[events.length - 1].seq : 0,
                  timestamp: data.timestamp,
                  acquirer_id: acquirerId,
                  type: 'outage',
                  label: `OUTAGE: ${acquirerId.replace('acquirer_', '').toUpperCase()}`,
                },
              ]);
            } else if (isRecovery) {
              setActiveOutages((prev) => ({ ...prev, [acquirerId]: false }));
              setOutageMarkers((prev) => [
                ...prev,
                {
                  id: `alert-recovery-${acquirerId}-${Date.now()}`,
                  seq: events.length > 0 ? events[events.length - 1].seq : 0,
                  timestamp: data.timestamp,
                  acquirer_id: acquirerId,
                  type: 'recovery',
                  label: `RECOVERY: ${acquirerId.replace('acquirer_', '').toUpperCase()}`,
                },
              ]);
            }
          }
        } catch (err) {
          console.warn('Failed to parse WebSocket telemetry frame:', err);
        }
      };

      ws.onclose = () => {
        setConnectionStatus('reconnecting');
        setIsStale(true);
        if (pingTimerRef.current) clearInterval(pingTimerRef.current);

        // Exponential backoff with jitter: min(10s, 0.5s * 2^attempt)
        setReconnectAttempt((attempt) => {
          const nextAttempt = attempt + 1;
          const delay = Math.min(10000, 500 * Math.pow(1.5, attempt)) + Math.random() * 300;
          reconnectTimerRef.current = setTimeout(connect, delay);
          return nextAttempt;
        });
      };

      ws.onerror = () => {
        ws.close();
      };
    } catch (err) {
      setConnectionStatus('disconnected');
    }
  }, []);

  useEffect(() => {
    connect();
    return () => {
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      if (pingTimerRef.current) clearInterval(pingTimerRef.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, [connect]);

  // Manual reset of outage flag from Ticket C
  const setOutageActiveState = useCallback((acquirerId, isActive) => {
    setActiveOutages((prev) => ({ ...prev, [acquirerId]: isActive }));
    if (isActive) {
      setOutageMarkers((prev) => [
        ...prev,
        {
          id: `manual-outage-${acquirerId}-${Date.now()}`,
          seq: events.length > 0 ? events[events.length - 1].seq : 0,
          timestamp: Date.now() / 1000,
          acquirer_id: acquirerId,
          type: 'outage',
          label: `OUTAGE TRIGGERED: ${acquirerId.replace('acquirer_', '').toUpperCase()}`,
        },
      ]);
    } else {
      setOutageMarkers((prev) => [
        ...prev,
        {
          id: `manual-recovery-${acquirerId}-${Date.now()}`,
          seq: events.length > 0 ? events[events.length - 1].seq : 0,
          timestamp: Date.now() / 1000,
          acquirer_id: acquirerId,
          type: 'recovery',
          label: `OUTAGE CLEARED: ${acquirerId.replace('acquirer_', '').toUpperCase()}`,
        },
      ]);
    }
  }, [events]);

  return {
    connectionStatus,
    reconnectAttempt,
    lastEventTime,
    isStale,
    events,
    outageMarkers,
    activeOutages,
    acquirerStates,
    metrics,
    setOutageActiveState,
    reconnect: connect,
  };
}
