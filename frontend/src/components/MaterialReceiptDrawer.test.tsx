import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { MaterialReceipt, Order } from '../types'
import { MaterialReceiptDrawer } from './MaterialReceiptDrawer'

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({ matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })),
})
class ResizeObserverMock { observe() {} unobserve() {} disconnect() {} }
globalThis.ResizeObserver = ResizeObserverMock

const apiMocks = vi.hoisted(() => ({ create: vi.fn(), update: vi.fn() }))
vi.mock('../api/client', () => ({
  materialReceiptApi: { create: apiMocks.create, update: apiMocks.update },
}))

const order: Order = {
  id: 7,
  order_no: 'TEST-ORDER-001',
  item_no: '10',
  batch_no: '',
  product_code: 'TEST-PRODUCT-001',
  product_name: '测试产品A',
  specification: 'TEST-SPEC-A',
  material: 'SYN-RUBBER-A',
  order_quantity: 240,
  order_date: '2026-08-03',
  status: 'OPEN',
}

const receipt: MaterialReceipt = {
  id: 19,
  order: null,
  order_id: null,
  order_no: 'TEST-ORDER-001',
  item_no: '10',
  finished_product_name: '客户原始名称',
  specification: '客户原始规格',
  material: 'SYN-RUBBER-A',
  batch_no: 'TEST-BATCH-09',
  sheet_size: 'TEST-SHEET-SIZE',
  weight_kg: '6.250',
  manufactured_on: '2026-08-04',
  source_sheet: '发料明细',
  source_row: 6,
}

function renderDrawer() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const invalidateSpy = vi.spyOn(client, 'invalidateQueries')
  const onClose = vi.fn()
  render(
    <QueryClientProvider client={client}>
      <App>
        <MaterialReceiptDrawer open receipt={receipt} orders={[order]} onClose={onClose} />
      </App>
    </QueryClientProvider>,
  )
  return { invalidateSpy, onClose }
}

describe('MaterialReceiptDrawer', () => {
  beforeEach(() => {
    apiMocks.create.mockReset().mockResolvedValue({ id: 20 })
    apiMocks.update.mockReset().mockResolvedValue({ id: receipt.id })
  })

  it('links an imported unlinked receipt and refreshes every dependent view', async () => {
    const user = userEvent.setup()
    const { invalidateSpy, onClose } = renderDrawer()

    expect(screen.getByText('这条发料记录尚未关联订单')).toBeInTheDocument()
    expect(screen.getByText('找到 1 条可能匹配的订单明细')).toBeInTheDocument()

    await user.click(screen.getByRole('combobox', { name: /关联订单明细/ }))
    await user.click(await screen.findByText(/建议匹配 · TEST-ORDER-001 \/ 10/))

    const orderNumber = screen.getByLabelText(/发料单订单号/)
    expect(orderNumber).toHaveValue('TEST-ORDER-001')
    expect(orderNumber).toHaveAttribute('readonly')
    expect(screen.getByLabelText(/项次/)).toHaveAttribute('readonly')
    expect(screen.getByLabelText(/成品名称/)).toHaveValue('测试产品A')
    expect(screen.getByLabelText(/规格/)).toHaveValue('TEST-SPEC-A')

    await user.click(screen.getByRole('button', { name: '保存并同步订单' }))

    await waitFor(() => expect(apiMocks.update).toHaveBeenCalledTimes(1))
    expect(apiMocks.update).toHaveBeenCalledWith(receipt.id, {
      order_id: order.id,
      order_no: order.order_no,
      item_no: order.item_no,
      finished_product_name: order.product_name,
      specification: order.specification,
      material: order.material,
      batch_no: receipt.batch_no,
      sheet_size: receipt.sheet_size,
      weight_kg: receipt.weight_kg,
      manufactured_on: receipt.manufactured_on,
    })

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
    const invalidatedKeys = invalidateSpy.mock.calls.map(([filters]) => filters?.queryKey)
    expect(invalidatedKeys).toEqual(expect.arrayContaining([
      ['material-receipts'],
      ['orders'],
      ['quality'],
      ['analytics'],
    ]))
  }, 20_000)
})
