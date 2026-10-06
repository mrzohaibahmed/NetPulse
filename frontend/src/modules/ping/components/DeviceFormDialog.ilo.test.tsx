import { describe, expect, it } from 'vitest'
import { deviceFormSchema, isIloOnlyDeviceType } from '@/modules/ping/components/DeviceFormDialog'

const base = {
  hostname: 'host-1',
  ipAddress: '',
  iloAddress: '',
  deviceType: 'Server',
  vendor: '',
  username: '',
  password: '',
  enableSecret: '',
  iloUsername: '',
  iloPassword: '',
  critical: false,
  monitor: true,
  showOnDashboard: false,
  location: '',
}

describe('deviceFormSchema / iLO identity', () => {
  it('accepts OS IP only', () => {
    const result = deviceFormSchema.safeParse({ ...base, ipAddress: '10.0.0.1', deviceType: 'Switch' })
    expect(result.success).toBe(true)
  })

  it('accepts iLO-only for eligible server types', () => {
    for (const deviceType of ['Server', 'Linux Server', 'ESXi Server']) {
      const result = deviceFormSchema.safeParse({
        ...base,
        iloAddress: 'ilo.example.com',
        deviceType,
        monitor: false,
      })
      expect(result.success).toBe(true)
    }
  })

  it('rejects iLO-only for Switch', () => {
    const result = deviceFormSchema.safeParse({
      ...base,
      iloAddress: '10.0.0.9',
      deviceType: 'Switch',
    })
    expect(result.success).toBe(false)
  })

  it('rejects neither address', () => {
    const result = deviceFormSchema.safeParse({ ...base })
    expect(result.success).toBe(false)
  })

  it('rejects URL-form iLO address', () => {
    const result = deviceFormSchema.safeParse({
      ...base,
      iloAddress: 'https://ilo.example/redfish/v1',
      deviceType: 'Server',
    })
    expect(result.success).toBe(false)
  })

  it('accepts both addresses', () => {
    const result = deviceFormSchema.safeParse({
      ...base,
      ipAddress: '10.0.0.1',
      iloAddress: 'ilo.example.com',
      deviceType: 'Server',
      monitor: true,
    })
    expect(result.success).toBe(true)
  })

  it('recognizes iLO-only device types', () => {
    expect(isIloOnlyDeviceType('Server')).toBe(true)
    expect(isIloOnlyDeviceType('Switch')).toBe(false)
  })
})
