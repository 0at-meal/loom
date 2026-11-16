import React, { useEffect } from 'react';

const AUTO_DISMISS_MS = 4000;

/**
 * A single toast notification. Auto-dismisses after 4 seconds.
 */
function ToastItem({ status, onClose }) {
  useEffect(() => {
    const t = setTimeout(onClose, AUTO_DISMISS_MS);
    return () => clearTimeout(t);
  }, [onClose]);

  const borderColor = status.type === 'success' ? 'var(--color-ok)' : 'var(--color-alert)';
  const iconColor = status.type === 'success' ? 'text-ok' : 'text-alert';

  return (
    <div
      className="bg-pane border border-rule px-3 py-2 flex items-center gap-2 text-[12px] animate-toast-in"
      style={{ borderLeftWidth: '3px', borderLeftColor: borderColor }}
      role="status"
      aria-live="polite"
    >
      <span className={iconColor}>■</span>
      <span className="text-text flex-1 truncate">{status.text}</span>
      <button
        type="button"
        onClick={onClose}
        className="text-dim hover:text-text shrink-0"
        aria-label="Dismiss notification"
      >
        ×
      </button>
    </div>
  );
}

/**
 * Toast stack: renders at most 3 notifications, positioned top-right below the header bar.
 * Each toast auto-dismisses after 4 s.
 *
 * Props:
 *   toasts: Array of { id, type, text }
 *   onDismiss: (id) => void
 */
export function ToastStack({ toasts = [], onDismiss }) {
  const visible = toasts.slice(-3);

  if (visible.length === 0) return null;

  return (
    <div className="fixed top-[40px] right-4 z-50 flex flex-col gap-1.5 w-80">
      {visible.map((t) => (
        <ToastItem
          key={t.id}
          status={t}
          onClose={() => onDismiss && onDismiss(t.id)}
        />
      ))}
    </div>
  );
}

export default ToastStack;
