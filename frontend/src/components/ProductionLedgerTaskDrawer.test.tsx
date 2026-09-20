import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { ProductionLedgerTaskDrawer } from './ProductionLedgerTaskDrawer'
import type { ProductionRun } from '../types'

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
  updateRun: vi.fn(),
}))

vi.mock('../api/client', () => ({
  orderApi: { list: apiMocks.listOrders },
  moldApi: { list: apiMocks.listMolds },
  productionApi: { stations: apiMocks.listStations, updateRun: apiMocks.updateRun },
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

function renderEditDrawer(run: ProductionRun) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <App>
        <ProductionLedgerTaskDrawer open run={run} onClose={vi.fn()} />
      </App>
    </QueryClientProvider>,
  )
}

describe('ProductionLedgerTaskDrawer', () => {
  beforeEach(() => {
    apiMocks.listOrders.mockResolvedValue([])
    apiMocks.listMolds.mockResolvedValue([])
    apiMocks.listStations.mockResolvedValue([])
    apiMocks.updateRun.mockResolvedValue({})
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

  it('shows the reason field when an existing ledger task can change its orders', async () => {
    apiMocks.listOrders.mockResolvedValue([
      { id: 1, order_no: '362-001', item_no: '10', product_name: '产品362', specification: '362', material: 'NBR', order_quantity: 700, production_remaining_quantity: 700, due_date: '2026-09-20', status: 'OPEN' },
      { id: 2, order_no: '362-002', item_no: '20', product_name: '产品362', specification: '362', material: 'NBR', order_quantity: 500, production_remaining_quantity: 500, due_date: '2026-09-21', status: 'OPEN' },
    ])
    const run = {
      id: 99,
      order_id: 1,
      order_no: '362-001',
      specification: '362',
      material: 'NBR',
      order_quantity: 700,
      cavities: 2,
      estimated_defect_rate: 0,
      planned_mold_count: 350,
      estimated_hours: 1,
      status: 'PLANNED',
      is_ledger_only: true,
      order_allocations: [{ id: 1, order_id: 1, order_no: '362-001', item_no: '10', due_date: '2026-09-20', planned_quantity: 700, allocated_quantity: 0, remaining_quantity: 700 }],
    } as ProductionRun
    renderEditDrawer(run)

    await screen.findByRole('combobox', { name: /本次合并生产的订单/ })
    expect(screen.getByLabelText(/关联订单修改原因/)).toBeInTheDocument()
    expect(screen.getByText(/只有增加、移除订单或调整本次生产数量时必填/)).toBeInTheDocument()
    expect(screen.getByPlaceholderText('例如：追加同规格订单，合并生产以减少重复换模')).toBeInTheDocument()
  }, 20_000)
})
