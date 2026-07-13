import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { MoldAsset } from '../types'
import { OperationDrawer } from './OperationDrawer'

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = ResizeObserverMock

const apiMocks = vi.hoisted(() => ({
  action: vi.fn(),
  listMachines: vi.fn(),
  listSlots: vi.fn(),
}))

vi.mock('../api/client', () => ({
  ApiError: class ApiError extends Error {},
  moldApi: { action: apiMocks.action },
  masterApi: () => ({ list: apiMocks.listMachines }),
  slotApi: { list: apiMocks.listSlots },
  toList: <T,>(payload: T[]) => payload,
}))

const mold = {
  id: 8,
  asset_code: 'MACHINE-008',
  mold_model: { id: 1, code: 'MACHINE', product_name: '机台模具' },
  status: 'ON_MACHINE',
  machine: { id: 2, code: '2', name: '2号机台' },
} satisfies MoldAsset

describe('OperationDrawer', () => {
  beforeEach(() => {
    apiMocks.action.mockReset()
    apiMocks.listSlots.mockReset().mockResolvedValue([])
    apiMocks.listMachines.mockReset().mockResolvedValue([
      mold.machine,
      { id: 3, code: '3', name: '3号机台', active: true },
    ])
  })

  it('uses the change-machine wording and excludes the current machine', async () => {
    const user = userEvent.setup()
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <App><OperationDrawer open mold={mold} action="load-machine" onClose={vi.fn()} /></App>
      </QueryClientProvider>,
    )

    expect(screen.getByRole('dialog', { name: /更换机台/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /确认更换机台/ })).toBeInTheDocument()
    await user.click(screen.getByRole('combobox', { name: '机台' }))
    expect(await screen.findByText('3 · 3号机台')).toBeInTheDocument()
    expect(screen.queryByText('2 · 2号机台')).not.toBeInTheDocument()
  })
})
