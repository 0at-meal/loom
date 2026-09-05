import React from 'react';
import { useLoomTelemetry } from './hooks/useLoomTelemetry';
import { useSimulatorControls } from './hooks/useSimulatorControls';
import { HeaderBar } from './components/HeaderBar';
import { AllocationChart } from './components/AllocationChart';
import { MetricReadouts } from './components/MetricReadouts';
import { BaselineComparisonCard } from './components/BaselineComparisonCard';
import { OperatorControls } from './components/OperatorControls';

/**
 * Loom Phase 7 Master Telemetry Dashboard
 *
 * Phase 7 Revision 4 Contract:
 * - Wordmark: Space Grotesk (weight 700) + single minimal status line
 * - Projector-Scale Headline Numbers: Space Mono (text-6xl sm:text-7xl), borderless, side-by-side
 * - Single-row acquirer strip: dot, name, single number, plain text button
 * - Zero-footprint progressive disclosure: single small text link ('Diagnostics ›')
 * - Centerpiece: Live Traffic Allocation Chart with deterministic acquirer colors and alert override
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
    setOutageActiveState,
    reconnect,
  } = useLoomTelemetry();

  const simulatorControls = useSimulatorControls({
    activeOutages,
    setOutageActiveState,
  });

  const hasActiveOutage =
    Object.values(activeOutages).some(Boolean) ||
    Object.values(acquirerStates).some((s) => s.health_score !== undefined && s.health_score < 0.70);

  return (
    <div className="min-h-screen bg-[#0F1115] text-[#E4E6EB] flex flex-col font-sans selection:bg-[#2A2D34]">
      {/* Main Mission Dashboard Body */}
      <main className="flex-1 max-w-[1400px] w-full mx-auto p-4 sm:p-6 md:p-8 flex flex-col gap-6 sm:gap-8">
        {/* Masthead: Wordmark & Single Minimal Status Line */}
        <HeaderBar
          connectionStatus={connectionStatus}
          reconnectAttempt={reconnectAttempt}
          hasActiveOutage={hasActiveOutage}
          onReconnect={reconnect}
        />

        {/* Primary Telemetry: Projector-Scale Headline Numbers & Single-Row Acquirers */}
        <section aria-label="Headline telemetry and acquirer status">
          <MetricReadouts
            metrics={metrics}
            acquirerStates={acquirerStates}
            activeOutages={activeOutages}
            submitting={simulatorControls.submitting}
            onToggleOutage={simulatorControls.handleToggleOutage}
          />
        </section>

        {/* Progressive Disclosures: Minimal text links ('Diagnostics ›' and 'Simulation settings ›') */}
        <section aria-label="Diagnostics and simulation controls" className="flex flex-wrap items-center gap-6">
          <details className="group">
            <summary className="cursor-pointer text-xs text-[#8B8F98] hover:text-[#5B8DEF] inline-flex items-center gap-1 list-none select-none transition-colors">
              <span>Diagnostics ›</span>
            </summary>
            <div className="pt-3 border-t border-[#2A2D34] mt-2 w-full">
              <BaselineComparisonCard />
            </div>
          </details>

          <details className="group">
            <summary className="cursor-pointer text-xs text-[#8B8F98] hover:text-[#5B8DEF] inline-flex items-center gap-1 list-none select-none transition-colors">
              <span>Simulation settings ›</span>
            </summary>
            <div className="pt-3 border-t border-[#2A2D34] mt-2 w-full">
              <OperatorControls
                lastActionStatus={simulatorControls.lastActionStatus}
                onClearStatus={() => simulatorControls.setLastActionStatus(null)}
                onPresetStandardCliff={simulatorControls.handlePresetStandardCliff}
                onPresetSensitiveBlip={simulatorControls.handlePresetSensitiveBlip}
                onPresetGrayFailure={simulatorControls.handlePresetGrayFailure}
                onPresetGlobalReset={simulatorControls.handlePresetGlobalReset}
              />
            </div>
          </details>
        </section>

        {/* Centerpiece: Ticket A Live Allocation Chart (Hero Composition Intact) */}
        <section aria-label="Live traffic allocation">
          <AllocationChart
            events={events}
            outageMarkers={outageMarkers}
            activeOutages={activeOutages}
            acquirerStates={acquirerStates}
            connectionStatus={connectionStatus}
            isStale={isStale}
            peakStepDelta={metrics.peakStepDelta}
          />
        </section>
      </main>

      {/* Footer Architectural Descriptors (Phase 7 Revision 5: Humanized, One Line Each, Zero Dots/Pipes) */}
      <footer className="w-full border-t border-[#2A2D34] px-4 py-4 text-[11px] text-[#8B8F98] font-sans max-w-[1400px] mx-auto flex flex-col sm:flex-row sm:items-center justify-between gap-3 leading-relaxed">
        <div className="flex flex-col gap-1">
          <span>Smooths every reroute so traffic never jumps</span>
          <span>Learns which gateway is healthiest, weighted toward the last minute</span>
        </div>
        <div className="flex flex-col gap-1 sm:text-right">
          <span>Every decision is logged, permanently</span>
          <span>Reacts to every transaction instantly</span>
        </div>
      </footer>
    </div>
  );
}

export default App;
