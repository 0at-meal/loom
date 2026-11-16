import { useState, useEffect } from 'react';

/**
 * Measures a container's pixel dimensions via ResizeObserver.
 * Falls back safely if ResizeObserver is not available (e.g. in test environments).
 * Returns { width, height } that update when the element resizes.
 */
export function useChartSize(ref) {
  const [size, setSize] = useState({ width: 800, height: 300 });

  useEffect(() => {
    if (!ref.current || typeof ResizeObserver === 'undefined') return;
    try {
      const ro = new ResizeObserver(([entry]) => {
        if (!entry) return;
        const { width, height } = entry.contentRect || {};
        if (width > 0 && height > 0) {
          setSize({ width: Math.round(width), height: Math.round(height) });
        }
      });
      ro.observe(ref.current);
      return () => ro.disconnect();
    } catch {
      // In case ResizeObserver polyfill or mock throws
    }
  }, [ref]);

  return size;
}

export default useChartSize;
