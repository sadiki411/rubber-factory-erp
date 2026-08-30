import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { ProductionLedgerTaskDrawer } from './ProductionLedgerTaskDrawer'

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
class ResizeObserverMock { observe() {} unobserve() {} disconnect() {} }
globalThis.ResizeObserver = ResizeObserverMock

const apiMocks = vi.hoisted(() => ({
  listOrders: vi.fn(),
  listMolds: vi.fn(),
  listStations: vi.fn(),
}))

vi.mock('../api/client', () => ({
  orderApi: { list: apiMocks.listOrders },
  moldApi: { list: apiMocks.listMolds },
  productionApi: { stations: apiMocks.listStations },
  toList: <T,>(payload: T[]) => payload,
}))

function renderDrawer(open: boolean) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <App>
        <ProductionLedgerTaskDrawer open={open} onClose={vi.fn()} />
      </App>
    </QueryClientProvider>,
  )
}

describe('ProductionLedgerTaskDrawer', () => {
  beforeEach(() => {
    apiMocks.listOrders.mockResolvedValue([])
    apiMocks.listMolds.mockResolvedValue([])
    apiMocks.listStations.mockResolvedValue([])
  })

  it('can stay mounted while closed before a task is selected', () => {
    expect(() => renderDrawer(false)).not.toThrow()
  })

  it('opens a new hand-ledger task without requiring machine or mold', async () => {
    renderDrawer(true)
    expect(await screen.findByText('新增生产手工账任务')).toBeInTheDocument()
    expect(screen.getByText(/机台、具体模具、工艺参数都可留空以后补录/)).toBeInTheDocument()
    expect(screen.getByLabelText(/机台（6台中选填）/)).not.toBeRequired()
    expect(screen.getByLabelText(/具体实物模具（可后补）/)).not.toBeRequired()
  })
})
