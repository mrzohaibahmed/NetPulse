import type { SwitchHardwareCurrent } from '@/api/switchHardwareService'
import { EmptyState } from '@/shared/components/EmptyState'
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table'
import { formatHardwareValue } from '@/modules/storm/components/hardware/hardwareStatus'

interface InventorySectionProps {
  inventory: SwitchHardwareCurrent['inventory'] | null | undefined
  vendor?: string | null
  platform?: string | null
}

export function InventorySection({ inventory, vendor, platform }: InventorySectionProps) {
  if (!inventory) {
    return (
      <Card className="border-border/70">
        <CardHeader>
          <CardTitle className="text-base">Hardware Inventory</CardTitle>
        </CardHeader>
        <CardContent>
          <EmptyState
            title="Inventory unavailable"
            description="No chassis or module inventory has been collected yet."
          />
        </CardContent>
      </Card>
    )
  }

  const fields = [
    { label: 'Vendor', value: vendor || 'Cisco' },
    { label: 'Model', value: inventory.model },
    { label: 'Serial number', value: inventory.serialNumber },
    { label: 'Product ID', value: inventory.productId },
    { label: 'Platform', value: platform },
    { label: 'IOS / firmware', value: inventory.iosVersion || inventory.firmwareVersion },
    { label: 'Hostname', value: inventory.hostname },
    { label: 'Uptime', value: inventory.uptime },
    { label: 'Boot reason', value: inventory.bootReason },
  ]

  const modules = [...(inventory.chassis || []), ...(inventory.modules || [])]

  return (
    <Card className="border-border/70">
      <CardHeader>
        <CardTitle className="text-base">Hardware Inventory</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {fields.map((field) => (
            <div key={field.label} className="rounded-lg border border-border/60 px-3 py-2">
              <p className="text-xs text-muted-foreground">{field.label}</p>
              <p className="break-words text-sm font-medium">{formatHardwareValue(field.value)}</p>
            </div>
          ))}
        </div>

        {modules.length === 0 ? (
          <EmptyState
            title="No modules reported"
            description="Chassis/module details were not present in the latest collection."
          />
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Product ID</TableHead>
                  <TableHead>Serial</TableHead>
                  <TableHead>Description</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {modules.map((module, index) => (
                  <TableRow key={`${module.name || 'module'}-${index}`}>
                    <TableCell className="font-medium">
                      {formatHardwareValue(module.name)}
                    </TableCell>
                    <TableCell className="mono">
                      {formatHardwareValue(module.productId)}
                    </TableCell>
                    <TableCell className="mono">
                      {formatHardwareValue(module.serialNumber)}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {formatHardwareValue(module.descr)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
