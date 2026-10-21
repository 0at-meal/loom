/**
 * Pure state for the telemetry stream (AUDIT F-27).
 *
 * Every number here comes from a frame the router sent; nothing is filled in when data is
 * missing. Values the stream has not provided yet are null, and components render them as
 * "no data".
 *
 * Frames are numbered by the router in the order they are published, starting again at 1
 * when the router process restarts. Each process numbers its frames under its own
 * stream_id. A skipped number means frames were dropped (the router drops the oldest
 * queued frame for a slow client, and a disconnected client misses frames); a new
 * stream_id, or a number that goes backwards, means the router restarted.
 */

export const MAX_EVENTS = 120;
export const ROLLING_WINDOW = 50;
// Markers of the same kind on the same acquirer closer than this are one event
const MARKER_DEDUPE_FRAMES = 15;

export const initialStream = {
  streamId: null,
  lastSeq: null,
  droppedFrames: 0,
  gaps: 0,
  restarts: 0,
};

export const initialMetrics = {
  totalCount: 0,
  authorizedCount: 0,
  declinedCount: 0,
  errorCount: 0,
  rollingPSR: null,
  lifetimePSR: null,
  peakStepDelta: null,
};

export const initialTelemetryState = {
  // Local frame counter: every received ROUTING_COMPLETED frame gets the next index, so
  // chart positions and markers do not depend on the router's numbering.
  nextIndex: 0,
  events: [],
  markers: [],
  activeOutages: {},
  acquirerStates: {},
  metrics: initialMetrics,
  rollingWindow: [],
  prevAllocation: null,
  stream: initialStream,
};

const isObject = (v) => v !== null && typeof v === 'object';

const shortName = (acquirerId) => String(acquirerId).replace('acquirer_', '').toUpperCase();

/**
 * Update the stream bookkeeping for one numbered frame.
 *
 * Returns the new stream state, how many frames are missing just before this one, and
 * whether this frame starts a new numbering (router restart).
 */
export function trackSequence(stream, seq, streamId) {
  const hasSeq = Number.isInteger(seq) && seq >= 0;
  const knownStream = stream.streamId;
  if (streamId && knownStream && streamId !== knownStream) {
    return {
      stream: { ...stream, streamId, lastSeq: hasSeq ? seq : null, restarts: stream.restarts + 1 },
      missing: 0,
      restarted: true,
    };
  }
  const next = { ...stream, streamId: streamId || knownStream };
  if (!hasSeq) {
    return { stream: next, missing: 0, restarted: false };
  }
  if (stream.lastSeq === null) {
    // First numbered frame: joining mid-stream is not a gap
    return { stream: { ...next, lastSeq: seq }, missing: 0, restarted: false };
  }
  if (seq <= stream.lastSeq) {
    // Numbering went backwards: an older router without stream_id restarted
    return {
      stream: { ...next, lastSeq: seq, restarts: stream.restarts + 1 },
      missing: 0,
      restarted: true,
    };
  }
  const missing = seq - stream.lastSeq - 1;
  return {
    stream: {
      ...next,
      lastSeq: seq,
      droppedFrames: stream.droppedFrames + missing,
      gaps: stream.gaps + (missing > 0 ? 1 : 0),
    },
    missing,
    restarted: false,
  };
}

function addMarker(markers, marker) {
  const duplicate = markers.some(
    (m) =>
      m.type === marker.type &&
      m.acquirerId === marker.acquirerId &&
      m.index !== null &&
      marker.index !== null &&
      Math.abs(m.index - marker.index) < MARKER_DEDUPE_FRAMES,
  );
  return duplicate ? markers : [...markers, marker];
}

function pruneMarkers(markers, events) {
  if (events.length === 0) return markers;
  const oldest = events[0].index;
  return markers.filter((m) => m.index === null || m.index >= oldest);
}

/** Index of the newest frame, or null before the first one: markers anchor to it. */
function lastIndex(state) {
  return state.events.length > 0 ? state.events[state.events.length - 1].index : null;
}

