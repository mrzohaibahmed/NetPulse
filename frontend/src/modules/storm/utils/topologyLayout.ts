export type LayoutPosition = { x: number; y: number }

export function layoutPositionsFromSaved(
  layout: { nodes?: Array<{ id: string; position: LayoutPosition }> } | null | undefined,
): Record<string, LayoutPosition> {
  const map: Record<string, LayoutPosition> = {}
  for (const node of layout?.nodes ?? []) {
    if (!node?.id || !node.position) continue
    map[node.id] = { x: node.position.x, y: node.position.y }
  }
  return map
}

/** When the user has unsaved drags, session wins. Otherwise saved server layout wins. */
export function mergeLayoutPositions(
  saved: Record<string, LayoutPosition>,
  session: Record<string, LayoutPosition>,
  preferSession: boolean,
): Record<string, LayoutPosition> {
  return preferSession ? { ...saved, ...session } : { ...session, ...saved }
}

export function hasSavedLayout(saved: Record<string, LayoutPosition>): boolean {
  return Object.keys(saved).length > 0
}
