import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { QualityPage } from './QualityPage'

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({ matches: !query.includes('max-width'), media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })),
})
class ResizeObserverMock { observe() {} unobserve() {} disconnect() {} }
globalThis.ResizeObserver = ResizeObserverMock

const apiMocks = vi.hoisted(() => ({
  summary: vi.fn(),
  listEmployees: vi.fn(),
  listShipmentLedger: vi.fn(),
  listShipments: vi.fn(),
  listReworks: vi.fn(),
  listOrders: vi.fn(),
  listProcessCards: vi.fn(),
  listUnitWeights: vi.fn(),
  listShipmentBatches: vi.fn(),
  listReworkCases: vi.fn(),
}))

vi.mock('../api/client', () => ({
  qualityApi: {
    summary: apiMocks.summary,
    listEmployees: apiMocks.listEmployees,
    listShipmentLedger: apiMocks.listShipmentLedger,
    listShipments: apiMocks.listShipments,
    listReworks: apiMocks.listReworks,
  },
  orderApi: { list: apiMocks.listOrders },
  qualityWorkflowApi: {
    listProcessCards: apiMocks.listProcessCards,
    listUnitWeights: apiMocks.listUnitWeights,
    listShipmentBatches: apiMocks.listShipmentBatches,
    listReworkCases: apiMocks.listReworkCases,
    createAndConfirmShipmentBatch: vi.fn(),
    createProcessCard: vi.fn(),
    updateProcessCard: vi.fn(),
  },
  toList: (payload: any) => Array.isArray(payload) ? payload : payload.results || [],
}))

vi.mock('../components/QualityShippingWorkflow', () => ({ QualityShippingWorkflow: () => <div>流程卡区域</div> }))
vi.mock('../components/QualityWorkflowManagement', () => ({
  QualityWorkflowManagement: () => <div>出货管理区域</div>,
  ShipmentBatchReviewDrawer: ({ open, item }: any) => open ? <div>批次详情 {item?.shipment_no}</div> : null,
}))
vi.mock('../components/QualityFormDrawers', () => ({
  QualityShipmentDrawer: () => null,
  QualityReworkDrawer: () => null,
  QualityEmployeeDrawer: () => null,
}))

const weightedBatch = {
  id: 1,
  shipment_no: 'QS-20260820-WEIGHT',
  shipment_date: '2026-08-20',
  status: 'CONFIRMED',
  shipped_quantity: 300,
  net_weight_kg: '7.500',
  line_count: 1,
  inspectors: [{ id: 1, employee_no: 'Q01', name: '王品检', role: 'INSPECTOR', is_active: true }],
  lines: [],
}

describe('QualityPage unified shipment ledger', () => {
  beforeEach(() => {
    apiMocks.summary.mockResolvedValue({
      totals: { inspection_quantity: 0, shipped_quantity: 300, returned_quantity: 0, reworked_quantity: 0, shipment_count: 1 },
      daily_trend: [], order_stats: [],
    })
    apiMocks.listEmployees.mockResolvedValue(weightedBatch.inspectors)
    apiMocks.listOrders.mockResolvedValue([])
    apiMocks.listShipments.mockResolvedValue([])
    apiMocks.listReworks.mockResolvedValue([])
    apiMocks.listProcessCards.mockResolvedValue([])
    apiMocks.listUnitWeights.mockResolvedValue([])
    apiMocks.listShipmentBatches.mockImplementation((filters: any) => filters?.status === 'DRAFT' ? [] : [weightedBatch])
    apiMocks.listReworkCases.mockResolvedValue([])
    apiMocks.listShipmentLedger.mockImplementation((filters: any) => filters?.shipment_status === 'DRAFT' ? [] : [
      {
        key: 'WEIGHTED:1', source_type: 'WEIGHTED', source_id: 1, status: 'CONFIRMED',
        shipment_no: weightedBatch.shipment_no, shipment_date: weightedBatch.shipment_date,
        order_nos: ['XB-001'], item_nos: ['10'], product_names: ['密封圈A'],
        specifications: ['20×30'], materials: ['NBR'], due_dates: ['2026-08-25'],
        inspectors: weightedBatch.inspectors, shipped_quantity: 300, net_weight_kg: '7.500',
        line_count: 1, batch: weightedBatch,
      },
      {
        key: 'LEGACY:1', source_type: 'LEGACY', source_id: 1, status: 'CONFIRMED',
        shipment_no: 'OLD-001', shipment_date: '2026-08-19', order_nos: ['OLD-ORDER'],
        product_names: ['历史产品'], specifications: ['旧规格'], materials: ['EPDM'],
        due_dates: [], inspectors: [], shipped_quantity: 20, net_weight_kg: null, line_count: 1,
        inspection_quantity: 25, qualified_quantity: 23, defective_quantity: 2,
        returned_quantity: 3, rework_count: 1,
        shipment: { id: 1, shipment_no: 'OLD-001' },
      },
    ])
  })

  it('shows weighted and legacy records once and opens weighted details', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<MemoryRouter><QueryClientProvider client={client}><App><QualityPage /></App></QueryClientProvider></MemoryRouter>)

    await userEvent.setup().click(await screen.findByRole('tab', { name: '每日出货' }))
    expect(await screen.findByText('QS-20260820-WEIGHT')).toBeInTheDocument()
    expect(screen.getByText('OLD-001')).toBeInTheDocument()
    expect(screen.getByText('密封圈A')).toBeInTheDocument()
    expect(screen.getByText('20×30 · NBR')).toBeInTheDocument()
    expect(screen.getByText('7.5 kg')).toBeInTheDocument()
    const legacyRow = screen.getByText('OLD-001').closest('tr')
    expect(legacyRow).toHaveTextContent('25 / 23 / 2')
    expect(legacyRow).toHaveTextContent('3')
    expect(legacyRow).toHaveTextContent('1 次')

    await userEvent.setup().click(screen.getAllByRole('button', { name: /查看明细/ })[0])
    expect(await screen.findByText('批次详情 QS-20260820-WEIGHT')).toBeInTheDocument()
  })

  it('passes keyword and status filters to the unified ledger and batch APIs', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<MemoryRouter><QueryClientProvider client={client}><App><QualityPage /></App></QueryClientProvider></MemoryRouter>)
    await screen.findByText('品检出货与退货返工')

    await userEvent.setup().type(screen.getByPlaceholderText('搜索出货单、订单、产品、规格、材质或品检员'), 'NBR')
    await waitFor(() => expect(apiMocks.listShipmentLedger).toHaveBeenLastCalledWith(expect.objectContaining({ q: 'NBR', shipment_status: 'CONFIRMED' })))
    expect(apiMocks.listShipmentBatches).toHaveBeenCalledWith({ ordering: '-shipment_date', page_size: 1000 })

    await userEvent.setup().click(screen.getByText('已确认出货'))
    await userEvent.setup().click(await screen.findByText('草稿 / 待确认'))
    await waitFor(() => {
      expect(apiMocks.listShipmentLedger).toHaveBeenLastCalledWith(expect.objectContaining({ shipment_status: 'DRAFT' }))
      expect(apiMocks.listShipmentBatches).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'DRAFT' }))
    })
  })
})
