import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { Sidebar } from '@/shared/layout/Sidebar'
import { TooltipProvider } from '@/shared/ui/tooltip'

vi.mock('@/shared/auth/AuthContext', () => ({
  useAuth: () => ({ isUser: true, isAdmin: true, user: { username: 'admin', role: 'admin' }, logout: vi.fn() }),
}))

vi.mock('@/hooks/queries', () => ({
  useHealthQuery: () => ({ data: { database: 'Connected' }, isError: false }),
}))

describe('Sidebar Navigation - Server Hardware', () => {
  const defaultProps = {
    pinned: true,
    onPinnedChange: vi.fn(),
    mobileOpen: false,
    onMobileOpenChange: vi.fn(),
  }

  beforeEach(() => {
    vi.resetAllMocks()
  })

  it('1. Renders Server Hardware sidebar navigation item', () => {
    render(
      <TooltipProvider delayDuration={0}>
        <MemoryRouter initialEntries={['/']}>
          <Sidebar {...defaultProps} />
        </MemoryRouter>
      </TooltipProvider>,
    )

    expect(screen.getByText('Server Hardware')).toBeInTheDocument()
    expect(screen.getByText('Switches')).toBeInTheDocument()
    expect(screen.getByText('Devices')).toBeInTheDocument()
  })

  it('2. Active navigation state works when on /server-hardware', () => {
    render(
      <TooltipProvider delayDuration={0}>
        <MemoryRouter initialEntries={['/server-hardware']}>
          <Sidebar {...defaultProps} />
        </MemoryRouter>
      </TooltipProvider>,
    )

    const serverHwLink = screen.getByRole('link', { name: /Server Hardware/i })
    expect(serverHwLink.className).toContain('text-white')
  })

  it('3. Switches navigation item remains active on /switches', () => {
    render(
      <TooltipProvider delayDuration={0}>
        <MemoryRouter initialEntries={['/switches']}>
          <Sidebar {...defaultProps} />
        </MemoryRouter>
      </TooltipProvider>,
    )

    const switchesLink = screen.getByRole('link', { name: /Switches/i })
    expect(switchesLink.className).toContain('text-white')

    const serverHwLink = screen.getByRole('link', { name: /Server Hardware/i })
    expect(serverHwLink.className).not.toContain('text-white')
  })
})
