import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { OrdersPage } from './OrdersPage'
import { ProductSpecificationsPage } from './ProductSpecificationsPage'

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({ matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })),
})
class ResizeObserverMock { observe() {} unobserve() {} disconnect() {} }
globalThis.ResizeObserver = ResizeObserverMock

const apiMocks = vi.hoisted(() => ({ listSpecifications: vi.fn(), listOrders: vi.fn(), listReceipts: vi.fn(), createSpecification: vi.fn(), updateSpecification: vi.fn(), createOrder: vi.fn(), updateOrder: vi.fn(), createReceipt: vi.fn(), updateReceipt: vi.fn() }))
vi.mock('../api/client', () => ({
  productSpecificationApi: { list: apiMocks.listSpecifications, create: apiMocks.createSpecification, update: apiMocks.updateSpecification },
  orderApi: { list: apiMocks.listOrders, create: apiMocks.createOrder, update: apiMocks.updateOrder },
  materialReceiptApi: { list: apiMocks.listReceipts, create: apiMocks.createReceipt, update: apiMocks.updateReceipt },
  businessImportApi: { preview: vi.fn(), commit: vi.fn(), templateUrl: (type: string) => `/template?type=${type}`, errorReportUrl: (token: string) => `/errors/${token}` },
  toList: <T,>(payload: T[]) => payload,
}))

function renderPage(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><App>{node}</App></QueryClientProvider>)
}

describe('business data pages on mobile', () => {
  beforeEach(() => {
    apiMocks.listSpecifications.mockResolvedValue([{
      id: 1, product_name: '测试产品A', customer_product_no: 'TEST-PRODUCT-001', specification: 'TEST-SPEC-A', material: 'SYN-RUBBER-A',
      primary_curing: '160℃×240秒', secondary_curing: '140℃×2h', total_cavities: '06孔', effective_cavities: '05孔', standard_hours: '4.5', is_active: true,
    }])
    apiMocks.listOrders.mockResolvedValue([{
      id: 2, order_no: 'TEST-ORDER-001', item_no: '10', batch_no: '', product_code: 'TEST-PRODUCT-001', product_name: '测试产品A', specification: 'TEST-SPEC-A', material: 'SYN-RUBBER-A',
      order_quantity: 240, order_date: '2026-08-16', due_date: '2026-08-30', status: 'OPEN', required_material_kg: '8.75', received_material_kg: '0', material_gap_kg: '8.75', material_status: 'NOT_RECEIVED', process_card_count: 0, process_card_covered_quantity: 0, process_card_status: 'NOT_RECEIVED',
    }])
    apiMocks.listReceipts.mockResolvedValue([{
      id: 9, order: null, order_id: null, order_no: 'TEST-ORDER-001', item_no: '10', finished_product_name: '测试产品A', specification: 'TEST-SPEC-A', material: 'SYN-RUBBER-A', batch_no: 'TEST-BATCH-09', sheet_size: 'TEST-SHEET-SIZE', weight_kg: '2.500', manufactured_on: '2026-08-04', source_sheet: '发料明细', source_row: 6,
    }])
  })

  afterEach(cleanup)

  it('renders product specifications as cards without changing source text', async () => {
    renderPage(<ProductSpecificationsPage />)
    expect(await screen.findByText('测试产品A')).toBeInTheDocument()
    expect(screen.getByText('05孔 / 06孔')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('renders material and process-card states in order cards and preserves zero', async () => {
    renderPage(<OrdersPage />)
    expect(await screen.findByText('TEST-ORDER-001 / 10')).toBeInTheDocument()
    expect(screen.getAllByText('未收到').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('0 kg')).toBeInTheDocument()
    expect(screen.getByText('0 张 / 覆盖 0')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('shows pending receipt count before opening the mobile receipt tab and renders actionable cards', async () => {
    const user = userEvent.setup()
    renderPage(<OrdersPage />)

    expect(await screen.findByText('1 条待关联')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: /发料记录/ }))

    expect(await screen.findByText('2.500 kg')).toBeInTheDocument()
    expect(screen.getByText('关联到订单').closest('button')).toHaveClass('ant-btn-block')
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
})
