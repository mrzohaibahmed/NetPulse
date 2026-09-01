import { PageHeader } from '@/shared/components/PageHeader'
import { LoadingState } from '@/shared/components/LoadingState'
import { ErrorState } from '@/shared/components/ErrorState'
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card'
import { useTopologySwitches, useFullTopology } from '@/hooks/useTopologyData'
import { SwitchTopology } from '@/modules/storm/components/switches/SwitchTopology'

export function SwitchesPage() {
  const switchesQuery = useTopologySwitches()
  const topologyQuery = useFullTopology(true)

  const switches = switchesQuery.data ?? []
  const topologyEdges = topologyQuery.data?.edges ?? []

  const retry = () => {
    void switchesQuery.refetch()
    void topologyQuery.refetch()
  }

  if (switchesQuery.isLoading && switches.length === 0) {
    return (
      <div className="np-page">
        <PageHeader
          title="Switches"
          description="Switch inventory and switch-to-switch connectivity from CDP/LLDP discovery."
        />
        <LoadingState label="Loading switches..." />
      </div>
    )
  }

  if (switchesQuery.isError) {
    return (
      <div className="np-page">
        <PageHeader
          title="Switches"
          description="Switch inventory and switch-to-switch connectivity from CDP/LLDP discovery."
        />
        <ErrorState title="Unable to load switches" message="Unable to load switches." onRetry={retry} />
      </div>
    )
  }

  return (
    <div className="np-page space-y-6">
      <PageHeader
        title="Switches"
        description="Switch inventory and switch-to-switch connectivity from CDP/LLDP discovery."
      />

      <Card variant="section" className="glass rounded-xl">
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Switch topology</CardTitle>
        </CardHeader>
        <CardContent>
          {switches.length === 0 ? (
            <div className="flex min-h-[420px] items-center justify-center rounded-xl border border-dashed border-border/60 bg-muted/10 text-sm text-muted-foreground">
              No switches found.
            </div>
          ) : (
            <div className="space-y-3">
              {topologyQuery.isError ? (
                <p className="text-sm text-muted-foreground">
                  Switch links could not be loaded. Showing switches without connections.
                </p>
              ) : null}
              <SwitchTopology switches={switches} edges={topologyEdges} />
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
