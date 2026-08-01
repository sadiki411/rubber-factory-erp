import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BusinessImportDrawer } from './BusinessImportDrawer'

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({ matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })),
})
class ResizeObserverMock { observe() {} unobserve() {} disconnect() {} }
globalThis.ResizeObserver = ResizeObserverMock

const apiMocks = vi.hoisted(() => ({ preview: vi.fn(), commit: vi.fn() }))
vi.mock('../api/client', () => ({
  businessImportApi: {
    preview: apiMocks.preview,
    commit: apiMocks.commit,
    templateUrl: (type: string) => `/api/orders/imports/template/?type=${type}`,
    errorReportUrl: (token: string) => `/api/orders/imports/${token}/errors/`,
  },
}))

function renderDrawer() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const invalidateSpy = vi.spyOn(client, 'invalidateQueries')
  const onClose = vi.fn()
  const result = render(<QueryClientProvider client={client}><App><BusinessImportDrawer open context="orders" onClose={onClose} /></App></QueryClientProvider>)
  return { ...result, invalidateSpy, onClose }
}

describe('BusinessImportDrawer', () => {
  beforeEach(() => {
    apiMocks.preview.mockReset()
    apiMocks.commit.mockReset()
  })

  it('shows detected record counts and blocks commit when preview has errors', async () => {
    apiMocks.preview.mockResolvedValue({
      token: 'token-1', source_type: 'MIXED', total_rows: 3,
      counts: { product_specifications: 1, orders: 1, material_receipts: 1, inspection_criteria: 0 },
      error_count: 1, warning_count: 1,
      rows: [{ row_key: 'order-1', record_type: 'ORDER', sheet: '订单', row: 2, action: 'CREATE', order_no: 'TEST-ORDER-001', valid: false }],
      issues: [{ level: 'error', sheet: '订单', row: 2, field: 'order_no', message: '订单编号重复' }],
    })
    const user = userEvent.setup()
    renderDrawer()
    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    await user.upload(input, new File(['excel'], 'customer.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }))
    await user.click(screen.getByRole('button', { name: /上传并自动识别/ }))

    expect(await screen.findByText('混合业务工作簿')).toBeInTheDocument()
    expect(screen.getByText('订单编号重复')).toBeInTheDocument()
    const commit = screen.getByRole('button', { name: /确认整批导入/ })
    expect(commit).toBeDisabled()
    await waitFor(() => expect(apiMocks.preview).toHaveBeenCalledTimes(1))
    expect(apiMocks.commit).not.toHaveBeenCalled()
  }, 15_000)

  it('commits incremental updates and reports created, updated, and skipped totals', async () => {
    apiMocks.preview.mockResolvedValue({
      token: 'token-2', source_type: 'FACTORY_WORK_CONTACT', total_rows: 1,
      counts: { product_specifications: 0, orders: 1, material_receipts: 0, inspection_criteria: 0 },
      error_count: 0, warning_count: 0,
      rows: [{
        row_key: 'order-2', record_type: 'ORDER', sheet: 'sheet1', row: 4,
        action: 'UPDATE', order_no: 'TEST-ORDER-002', item_no: '15', valid: true,
        changes: { due_date: { from: '2026-08-08', to: '2026-08-10' } },
      }],
      issues: [],
    })
    apiMocks.commit.mockResolvedValue({
      created: { orders: 1 },
      updated: { orders: 1, product_specifications: 1 },
      skipped: { material_receipts: 3 },
    })
    const user = userEvent.setup()
    const { invalidateSpy, onClose } = renderDrawer()

    expect(screen.getByText(/同一订单号与项次会增量更新现有订单/)).toBeInTheDocument()
    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    await user.upload(input, new File(['excel'], 'customer-update.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }))
    await user.click(screen.getByRole('button', { name: /上传并自动识别/ }))

    expect(await screen.findByText('更新')).toBeInTheDocument()
    expect(screen.getByText('交期：')).toBeInTheDocument()
    expect(screen.getByText('2026-08-08 → 2026-08-10')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /确认整批导入/ }))

    await waitFor(() => expect(apiMocks.commit).toHaveBeenCalledWith('token-2'))
    expect(await screen.findByText('业务数据导入完成：新增 1 条，更新 2 条，跳过 3 条')).toBeInTheDocument()
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
    const invalidatedKeys = invalidateSpy.mock.calls.map(([filters]) => filters?.queryKey)
    expect(invalidatedKeys).toEqual(expect.arrayContaining([
      ['product-specifications'],
      ['orders'],
      ['material-receipts'],
      ['quality'],
      ['production'],
      ['analytics'],
    ]))
  }, 15_000)
})
