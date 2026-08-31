import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { HistoryManagementSection } from '@/modules/settings/components/HistoryManagementSection'

const mutateAsync = vi.fn()
const useHistoryDeletionMutation = vi.fn(() => ({
  mutateAsync,
  isPending: false,
}))

vi.mock('@/hooks/queries', () => ({
  useHistoryDeletionMutation: () => useHistoryDeletionMutation(),
}))

function renderSection() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <HistoryManagementSection />
    </QueryClientProvider>,
  )
}

describe('HistoryManagementSection', () => {
  beforeEach(() => {
    mutateAsync.mockReset()
    useHistoryDeletionMutation.mockReturnValue({
      mutateAsync,
      isPending: false,
    })
  })

  it('renders delete buttons', () => {
    renderSection()
    expect(screen.getByText('History Management')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Delete Ping History' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Delete Telemetry History' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Delete Incident History' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Delete All History' })).toBeInTheDocument()
  })

  it('opens confirmation dialog before deletion', () => {
    renderSection()
    fireEvent.click(screen.getByRole('button', { name: 'Delete Ping History' }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(mutateAsync).not.toHaveBeenCalled()
  })

  it('deletes only after explicit confirmation', async () => {
    mutateAsync.mockResolvedValue({
      success: true,
      message: 'History deleted successfully. 10 records deleted.',
      scope: 'ping',
      deleted: { pingHistory: 10 },
      totalDeleted: 10,
    })
    renderSection()
    fireEvent.click(screen.getByRole('button', { name: 'Delete Ping History' }))
    fireEvent.click(screen.getByRole('button', { name: 'Delete history' }))
    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith('ping')
    })
  })

  it('requires typing DELETE for all scope', () => {
    renderSection()
    fireEvent.click(screen.getByRole('button', { name: 'Delete All History' }))
    const confirmButton = screen.getByRole('button', { name: 'Delete history' })
    expect(confirmButton).toBeDisabled()
    fireEvent.change(screen.getByLabelText(/Type/i), { target: { value: 'DELETE' } })
    expect(confirmButton).not.toBeDisabled()
  })

  it('disables delete buttons while request is pending', () => {
    useHistoryDeletionMutation.mockReturnValue({
      mutateAsync,
      isPending: true,
    })
    renderSection()
    expect(screen.getByRole('button', { name: 'Delete Ping History' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Delete All History' })).toBeDisabled()
  })
})
