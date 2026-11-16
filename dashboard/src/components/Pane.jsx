import React, { useState, useCallback } from 'react';

/**
 * Instrument pane: a bordered panel with a header bar carrying a function-key tag
 * ("F2"), an uppercase title, an optional right-side status slot, and a collapsible
 * body toggled by a chevron button. `alert` turns the header red; color is otherwise
 * kept out of the chrome.
 */
export function Pane({ code, title, status, alert = false, className = '', bodyClassName = '', id, children }) {
  const [collapsed, setCollapsed] = useState(false);
  const toggleCollapse = useCallback(() => setCollapsed((v) => !v), []);

  return (
    <section
      id={id}
      tabIndex={-1}
      aria-label={title}
      className={`pane flex flex-col min-w-0 ${className}`}
    >
      <header
        className={`flex items-center gap-2 px-2 h-6 border-b border-rule text-[11px] uppercase tracking-[0.12em] ${
          alert ? 'bg-alert/10' : ''
        }`}
      >
        {code && (
          <span
            className={`px-1 font-semibold leading-4 ${
              alert ? 'bg-alert text-ground' : 'bg-amber text-ground'
            }`}
          >
            {code}
          </span>
        )}
        <span className={`font-headline font-semibold truncate ${alert ? 'text-alert' : 'text-text'}`}>
          {title}
        </span>
        {status && <span className="ml-auto min-w-0 overflow-hidden whitespace-nowrap text-ellipsis text-dim normal-case tracking-normal">{status}</span>}
        <button
          type="button"
          onClick={toggleCollapse}
          aria-expanded={!collapsed}
          aria-label={collapsed ? `Expand ${title}` : `Collapse ${title}`}
          className={`${status ? '' : 'ml-auto'} px-1 text-dim hover:text-amber bg-transparent border-none cursor-pointer text-[11px] leading-none`}
        >
          {collapsed ? '▸' : '▾'}
        </button>
      </header>
      {!collapsed && (
        <div className={`flex-1 min-h-0 ${bodyClassName}`}>{children}</div>
      )}
    </section>
  );
}

export default Pane;
