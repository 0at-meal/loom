import React, { useCallback, useEffect, useState, useRef } from 'react';
import { useLoomTelemetry } from './hooks/useLoomTelemetry';
import { useSimulatorControls } from './hooks/useSimulatorControls';
import { HeaderBar } from './components/HeaderBar';
import { Pane } from './components/Pane';
import { AllocationChart } from './components/AllocationChart';
import { MetricReadouts } from './components/MetricReadouts';
import { AcquirerTable } from './components/AcquirerTable';
import { EventTape } from './components/EventTape';
import { BaselineComparisonCard } from './components/BaselineComparisonCard';
import { OperatorControls } from './components/OperatorControls';
import { ToastStack } from './components/Toast';
import { KeyboardHelp } from './components/KeyboardHelp';
import { ACQUIRER_IDS, isDegraded } from './theme';

const PANES = [
  { key: 'F1', id: 'pane-kpi', label: 'KPI' },
  { key: 'F2', id: 'pane-alloc', label: 'ALLOC' },
  { key: 'F3', id: 'pane-acq', label: 'ACQ' },
  { key: 'F4', id: 'pane-tape', label: 'TAPE' },
  { key: 'F5', id: 'pane-diag', label: 'DIAG' },
  { key: 'F6', id: 'pane-ctl', label: 'CTL' },
];

const CRT_KEY = 'loom.crt';

function readCrt() {
  try {
    return window.localStorage.getItem(CRT_KEY) !== 'off';
  } catch {
    return true;
  }
}

