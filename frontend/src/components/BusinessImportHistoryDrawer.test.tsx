import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BusinessImportHistoryDrawer } from './BusinessImportHistoryDrawer'

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

const apiMocks = vi.hoisted(() => ({ history: vi.fn(), historyDetail: vi.fn() }))
vi.mock('../api/client', () => ({
  businessImportApi: {
    history: apiMocks.history,
    historyDetail: apiMocks.historyDetail,
    errorReportUrl: (token: string) => `/api/orders/imports/${token}/errors/`,
  },
  toList: (payload: unknown[] | { results: unknown[] }) => Array.isArray(payload) ? payload : payload.results,
}))

function renderDrawer() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <App><BusinessImportHistoryDrawer open onClose={vi.fn()} /></App>
    </QueryClientProvider>,
  )
}

describe('BusinessImportHistoryDrawer', () => {
  beforeEach(() => {
    apiMocks.history.mockReset()
    apiMocks.historyDetail.mockReset()
  })

  it('shows persisted failed and skipped reasons from an import batch', async () => {
    const summary = {
      token: 'batch-1',
      original_name: 'NBR-T3(7-23).xlsx',
      source_type: 'FACTORY_WORK_CONTACT',
      source_type_display: '生产工作联络单',
      parser: 'openpyxl',
      status: 'COMMITTED',
      status_display: '已导入',
      created_at: '2026-08-02T08:30:00+08:00',
      committed_at: '2026-08-02T08:31:00+08:00',
      total_rows: 3,
      counts: { product_specifications: 0, orders: 3, material_receipts: 0, inspection_criteria: 0 },
      actions: { CREATE: 2, UPDATE: 0, SKIP: 1 },
      error_count: 0,
      warning_count: 2,
    }
    apiMocks.history.mockResolvedValue([summary])
    apiMocks.historyDetail.mockResolvedValue({
      ...summary,
      issues: [{ level: 'warning', sheet: 'sheet1', row: 8, field: 'skip_reason', message: '订单号和项次已存在。' }],
      rows: [{
        row_key: 'sheet1:8:ORDER', record_type: 'ORDER', sheet: 'sheet1', row: 8,
        action: 'SKIP', order_no: '04-A001-2607230001', item_no: '20',
        summary: '04-A001-2607230001 / 20', changes: {}, valid: true,
        skip_reason: '该订单号和项次已存在，且业务数据没有变化。',
        reasons: ['该订单号和项次已存在，且业务数据没有变化。'], issues: [],
      }],
    })

    const user = userEvent.setup()
    renderDrawer()
    expect(await screen.findByText('NBR-T3(7-23).xlsx')).toBeInTheDocument()
    expect(apiMocks.history).toHaveBeenCalledWith({ page: 1, page_size: 20 })
    expect(screen.getByText('新增 2 · 更新 0 · 跳过 1')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /查看原因/ }))

    expect(await screen.findByText('该订单号和项次已存在，且业务数据没有变化。')).toBeInTheDocument()
    expect(screen.getByText('订单号和项次已存在。')).toBeInTheDocument()
    await waitFor(() => expect(apiMocks.historyDetail).toHaveBeenCalledWith('batch-1'))
  })
})
