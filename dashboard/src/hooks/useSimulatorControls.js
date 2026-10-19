import { useState, useCallback, useRef } from 'react';

/**
 * useSimulatorControls
 *
 * Manages operator interaction with the simulated acquirer endpoints via the backend proxy:
 * - Per-acquirer outage toggles (applied locally only after the simulator confirms, with in-flight locking)
 * - Per-acquirer failure behaviors (RETURN_DECLINE, HTTP_503, LATENCY_SPIKE)
 * - Per-acquirer gray-failure base success rates (sliders)
 * - Global benchmark scenario gauntlet presets (Standard Cliff, Sensitive Blip M=1, Gray Failure, Reset)
 */
export function useSimulatorControls({ activeOutages = {}, setOutageActiveState } = {}) {
  const [behaviors, setBehaviors] = useState({
    acquirer_alpha: 'RETURN_DECLINE',
    acquirer_beta: 'RETURN_DECLINE',
    acquirer_gamma: 'RETURN_DECLINE',
  });

  const [rates, setRates] = useState({
    acquirer_alpha: 0.95,
    acquirer_beta: 0.90,
    acquirer_gamma: 0.85,
  });

  // Last rates the simulator confirmed; a failed commit puts the slider back here.
  const committedRates = useRef({
    acquirer_alpha: 0.95,
    acquirer_beta: 0.90,
    acquirer_gamma: 0.85,
  });

  const [submitting, setSubmitting] = useState({});
  const [lastActionStatus, setLastActionStatus] = useState(null);

  const acquirers = [
    { id: 'acquirer_alpha', name: 'Acquirer Alpha', role: 'Primary leader', baseRate: 0.95 },
    { id: 'acquirer_beta', name: 'Acquirer Beta', role: 'Secondary backup', baseRate: 0.90 },
    { id: 'acquirer_gamma', name: 'Acquirer Gamma', role: 'Tertiary floor', baseRate: 0.85 },
  ];

  // Toggle outage on a single acquirer
  const handleToggleOutage = useCallback(
    async (acquirerId) => {
      const isCurrentlyOut = !!activeOutages[acquirerId];
      const nextActive = !isCurrentlyOut;
      const currentBehavior = behaviors[acquirerId] || 'RETURN_DECLINE';

      setSubmitting((prev) => ({ ...prev, [acquirerId]: true }));
      setLastActionStatus({
        type: 'info',
        text: `Dispatching ${nextActive ? 'outage' : 'clear'} to ${acquirerId}...`,
      });

      try {
        const response = await fetch(`/api/simulator/acquirers/${acquirerId}/outage`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            active: nextActive,
            behavior: currentBehavior,
            transition_seconds: 0.0,
          }),
        });

        if (!response.ok) {
          const errorText = await response.text();
          console.warn(`Simulator outage proxy response: ${response.status} - ${errorText}`);
          setLastActionStatus({
            type: 'warning',
            text: `Outage ${nextActive ? 'not armed' : 'not cleared'} on ${acquirerId}: proxy returned ${response.status}.`,
          });
        } else {
          if (setOutageActiveState) {
            setOutageActiveState(acquirerId, nextActive);
          }
          setLastActionStatus({
            type: 'success',
            text: `${acquirerId} outage ${nextActive ? 'armed' : 'cleared'} successfully.`,
          });
        }
      } catch (err) {
        console.warn('Network error reaching simulator proxy:', err);
        setLastActionStatus({
          type: 'warning',
          text: `Router unreachable; outage ${nextActive ? 'not armed' : 'not cleared'} on ${acquirerId}.`,
        });
      } finally {
        setSubmitting((prev) => ({ ...prev, [acquirerId]: false }));
      }
    },
    [activeOutages, behaviors, setOutageActiveState]
  );

  // Commit base success rate change (gray failure slider)
  const handleRateCommit = useCallback(
    async (acquirerId, newRate) => {
      setSubmitting((prev) => ({ ...prev, [`rate_${acquirerId}`]: true }));
      try {
        const response = await fetch(`/api/simulator/acquirers/${acquirerId}/success-rate`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            success_rate: newRate,
            reason: 'Operator gray failure test',
          }),
        });

        if (response.ok) {
          committedRates.current[acquirerId] = newRate;
          setLastActionStatus({
            type: 'success',
            text: `${acquirerId} base rate set to ${(newRate * 100).toFixed(0)}%.`,
          });
        } else {
          const errorText = await response.text();
          console.warn(`Simulator success-rate proxy response: ${response.status} - ${errorText}`);
          setRates((prev) => ({ ...prev, [acquirerId]: committedRates.current[acquirerId] }));
          setLastActionStatus({
            type: 'warning',
            text: `${acquirerId} base rate not changed: proxy returned ${response.status}.`,
          });
        }
      } catch (err) {
        console.warn('Network error setting success rate:', err);
        setRates((prev) => ({ ...prev, [acquirerId]: committedRates.current[acquirerId] }));
        setLastActionStatus({
          type: 'warning',
          text: `Router unreachable; ${acquirerId} base rate not changed.`,
        });
      } finally {
        setSubmitting((prev) => ({ ...prev, [`rate_${acquirerId}`]: false }));
      }
    },
    []
  );

  // Update behavior mode for an acquirer
  const setBehavior = useCallback((acquirerId, mode) => {
    setBehaviors((prev) => ({ ...prev, [acquirerId]: mode }));
  }, []);

  // Update slider rate locally for an acquirer
  const setRate = useCallback((acquirerId, val) => {
    setRates((prev) => ({ ...prev, [acquirerId]: val }));
  }, []);

  // Preset 1: Standard Cliff (Step Outage on Alpha)
  const handlePresetStandardCliff = useCallback(async () => {
    setLastActionStatus({ type: 'info', text: 'Executing Preset 1: Standard cliff (Alpha outage)...' });
    if (!activeOutages.acquirer_alpha) {
      await handleToggleOutage('acquirer_alpha');
    }
  }, [activeOutages.acquirer_alpha, handleToggleOutage]);

  // Preset 2: Sensitive Blip (3.5-second transient outage on Alpha)
  const handlePresetSensitiveBlip = useCallback(async () => {
    setLastActionStatus({ type: 'info', text: 'Executing Preset 2: Sensitive blip (Transient Alpha outage)...' });
    if (!activeOutages.acquirer_alpha) {
      await handleToggleOutage('acquirer_alpha');
      setTimeout(async () => {
        await handleToggleOutage('acquirer_alpha');
      }, 3500);
    }
  }, [activeOutages.acquirer_alpha, handleToggleOutage]);

  // Preset 3: Gray Failure (Partial degradation p=0.60 on Alpha)
  const handlePresetGrayFailure = useCallback(async () => {
    setLastActionStatus({ type: 'info', text: 'Executing Preset 3: Gray failure (Alpha at 60% PSR)...' });
    setRates((prev) => ({ ...prev, acquirer_alpha: 0.60 }));
    await handleRateCommit('acquirer_alpha', 0.60);
    if (activeOutages.acquirer_alpha) {
      await handleToggleOutage('acquirer_alpha');
    }
  }, [activeOutages.acquirer_alpha, handleRateCommit, handleToggleOutage]);

  // Preset 4: Global Reset
  const handlePresetGlobalReset = useCallback(async () => {
    setLastActionStatus({ type: 'info', text: 'Executing global simulator reset...' });

    for (const acq of acquirers) {
      if (activeOutages[acq.id]) {
        await handleToggleOutage(acq.id);
      }
    }

    try {
      const response = await fetch('/api/simulator/admin/reset', { method: 'POST' });
      if (!response.ok) {
        console.warn(`Simulator reset proxy response: ${response.status} - ${await response.text()}`);
        setLastActionStatus({
          type: 'warning',
          text: `Simulator reset failed: proxy returned ${response.status}.`,
        });
        return;
      }
    } catch (err) {
      console.warn('Failed to call reset endpoint:', err);
      setLastActionStatus({ type: 'warning', text: 'Router unreachable; simulator not reset.' });
      return;
    }

    const defaults = { acquirer_alpha: 0.95, acquirer_beta: 0.90, acquirer_gamma: 0.85 };
    committedRates.current = { ...defaults };
    setRates(defaults);
    setLastActionStatus({ type: 'success', text: 'All simulator routes and telemetry states reset.' });
  }, [acquirers, activeOutages, handleToggleOutage]);

  return {
    acquirers,
    behaviors,
    rates,
    submitting,
    lastActionStatus,
    setLastActionStatus,
    setBehavior,
    setRate,
    handleToggleOutage,
    handleRateCommit,
    handlePresetStandardCliff,
    handlePresetSensitiveBlip,
    handlePresetGrayFailure,
    handlePresetGlobalReset,
  };
}