function focusPane(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.focus({ preventScroll: true });
  el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

/**
 * Loom terminal: a multi-pane, Bloomberg-style screen over the live telemetry stream.
 * Overview first (status bar, KPIs), then allocation and acquirer detail, then the tape,
 * benchmark and controls.
 */
export function App() {
  const {
    connectionStatus,
    reconnectAttempt,
    isStale,
    events,
    outageMarkers,
    activeOutages,
    acquirerStates,
    metrics,
    stream,
    setOutageActiveState,
    reconnect,
  } = useLoomTelemetry();

  const simulatorControls = useSimulatorControls({
    activeOutages,
    setOutageActiveState,
  });

  const [crt, setCrt] = useState(readCrt);
  const [showHelp, setShowHelp] = useState(false);
  const [toasts, setToasts] = useState([]);
  const toastIdRef = useRef(1);

  useEffect(() => {
    document.documentElement.dataset.crt = crt ? 'on' : 'off';
    try {
      window.localStorage.setItem(CRT_KEY, crt ? 'on' : 'off');
    } catch {
      // storage unavailable
    }
  }, [crt]);

  const toggleCrt = useCallback(() => setCrt((v) => !v), []);

  // When simulator produces a new action status, dispatch to toast queue
  const prevActionStatusRef = useRef(null);
  useEffect(() => {
    const status = simulatorControls.lastActionStatus;
    if (status && status !== prevActionStatusRef.current) {
      const id = toastIdRef.current++;
      setToasts((prev) => [...prev, { id, ...status }].slice(-3));
    }
    prevActionStatusRef.current = status;
  }, [simulatorControls.lastActionStatus]);

  const handleDismissToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const degraded = ACQUIRER_IDS.filter((aid) => isDegraded(aid, activeOutages, acquirerStates));
  const hasActiveOutage = degraded.length > 0;

  // Keyboard handlers: F1–F6, Shift+1/2/3 presets, R reset, C crt, ? help
  const {
    handlePresetStandardCliff,
    handlePresetSensitiveBlip,
    handlePresetGrayFailure,
    handlePresetGlobalReset,
  } = simulatorControls;

  useEffect(() => {
    const onKey = (e) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const tag = e.target?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || e.target?.isContentEditable) return;

      if (e.key === '?' || (e.shiftKey && e.key === '/')) {
        e.preventDefault();
        setShowHelp((v) => !v);
        return;
      }

      const fk = /^F([1-6])$/.exec(e.key);
      if (fk) {
        e.preventDefault();
        focusPane(PANES[Number(fk[1]) - 1].id);
        return;
      }

      if (e.shiftKey) {
        const preset = { Digit1: handlePresetStandardCliff, Digit2: handlePresetSensitiveBlip, Digit3: handlePresetGrayFailure }[e.code];
        if (preset) {
          e.preventDefault();
          preset();
        }
        return;
      }

      if (/^[1-6]$/.test(e.key)) {
        focusPane(PANES[Number(e.key) - 1].id);
      } else if (e.key === 'c' || e.key === 'C') {
        toggleCrt();
      } else if ((e.key === 'r' || e.key === 'R') && handlePresetGlobalReset) {
        if (window.confirm('Reset all routes? This clears every outage and restores success rates.')) {
          handlePresetGlobalReset();
        }
      }
    };

    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [handlePresetStandardCliff, handlePresetSensitiveBlip, handlePresetGrayFailure, handlePresetGlobalReset, toggleCrt]);

  const outageStatus = hasActiveOutage ? (
    <span className="text-alert font-semibold">{degraded.length} degraded</span>
  ) : null;

  return (
    <div className="min-h-screen bg-ground text-text flex flex-col">
      {/* Skip to main content for screen readers and keyboard users */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:px-3 focus:py-1 focus:bg-amber focus:text-ground focus:font-semibold"
      >
        Skip to main content
      </a>

      {/* Floating toast notification stack */}
      <ToastStack toasts={toasts} onDismiss={handleDismissToast} />

      {/* Keyboard shortcuts modal */}
      <KeyboardHelp open={showHelp} onClose={() => setShowHelp(false)} />

      <HeaderBar
        connectionStatus={connectionStatus}
        reconnectAttempt={reconnectAttempt}
        hasActiveOutage={hasActiveOutage}
        isStale={isStale}
        stream={stream}
        onReconnect={reconnect}
        crt={crt}
        onToggleCrt={toggleCrt}
        onOpenHelp={() => setShowHelp(true)}
      />

      <main
        id="main-content"
        className="flex-1 w-full max-w-[1600px] mx-auto p-2 sm:p-3 grid grid-cols-1 lg:grid-cols-12 gap-2 auto-rows-min"
      >
        <Pane id="pane-kpi" code="F1" title="Key figures" className="lg:col-span-12">
          <MetricReadouts
            metrics={metrics}
            stream={stream}
            acquirerStates={acquirerStates}
            activeOutages={activeOutages}
          />
        </Pane>

        <Pane
          id="pane-alloc"
          code="F2"
          title="Live traffic allocation"
          status={<span>smoothed weights · {events.length} frames</span>}
          className="lg:col-span-8"
          bodyClassName="flex flex-col"
        >
          <AllocationChart
            events={events}
            outageMarkers={outageMarkers}
            activeOutages={activeOutages}
            acquirerStates={acquirerStates}
            connectionStatus={connectionStatus}
            isStale={isStale}
            stream={stream}
          />
        </Pane>

        <Pane
          id="pane-acq"
          code="F3"
          title="Acquirers"
          status={outageStatus}
          alert={hasActiveOutage}
          className="lg:col-span-4"
        >
          <AcquirerTable
            acquirerStates={acquirerStates}
            activeOutages={activeOutages}
            submitting={simulatorControls.submitting}
            onToggleOutage={simulatorControls.handleToggleOutage}
          />
          <dl className="grid grid-cols-2 gap-x-3 gap-y-0.5 px-2 py-2 border-t border-rule text-[10px] text-dim">
            <dt>Hlth</dt><dd className="text-right">health score, red below 0.700</dd>
            <dt>E[SR]</dt><dd className="text-right">expected success rate</dd>
            <dt>θ</dt><dd className="text-right">latest Thompson draw</dd>
            <dt>Tgt / Wt</dt><dd className="text-right">PID target / smoothed weight</dd>
          </dl>
        </Pane>

        <Pane
          id="pane-tape"
          code="F4"
          title="Event tape"
          status={<span>newest first</span>}
          className="lg:col-span-6"
        >
          <EventTape events={events} />
        </Pane>

        <Pane id="pane-diag" code="F5" title="Benchmark vs static baseline" className="lg:col-span-6">
          <BaselineComparisonCard />
        </Pane>

        <Pane id="pane-ctl" code="F6" title="Simulation controls" className="lg:col-span-12">
          <OperatorControls
            rates={simulatorControls.rates}
            behaviors={simulatorControls.behaviors}
            onSetRate={simulatorControls.setRate}
            onRateCommit={simulatorControls.handleRateCommit}
            onSetBehavior={simulatorControls.setBehavior}
            submitting={simulatorControls.submitting}
            lastActionStatus={simulatorControls.lastActionStatus}
            onClearStatus={() => simulatorControls.setLastActionStatus(null)}
            onPresetStandardCliff={simulatorControls.handlePresetStandardCliff}
            onPresetSensitiveBlip={simulatorControls.handlePresetSensitiveBlip}
            onPresetGrayFailure={simulatorControls.handlePresetGrayFailure}
            onPresetGlobalReset={simulatorControls.handlePresetGlobalReset}
          />
        </Pane>
      </main>

      {/* Function-key footer */}
      <footer className="sticky bottom-0 w-full border-t border-rule bg-pane">
        <nav aria-label="Panes" className="flex flex-wrap items-center gap-1 px-2 py-1 text-[10px] uppercase tracking-[0.08em]">
          {PANES.map((p) => (
            <button
              key={p.key}
              type="button"
              onClick={() => focusPane(p.id)}
              className="flex items-center gap-1 pr-1.5 border border-rule hover:border-amber cursor-pointer"
            >
              <span className="px-1 bg-amber text-ground font-semibold">{p.key}</span>
              <span className="text-text">{p.label}</span>
            </button>
          ))}
          <span className="ml-auto text-dim normal-case tracking-normal hidden md:inline">
            <span className="text-amber font-semibold cursor-pointer" onClick={() => setShowHelp(true)}>?</span> shortcuts · ⇧1–3 presets · R reset · C crt
          </span>
        </nav>
      </footer>
    </div>
  );
}

export default App;
