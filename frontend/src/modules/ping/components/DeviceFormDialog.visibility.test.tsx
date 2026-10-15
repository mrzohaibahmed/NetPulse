import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { DeviceFormDialog } from '@/modules/ping/components/DeviceFormDialog'

const createDevice = vi.fn()

vi.mock('@/hooks/queries', () => ({
  useDeviceMutations: () => ({
    create: { isPending: false, mutateAsync: createDevice },
    update: { isPending: false, mutateAsync: vi.fn() },
  }),
}))

async function selectDeviceType(type: string) {
  fireEvent.click(screen.getAllByRole('combobox')[0])
  fireEvent.click(await screen.findByRole('option', { name: type }))
}

describe('manual device iLO configuration visibility', () => {
  beforeEach(() => {
    createDevice.mockReset().mockResolvedValue({})
    vi.stubGlobal('ResizeObserver', class {
      observe() {}
      unobserve() {}
      disconnect() {}
    })
    Element.prototype.scrollIntoView = vi.fn()
  })

  it('shows iLO fields only for server device types', async () => {
    render(<DeviceFormDialog open onOpenChange={vi.fn()} />)

    expect(screen.getByLabelText('iLO address')).toBeInTheDocument()
    expect(screen.getByLabelText('iLO Username')).toBeInTheDocument()
    expect(screen.getByLabelText('iLO Password')).toBeInTheDocument()

    await selectDeviceType('Switch')
    expect(screen.queryByLabelText('iLO address')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('iLO Username')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('iLO Password')).not.toBeInTheDocument()
    expect(screen.getByLabelText('SSH Username')).toBeInTheDocument()

    await selectDeviceType('Linux Server')
    expect(screen.getByLabelText('iLO address')).toBeInTheDocument()

    await selectDeviceType('ESXi Server')
    expect(screen.getByLabelText('iLO address')).toBeInTheDocument()
  })

  it('does not submit hidden iLO configuration for a non-server', async () => {
    render(<DeviceFormDialog open onOpenChange={vi.fn()} />)

    fireEvent.change(screen.getByLabelText('Hostname'), { target: { value: 'switch-1' } })
    fireEvent.change(screen.getByLabelText('OS IP address'), { target: { value: '10.0.0.1' } })
    fireEvent.change(screen.getByLabelText('iLO address'), { target: { value: 'ilo.local' } })
    fireEvent.change(screen.getByLabelText('iLO Username'), { target: { value: 'admin' } })
    fireEvent.change(screen.getByLabelText('iLO Password'), { target: { value: 'secret' } })
    await selectDeviceType('Switch')
    fireEvent.click(screen.getByRole('button', { name: 'Create device' }))

    await waitFor(() => expect(createDevice).toHaveBeenCalledOnce())
    const payload = createDevice.mock.calls[0][0]
    expect(payload.deviceType).toBe('Switch')
    expect(payload.iloAddress).toBeNull()
    expect(payload.credentials).toBeUndefined()
  })
})
