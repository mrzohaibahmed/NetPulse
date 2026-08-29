import { LINK_SPEED_COLORS, DOWN_LINK_COLOR } from './switchTopologyUtils'

const LEGEND_ITEMS = [
  { label: '100.0 Gbps', color: LINK_SPEED_COLORS['100_GBPS'], dashed: false },
  { label: '20.0 Gbps', color: LINK_SPEED_COLORS['20_GBPS'], dashed: false },
  { label: '10.0 Gbps', color: LINK_SPEED_COLORS['10_GBPS'], dashed: false },
  { label: '1.0 Gbps', color: LINK_SPEED_COLORS['1_GBPS'], dashed: false },
  { label: '100.0 Mbps', color: LINK_SPEED_COLORS['100_MBPS'], dashed: false },
  { label: '10.0 Mbps', color: LINK_SPEED_COLORS['10_MBPS'], dashed: false },
  { label: 'Down Link', color: DOWN_LINK_COLOR, dashed: true },
] as const

function LegendLine({ color, dashed }: { color: string; dashed: boolean }) {
  return (
    <span
      className="inline-block h-0 w-8 shrink-0 border-t-2"
      style={{
        borderColor: color,
        borderStyle: dashed ? 'dashed' : 'solid',
      }}
      aria-hidden
    />
  )
}

export function LinkSpeedLegend() {
  return (
    <div className="rounded-lg border border-border/70 bg-card/80 px-3 py-2 shadow-sm backdrop-blur-sm">
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Link Speed
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        {LEGEND_ITEMS.map((item) => (
          <div key={item.label} className="flex items-center gap-2 text-xs text-foreground">
            <LegendLine color={item.color} dashed={item.dashed} />
            <span>{item.label}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
