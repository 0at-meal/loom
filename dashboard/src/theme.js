/**
 * Shared palette and acquirer identities for the terminal (mirrors src/index.css).
 * The alert override is inviolable: an acquirer in outage or with health < 0.70 is drawn
 * in red everywhere, whatever its nominal color.
 */
export const COLORS = {
  ground: '#0A0A09',
  pane: '#111110',
  rule: '#2B2924',
  text: '#E8E2D0',
  dim: '#8A8473',
  amber: '#FFA028',
  cyan: '#5CC8E0',
  cream: '#D8D2BE',
  alert: '#FF4A3D',
  ok: '#4FD18B',
};

export const HEALTH_ALERT_THRESHOLD = 0.7;

export const ACQUIRERS = [
  { id: 'acquirer_alpha', name: 'Alpha', ticker: 'ALPHA', color: COLORS.amber },
  { id: 'acquirer_beta', name: 'Beta', ticker: 'BETA', color: COLORS.cyan },
  { id: 'acquirer_gamma', name: 'Gamma', ticker: 'GAMMA', color: COLORS.cream },
];

export const ACQUIRER_IDS = ACQUIRERS.map((a) => a.id);

export function isDegraded(acquirerId, activeOutages = {}, acquirerStates = {}) {
  const health = acquirerStates[acquirerId]?.health_score;
  return !!activeOutages[acquirerId] || (typeof health === 'number' && health < HEALTH_ALERT_THRESHOLD);
}

export function acquirerColor(acquirerId, activeOutages, acquirerStates) {
  if (isDegraded(acquirerId, activeOutages, acquirerStates)) return COLORS.alert;
  return ACQUIRERS.find((a) => a.id === acquirerId)?.color ?? COLORS.dim;
}

export function tickerOf(acquirerId) {
  return ACQUIRERS.find((a) => a.id === acquirerId)?.ticker ?? String(acquirerId ?? '—');
}
