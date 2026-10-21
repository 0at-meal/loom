import React from 'react';

/**
 * Instrument pane: a bordered panel with a header bar carrying a function-key tag
 * ("F2"), an uppercase title and an optional right-side status slot. `alert` turns the
 * header red; color is otherwise kept out of the chrome.
 */
export function Pane({ code, title, status, alert = false, className = '', bodyClassName = '', id, children }) {
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
      </header>
      <div className={`flex-1 min-h-0 ${bodyClassName}`}>{children}</div>
    </section>
  );
}

export default Pane;
