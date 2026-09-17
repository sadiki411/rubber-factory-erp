import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ProductionRunDrawer } from './ProductionRunDrawer'
import type { ProductionStation } from '../types'

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({ matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })),
})
class ResizeObserverMock { observe() {} unobserve() {} disconnect() {} }
globalThis.ResizeObserver = ResizeObserverMock

const apiMocks = vi.hoisted(() => ({ stations: vi.fn(), molds: vi.fn(), orders: vi.fn(), specifications: vi.fn(), createRun: vi.fn(), updateRun: vi.fn() }))
vi.mock('../api/client', () => ({
  productionApi: { stations: apiMocks.stations, createRun: apiMocks.createRun, updateRun: apiMocks.updateRun },
  moldApi: { list: apiMocks.molds },
  orderApi: { list: apiMocks.orders },
  productSpecificationApi: { list: apiMocks.specifications },
  toList: <T,>(payload: T[]) => payload,
}))

const station: ProductionStation = { id: 1, code: 'M-01', group: 'A', position_no: 1, is_active: true, machine: { id: 1, code: 'M-01', name: '1号机', is_active: true } }

function renderDrawer() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><App><ProductionRunDrawer open station={station} initialStatus="PLANNED" onClose={vi.fn()} /></App></QueryClientProvider>)
}

describe('ProductionRunDrawer order and specification linkage', () => {
  beforeEach(() => {
    apiMocks.stations.mockResolvedValue([station])
    apiMocks.molds.mockResolvedValue([])
    apiMocks.specifications.mockResolvedValue([{
      id: 9, product_name: '测试产品A', customer_product_no: 'TEST-PRODUCT-001', specification: 'TEST-SPEC-A', material: 'SYN-RUBBER-A',
      material_length: 'TEST-SHEET-SIZE', cut_weight: '10g', actual_material_length: 'ACTUAL-SHEET-SIZE', actual_cut_weight: '10.2g', strip_count: '9/4', primary_curing: '160℃×240秒', secondary_curing: '140℃×2h',
      total_cavities: '6', effective_cavities: '5', mold_model_id: 7, mold_model: { id: 7, code: 'TEST-MOLD-MODEL-01', product_name: '测试产品A模具', is_active: true }, is_active: true,
    }])
    apiMocks.orders.mockResolvedValue([{
      id: 3, order_no: 'TEST-ORDER-001', item_no: '10', batch_no: '', product_code: 'TEST-PRODUCT-001', product_name: '测试产品A', specification: 'TEST-SPEC-A', material: 'SYN-RUBBER-A',
      order_quantity: 240, order_date: '2026-08-16', status: 'OPEN', product_specification_id: 9, forming_hours: '4.75',
    }])
  })

  it('copies order snapshots and only safely convertible process parameters', async () => {
    const user = userEvent.setup()
    renderDrawer()

    const orderSelect = screen.getByRole('combobox', { name: /本次合并生产的订单/ })
    await user.click(orderSelect)
    await user.click(await screen.findByText(/TEST-ORDER-001 · 10 · 未填交期 · TEST-SPEC-A/))

    await waitFor(() => expect(screen.getByLabelText(/交期最早订单号/)).toHaveValue('TEST-ORDER-001'))
    expect(screen.getByLabelText(/^规格/)).toHaveValue('TEST-SPEC-A')
    expect(screen.getByLabelText(/材质 \/ 胶料配方/)).toHaveValue('SYN-RUBBER-A')
    expect(screen.getByLabelText(/本次合计生产数量/)).toHaveValue('240')
    expect(screen.getByLabelText(/胶料尺寸/)).toHaveValue('ACTUAL-SHEET-SIZE')
    expect(screen.getByLabelText(/模具孔数/)).toHaveValue('5')
    expect(screen.getByLabelText(/硫化时间/)).toHaveValue('240')
    expect(screen.getByLabelText(/预计生产工时/)).toHaveValue('3.33')
    expect(screen.getByText('二烤：140℃×2h')).toBeInTheDocument()
    expect(screen.getByText('模具型号：TEST-MOLD-MODEL-01')).toBeInTheDocument()
  }, 15_000)

  it('keeps planned hours independent when only a product specification is selected', async () => {
    const user = userEvent.setup()
    renderDrawer()

    const specificationSelect = screen.getByRole('combobox', { name: /关联产品规格/ })
    await user.click(specificationSelect)
    await user.click(await screen.findByText('TEST-PRODUCT-001 · 测试产品A · TEST-SPEC-A'))

    await waitFor(() => expect(screen.getByLabelText(/胶料尺寸/)).toHaveValue('ACTUAL-SHEET-SIZE'))
    expect(screen.getByLabelText(/预计生产工时/)).toHaveValue('8.00')
  }, 15_000)

  it('filters to matching open orders and totals their remaining quantities', async () => {
    const user = userEvent.setup()
    apiMocks.orders.mockResolvedValue([
      {
        id: 3, order_no: 'TEST-ORDER-001', item_no: '10', product_name: '测试产品A', specification: 'TEST-SPEC-A', material: 'SYN-RUBBER-A',
        order_quantity: 1000, production_remaining_quantity: 700, order_date: '2026-08-16', due_date: '2026-09-20', status: 'OPEN', product_specification_id: 9,
      },
      {
        id: 4, order_no: 'TEST-ORDER-002', item_no: '20', product_name: '测试产品A', specification: 'TEST-SPEC-A', material: 'SYN-RUBBER-A',
        order_quantity: 300, production_remaining_quantity: 300, order_date: '2026-08-17', due_date: '2026-09-18', status: 'OPEN', product_specification_id: 9,
      },
      {
        id: 5, order_no: 'OTHER-MATERIAL', item_no: '30', product_name: '测试产品A', specification: 'TEST-SPEC-A', material: 'EPDM',
        order_quantity: 200, order_date: '2026-08-17', due_date: '2026-09-19', status: 'OPEN', product_specification_id: 9,
      },
      {
        id: 6, order_no: 'COMPLETED-ORDER', item_no: '40', product_name: '测试产品A', specification: 'TEST-SPEC-A', material: 'SYN-RUBBER-A',
        order_quantity: 200, order_date: '2026-08-17', due_date: '2026-09-19', status: 'COMPLETED', product_specification_id: 9,
      },
    ])
    renderDrawer()

    const orderSelect = screen.getByRole('combobox', { name: /本次合并生产的订单/ })
    await user.click(orderSelect)
    await user.click(await screen.findByText(/TEST-ORDER-001 · 10 · 交期 2026-09-20/))
    await user.click(orderSelect)

    expect(await screen.findByText(/TEST-ORDER-002 · 20 · 交期 2026-09-18/)).toBeInTheDocument()
    expect(screen.queryByText(/OTHER-MATERIAL · 30/)).not.toBeInTheDocument()
    expect(screen.queryByText(/COMPLETED-ORDER · 40/)).not.toBeInTheDocument()
    await user.click(screen.getByText(/TEST-ORDER-002 · 20 · 交期 2026-09-18/))

    await waitFor(() => expect(screen.getByLabelText(/本次合计生产数量/)).toHaveValue('1000'))
    expect(screen.getByText('合并生产清单 · 共 2 个订单')).toBeInTheDocument()
    expect(screen.getByText(/交期顺序 1/)).toBeInTheDocument()
    expect(screen.getAllByText(/TEST-ORDER-002/).length).toBeGreaterThan(0)
    expect(screen.getByLabelText(/计划生产模数/)).toHaveValue('206')
  }, 20_000)
})