function applyBootstrap(state, frame) {
  const states = frame.states && typeof frame.states === 'object' ? frame.states : {};
  const acquirerStates = { ...state.acquirerStates };
  for (const [aid, snap] of Object.entries(states)) {
    acquirerStates[aid] = { ...(acquirerStates[aid] || {}), ...snap };
  }

  let { stream } = state;
  let { markers } = state;
  const seq = frame.sequence_number;
  const knownStream = stream.streamId;
  if (frame.stream_id && knownStream && frame.stream_id !== knownStream) {
    stream = {
      ...stream,
      streamId: frame.stream_id,
      lastSeq: Number.isInteger(seq) ? seq : null,
      restarts: stream.restarts + 1,
    };
    markers = addMarker(markers, {
      id: `restart-${state.nextIndex}`,
      index: lastIndex(state),
      type: 'restart',
      acquirerId: null,
      label: 'ROUTER RESTARTED',
    });
  } else if (Number.isInteger(seq)) {
    stream = { ...stream, streamId: frame.stream_id || knownStream };
    if (stream.lastSeq === null) {
      stream.lastSeq = seq;
    } else if (seq > stream.lastSeq) {
      // Frames published while this client was disconnected
      const missing = seq - stream.lastSeq;
      stream = {
        ...stream,
        lastSeq: seq,
        droppedFrames: stream.droppedFrames + missing,
        gaps: stream.gaps + 1,
      };
    } else if (seq < stream.lastSeq) {
      stream = { ...stream, lastSeq: seq, restarts: stream.restarts + 1 };
    }
  }
  return { ...state, acquirerStates, stream, markers };
}

