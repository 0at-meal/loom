import React, { useEffect } from 'react';

const SHORTCUTS = [
  { keys: 'F1 – F6', description: 'Focus pane by number' },
  { keys: '1 – 6', description: 'Focus pane (alternative)' },
  { keys: '⇧1', description: 'Preset: Standard cliff (Alpha outage)' },
  { keys: '⇧2', description: 'Preset: Sensitive blip (3.5 s transient)' },
  { keys: '⇧3', description: 'Preset: Gray failure (Alpha 60%)' },
  { keys: 'R', description: 'Reset all routes (confirms first)' },
  { keys: 'C', description: 'Toggle CRT scanline effect' },
  { keys: '?', description: 'Show / hide this help' },
  { keys: '← →', description: 'Move chart crosshair (when chart focused)' },
  { keys: 'Escape', description: 'Dismiss crosshair / close help' },
];

/**
 * Modal overlay listing all keyboard shortcuts.
 * Opened with "?" and closed with "?" again or Escape.
 */
export function KeyboardHelp({ open, onClose }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-ground/80"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Keyboard shortcuts"
    >
      <div
        className="pane w-[420px] max-w-[90vw] max-h-[80vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center gap-2 px-3 h-7 border-b border-rule text-[12px] uppercase tracking-[0.12em]">
          <span className="px-1 bg-amber text-ground font-semibold">?</span>
          <span className="font-headline font-semibold text-text">Keyboard shortcuts</span>
          <button
            type="button"
            onClick={onClose}
            className="ml-auto text-dim hover:text-text"
            aria-label="Close"
          >
            ×
          </button>
        </header>
        <div className="p-2">
          <table className="w-full text-[12px]">
            <tbody>
              {SHORTCUTS.map((s) => (
                <tr key={s.keys} className="border-b border-rule last:border-b-0">
                  <td className="px-2 py-1.5 whitespace-nowrap">
                    <kbd className="px-1.5 bg-ground border border-rule text-amber font-mono font-semibold">
                      {s.keys}
                    </kbd>
                  </td>
                  <td className="px-2 py-1.5 text-dim">{s.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export default KeyboardHelp;
