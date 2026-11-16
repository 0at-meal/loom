import { cleanup, render, screen, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { EventTape } from '../components/EventTape';
import { Pane } from '../components/Pane';
import { ToastStack } from '../components/Toast';
import { KeyboardHelp } from '../components/KeyboardHelp';
import { OperatorControls } from '../components/OperatorControls';
import { AllocationChart } from '../components/AllocationChart';
import { MetricReadouts } from '../components/MetricReadouts';
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

describe('UX Enhancements - EventTape', () => {
  it('filters events by status and expands snapshot row on click', () => {
    const s = stateFrom([
      frame(1, { acquirer_alpha: 0.8 }, { authorized: true, status: 'AUTHORIZED' }),
      frame(2, { acquirer_alpha: 0.2 }, { authorized: false, status: 'DECLINED', decline_code: 'ACQUIRER_OUTAGE' }),
    ]);
    render(<EventTape events={s.events} />);

    // Click DECLINED filter chip
    const declinedChip = screen.getByRole('button', { name: 'DECLINED' });
    fireEvent.click(declinedChip);

    // Should only have 1 data row now
    const tableBody = screen.getByTestId('event-tape').querySelector('tbody');
    expect(tableBody.querySelectorAll('tr')).toHaveLength(1);
    expect(tableBody.textContent).toMatch(/DECLINED/);
    expect(tableBody.textContent).not.toMatch(/AUTHORIZED/);

    // Click row to expand snapshot details
    fireEvent.click(tableBody.querySelector('tr'));
    expect(screen.getByText(/Snapshot Tx #2/)).toBeTruthy();
    expect(screen.getByText(/ALPHA:/)).toBeTruthy();
  });
});

describe('UX Enhancements - KeyboardHelp', () => {
  it('renders modal when open and handles close triggers', () => {
    const handleClose = vi.fn();
    const { rerender } = render(<KeyboardHelp open={false} onClose={handleClose} />);
    expect(screen.queryByText('Keyboard shortcuts')).toBeNull();

    rerender(<KeyboardHelp open={true} onClose={handleClose} />);
    expect(screen.getByText('Keyboard shortcuts')).toBeTruthy();
    expect(screen.getByText('F1 – F6')).toBeTruthy();

    // Close button
    const closeBtn = screen.getByRole('button', { name: 'Close' });
    fireEvent.click(closeBtn);
    expect(handleClose).toHaveBeenCalledTimes(1);

    // Escape key
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(handleClose).toHaveBeenCalledTimes(2);
  });
});

describe('UX Enhancements - ToastStack', () => {
  it('renders notifications and allows dismissal', () => {
    const handleDismiss = vi.fn();
    const toasts = [
      { id: 1, type: 'success', text: 'Alpha outage armed' },
      { id: 2, type: 'warning', text: 'Proxy error' },
    ];
    render(<ToastStack toasts={toasts} onDismiss={handleDismiss} />);
    expect(screen.getByText('Alpha outage armed')).toBeTruthy();
    expect(screen.getByText('Proxy error')).toBeTruthy();

    const dismissButtons = screen.getAllByRole('button', { name: 'Dismiss notification' });
    expect(dismissButtons).toHaveLength(2);
    fireEvent.click(dismissButtons[0]);
    expect(handleDismiss).toHaveBeenCalledWith(1);
  });
});

describe('UX Enhancements - Pane', () => {
  it('toggles body visibility when chevron is clicked', () => {
    render(
      <Pane code="F9" title="Test Pane">
        <div data-testid="pane-content">Content Visible</div>
      </Pane>
    );
    expect(screen.getByTestId('pane-content')).toBeTruthy();

    const chevron = screen.getByRole('button', { name: 'Collapse Test Pane' });
    fireEvent.click(chevron);
    expect(screen.queryByTestId('pane-content')).toBeNull();

    const expandChevron = screen.getByRole('button', { name: 'Expand Test Pane' });
    fireEvent.click(expandChevron);
    expect(screen.getByTestId('pane-content')).toBeTruthy();
  });
});

describe('UX Enhancements - OperatorControls', () => {
  it('expands advanced controls with sliders and selectors', () => {
    const handleSetRate = vi.fn();
    const handleSetBehavior = vi.fn();

    render(
      <OperatorControls
        rates={{ acquirer_alpha: 0.95 }}
        behaviors={{ acquirer_alpha: 'RETURN_DECLINE' }}
        onSetRate={handleSetRate}
        onSetBehavior={handleSetBehavior}
      />
    );

    // Initially advanced controls are hidden
    expect(screen.queryByLabelText('ALPHA base success rate')).toBeNull();

    // Toggle advanced section
    const toggleBtn = screen.getByText(/Advanced acquirer controls/);
    fireEvent.click(toggleBtn);

    // Controls now visible
    const alphaSlider = screen.getByLabelText('ALPHA base success rate');
    expect(alphaSlider).toBeTruthy();
    expect(alphaSlider.value).toBe('95');

    fireEvent.change(alphaSlider, { target: { value: '60' } });
    expect(handleSetRate).toHaveBeenCalledWith('acquirer_alpha', 0.6);

    const behaviorSelect = screen.getByLabelText('ALPHA failure behavior');
    expect(behaviorSelect.value).toBe('RETURN_DECLINE');
    fireEvent.change(behaviorSelect, { target: { value: 'HTTP_503' } });
    expect(handleSetBehavior).toHaveBeenCalledWith('acquirer_alpha', 'HTTP_503');
  });
});

describe('UX Enhancements - MetricReadouts', () => {
  it('renders per-acquirer breakdown and delta arrow', () => {
    render(
      <MetricReadouts
        metrics={{
          rollingPSR: 88.0,
          previousRollingPSR: 85.0,
          totalCount: 50,
          perAcquirerRolling: {
            acquirer_alpha: 92.0,
            acquirer_beta: 86.0,
          },
        }}
      />
    );
    expect(screen.getByText('↑ +3.0 pp')).toBeTruthy();
    expect(screen.getByText('92.0%')).toBeTruthy();
    expect(screen.getByText('86.0%')).toBeTruthy();
  });
});

describe('UX Enhancements - AllocationChart', () => {
  it('navigates crosshair using arrow keys', () => {
    const events = [
      { index: 1, seq: 10, weights: { acquirer_alpha: 0.8 }, timestamp: 1000 },
      { index: 2, seq: 11, weights: { acquirer_alpha: 0.7 }, timestamp: 1001 },
    ];
    render(<AllocationChart events={events} />);

    const chartWell = screen.getByLabelText(/Live traffic allocation chart/);
    fireEvent.keyDown(chartWell, { key: 'ArrowLeft' });

    // Crosshair tooltip should now be visible
    expect(screen.getByText(/Seq #11/)).toBeTruthy();

    fireEvent.keyDown(chartWell, { key: 'ArrowLeft' });
    expect(screen.getByText(/Seq #10/)).toBeTruthy();

    fireEvent.keyDown(chartWell, { key: 'Escape' });
    expect(screen.queryByText(/Seq #10/)).toBeNull();
  });
});
