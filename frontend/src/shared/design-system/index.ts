/**
 * NetPulse Enterprise Design System — shared tokens & class helpers.
 * Visual consistency only; no business logic.
 */

export const ds = {
  page: 'np-page',
  section: 'np-section',
  toolbar: 'np-toolbar',
  tableShell: 'np-table-shell',
  tableToolbar: 'np-table-toolbar',
  tableFooter: 'np-table-footer',
  label: 'np-label',
  metric: 'np-metric',
  caption: 'np-caption',
  description: 'np-description',
  hoverLift: 'np-hover-lift',
  fadeIn: 'np-fade-in',
  iconSm: 'np-icon-sm',
  iconMd: 'np-icon-md',
  iconLg: 'np-icon-lg',
  iconBox: 'np-icon-box',
  iconBoxSm: 'np-icon-box-sm',
} as const

export const badgeTone = {
  success: 'success',
  warning: 'warning',
  critical: 'danger',
  danger: 'danger',
  offline: 'offline',
  online: 'online',
  storm: 'storm',
  recovery: 'recovery',
  mitigation: 'mitigation',
  info: 'info',
  information: 'info',
} as const

export type BadgeTone = keyof typeof badgeTone
