import { useMemo, useState } from 'react'
import { Search } from 'lucide-react'

import { PageHeader } from '@/shared/components/PageHeader'
import { LoadingState } from '@/shared/components/LoadingState'
import { ErrorState } from '@/shared/components/ErrorState'
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card'
import { Input } from '@/shared/ui/input'
import { useTopologySwitches, useFullTopology } from '@/hooks/useTopologyData'
import { SwitchTopology } from '@/modules/storm/components/switches/SwitchTopology'

function matchesSwitchSearch(
  hostname: string,
  ip: string,
  query: string,
): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  return hostname.toLowerCase().includes(q) || ip.toLowerCase().includes(q)
}

export function SwitchesPage() {
  const [search, setSearch] = useState('')
  const switchesQuery = useTopologySwitches()
  const topologyQuery = useFullTopology(true)

  const switches = switchesQuery.data ?? []
  const topologyEdges = topologyQuery.data?.edges ?? []

  const filteredSwitches = useMemo(() => {
    return switches.filter((sw) => {
      const hostname = sw.hostname || sw.label || ''
      const ip = sw.ip || sw.managementAddress || ''
      return matchesSwitchSearch(hostname, ip, search)
    })
  }, [switches, search])

  const isLoading = switchesQuery.isLoading || topologyQuery.isLoading
  const isError = switchesQuery.isError || topologyQuery.isError

  const retry = () => {
    void switchesQuery.refetch()
    void topologyQuery.refetch()
  }

  if (isLoading && switches.length === 0) {
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

  if (isError) {
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

      <div className="max-w-md space-y-1.5">
        <label className="text-xs font-medium text-muted-foreground" htmlFor="switch-search">
          Search
        </label>
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            id="switch-search"
            type="search"
            placeholder="Search switches..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9"
          />
        </div>
      </div>

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
            <div className="relative min-h-[480px]">
              {isLoading ? (
                <div className="absolute inset-0 z-10 flex items-center justify-center rounded-xl bg-background/60 backdrop-blur-[1px]">
                  <LoadingState label="Loading switches..." />
                </div>
              ) : null}
              <SwitchTopology switches={filteredSwitches} edges={topologyEdges} />
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