function applyRouting(state, frame, receivedAt) {
  const { stream, missing, restarted } = trackSequence(
    state.stream,
    frame.sequence_number,
    frame.stream_id,
  );
  const index = state.nextIndex;
  const selected = frame.selected_acquirer;
  const smoothed =
    frame.smoothed_allocation && typeof frame.smoothed_allocation === 'object'
      ? frame.smoothed_allocation
      : null;
  const authorized = frame.authorized === true;

  // Largest change in any acquirer's weight since the previous frame. Not computed across
  // a gap or a restart, where the previous frame is not the previous step.
  let stepDelta = null;
  if (smoothed && state.prevAllocation && missing === 0 && !restarted) {
    for (const [aid, w] of Object.entries(smoothed)) {
      const prev = state.prevAllocation[aid];
      if (typeof w === 'number' && typeof prev === 'number') {
        const d = Math.abs(w - prev);
        stepDelta = stepDelta === null ? d : Math.max(stepDelta, d);
      }
    }
  }

  const rollingWindow = [...state.rollingWindow, authorized ? 1 : 0].slice(-ROLLING_WINDOW);
  const rollingSuccess = rollingWindow.reduce((a, b) => a + b, 0);
  const m = state.metrics;
  const totalCount = m.totalCount + 1;
  const authorizedCount = m.authorizedCount + (authorized ? 1 : 0);
  const peakPct = stepDelta === null ? m.peakStepDelta : Math.max(m.peakStepDelta ?? 0, stepDelta * 100);
  const metrics = {
    totalCount,
    authorizedCount,
    declinedCount: m.declinedCount + (frame.status === 'DECLINED' ? 1 : 0),
    errorCount: m.errorCount + (frame.status === 'ERROR' ? 1 : 0),
    rollingPSR: (rollingSuccess / rollingWindow.length) * 100,
    lifetimePSR: (authorizedCount / totalCount) * 100,
    peakStepDelta: peakPct,
  };

  const acquirerStates = { ...state.acquirerStates };
  if (selected && frame.updated_state && typeof frame.updated_state === 'object') {
    acquirerStates[selected] = { ...(acquirerStates[selected] || {}), ...frame.updated_state };
  }
  if (smoothed) {
    for (const [aid, w] of Object.entries(smoothed)) {
      acquirerStates[aid] = { ...(acquirerStates[aid] || {}), weight: w };
    }
  }
  // Latest Thompson draw and PID target per acquirer, only as sent in this frame
  const samples = isObject(frame.thompson_samples) ? frame.thompson_samples : null;
  const target = isObject(frame.target_allocation) ? frame.target_allocation : null;
  for (const [aid, v] of Object.entries(samples || {})) {
    if (typeof v === 'number') acquirerStates[aid] = { ...(acquirerStates[aid] || {}), sample: v };
  }
  for (const [aid, v] of Object.entries(target || {})) {
    if (typeof v === 'number') acquirerStates[aid] = { ...(acquirerStates[aid] || {}), target: v };
  }

  const event = {
    index,
    seq: Number.isInteger(frame.sequence_number) ? frame.sequence_number : null,
    timestamp: typeof frame.timestamp === 'number' ? frame.timestamp : receivedAt / 1000,
    selected,
    authorized,
    status: frame.status,
    declineCode: frame.decline_code ?? null,
    latencyMs: typeof frame.total_latency_ms === 'number' ? frame.total_latency_ms : null,
    weights: smoothed,
    missingBefore: missing,
    restartBefore: restarted,
  };
  const events = [...state.events, event].slice(-MAX_EVENTS);

  let markers = state.markers;
  let activeOutages = state.activeOutages;
  if (restarted) {
    markers = addMarker(markers, {
      id: `restart-${index}`,
      index,
      type: 'restart',
      acquirerId: null,
      label: 'ROUTER RESTARTED',
    });
  }
  if (frame.decline_code === 'ACQUIRER_OUTAGE' && selected) {
    activeOutages = { ...activeOutages, [selected]: true };
    markers = addMarker(markers, {
      id: `outage-${selected}-${index}`,
      index,
      type: 'outage',
      acquirerId: selected,
      label: `OUTAGE: ${shortName(selected)}${event.seq !== null ? ` (Tx #${event.seq})` : ''}`,
    });
  }

  return {
    ...state,
    nextIndex: index + 1,
    events,
    markers: pruneMarkers(markers, events),
    activeOutages,
    acquirerStates,
    metrics,
    rollingWindow,
    prevAllocation: smoothed ?? state.prevAllocation,
    stream,
  };
}

function applyOutageFlag(state, acquirerId, active, label) {
  if (!acquirerId) return state;
  const type = active ? 'outage' : 'recovery';
  const index = lastIndex(state);
  return {
    ...state,
    activeOutages: { ...state.activeOutages, [acquirerId]: active },
    markers: addMarker(state.markers, {
      id: `${type}-${acquirerId}-${state.nextIndex}-${state.markers.length}`,
      index,
      type,
      acquirerId,
      label: `${label}: ${shortName(acquirerId)}`,
    }),
  };
}

function applyHealthAlert(state, frame) {
  const critical =
    frame.severity === 'CRITICAL' || (frame.new_health < 0.7 && frame.old_health >= 0.7);
  const recovery = frame.severity === 'INFO' || (frame.new_health >= 0.7 && frame.old_health < 0.7);
  if (critical) return applyOutageFlag(state, frame.acquirer_id, true, 'OUTAGE');
  if (recovery) return applyOutageFlag(state, frame.acquirer_id, false, 'RECOVERY');
  return state;
}

/** Apply one parsed WebSocket frame. Unknown event types leave the state unchanged. */
export function applyFrame(state, frame, receivedAt = Date.now()) {
  if (!frame || typeof frame !== 'object') return state;
  switch (frame.event_type) {
    case 'BOOTSTRAP':
      return applyBootstrap(state, frame);
    case 'ROUTING_COMPLETED':
      return applyRouting(state, frame, receivedAt);
    case 'HEALTH_ALERT':
      return applyHealthAlert(state, frame);
    default:
      return state;
  }
}

export function telemetryReducer(state, action) {
  switch (action.type) {
    case 'frame':
      return applyFrame(state, action.frame, action.receivedAt);
    case 'manual_outage':
      return applyOutageFlag(
        state,
        action.acquirerId,
        action.active,
        action.active ? 'OUTAGE TRIGGERED' : 'OUTAGE CLEARED',
      );
    default:
      return state;
  }
}
