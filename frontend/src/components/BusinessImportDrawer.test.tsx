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
  return render(<QueryClientProvider client={client}><App><BusinessImportDrawer open context="orders" onClose={vi.fn()} /></App></QueryClientProvider>)
}

describe('BusinessImportDrawer', () => {
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
})
