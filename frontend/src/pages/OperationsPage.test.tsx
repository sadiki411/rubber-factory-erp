import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { OperationsPage } from './OperationsPage'
import { OrderOverviewPage } from './OrderOverviewPage'
import type { OrderOverviewResult, WorkbenchResult } from '../operations'

const api = vi.hoisted(() => ({ workbench: vi.fn(), overview: vi.fn() }))
vi.mock('../api/client', () => ({ orderApi: api }))
Object.defineProperty(window, 'matchMedia', { writable: true, value: vi.fn().mockImplementation((media: string) => ({
  matches: false, media, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
})) })
class ResizeObserverMock { observe() {} unobserve() {} disconnect() {} }
globalThis.ResizeObserver = ResizeObserverMock

const tasks: WorkbenchResult = {
  count: 41, page: 2, page_size: 20,
  summary: { ORDER_SHORTAGE: 41, RETURN_PENDING: 8, INVENTORY_QUALITY: 4, RECEIPT_UNLINKED: 22 },
  summary_scope: '全部匹配搜索的事项，不受分页影响', generated_at: '2026-11-04T12:00:00+08:00', limitations: [],
  results: [{ id: 'order:2', kind: 'ORDER_SHORTAGE', title: '订单欠交', document_no: 'DEMO-B / 2', specification: '240', material: 'N7200', quantity: 40, unit: '件', due_date: null, order_id: 2, owner: null, reason: '净有效交付后欠40件', source: '明确订单', url: '/order-overview/2' }],
}
const detail: OrderOverviewResult = {
  order: { id: 2, order_no: 'DEMO-B', item_no: '2', specification: '240', material: 'N7200', product_name: '测试圈', status: 'OPEN', due_date: null, order_quantity: 200 },
  summary: { net_delivered: 160, delivery_remaining: 40, production_progress_quantity: 200, production_status: 'PRODUCED' },
  counts: { production: 1, receipts: 0, cards: 0, shipments: 35, piece_shipments: 0, returns: 1, piece_returns: 0, inventory: 0 },
  section: 'shipments', count: 35, page: 1, page_size: 20, generated_at: tasks.generated_at,
  limitations: ['库存独立，不自动扣库'],
  results: [{ id: 7, source: 'QualityShipmentLine', document_no: 'DEMO-SH', date: null, status: '已确认', physical_quantity: 300, own_quantity: 200, unit: '件', details: '本单原归属尚未扣退回', cards: ['DEMO-CARD'], url: '/quality?shipment_batch_id=1' }],
}
function Location() { const location = useLocation(); return <output aria-label="当前地址">{location.pathname}{location.search}</output> }
function renderPage(url = '/operations?q=240&kind=ORDER_SHORTAGE&page=2') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[url]}><Location /><Routes>
    <Route path="/operations" element={<OperationsPage />} />
    <Route path="/order-overview/:id" element={<OrderOverviewPage />} />
  </Routes></MemoryRouter></QueryClientProvider>)
}
describe('read-only operations and explicit order trace', () => {
  beforeEach(() => { api.workbench.mockReset().mockResolvedValue(tasks); api.overview.mockReset().mockResolvedValue(detail) })
  afterEach(() => cleanup())

  it('does not show zero or an empty conclusion before reading finishes, and distinguishes failure', async () => {
    let fail: (error: Error) => void = () => {}
    api.workbench.mockReturnValue(new Promise((_, reject) => { fail = reject }))
    renderPage()
    expect(screen.queryByText(/订单欠交 0/)).not.toBeInTheDocument()
    expect(screen.queryByText(/没有已登记的待处理项/)).not.toBeInTheDocument()
    expect(document.querySelector('.ant-skeleton')).toBeInTheDocument()
    await act(async () => fail(new Error('隔离断网')))
    expect(await screen.findByText('待办读取失败，不能将失败视为0项')).toBeInTheDocument()
    expect(screen.queryByText(/订单欠交 0/)).not.toBeInTheDocument()
  })

  it('shows full-scope counts and restores search, kind and page after opening an order', async () => {
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByText('订单欠交 41')).toBeInTheDocument()
    expect(screen.getByText('退回/返工待处理 8')).toBeInTheDocument()
    expect(screen.getByText('共 41 项')).toBeInTheDocument()
    expect(screen.getByText(/不推定责任人/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '看订单' }))
    expect(await screen.findByText('DEMO-B / 2')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '返回待办筛选' }))
    await waitFor(() => expect(screen.getByLabelText('当前地址')).toHaveTextContent('/operations?q=240&kind=ORDER_SHORTAGE&page=2'))
    expect(api.workbench).toHaveBeenCalledWith({ q: '240', kind: 'ORDER_SHORTAGE', page: 2, page_size: 20 }, expect.objectContaining({ signal: expect.anything() }))
  })

  it('separates physical, own-order and net quantities, with server totals and explicit missing values', async () => {
    const user = userEvent.setup()
    renderPage('/order-overview/2?section=shipments')
    expect(await screen.findByText('物理/原记录量：300 件', { exact: false })).toBeInTheDocument()
    expect(screen.getByText('本项次归属量：200 件', { exact: false })).toBeInTheDocument()
    expect(screen.getByText('净有效交付')).toBeInTheDocument()
    expect(screen.getByText('重量/卡出货（35）')).toBeInTheDocument()
    expect(screen.getByText('共 35 条原记录')).toBeInTheDocument()
    api.overview.mockResolvedValue({ ...detail, count: 1, section: 'returns', results: [{ ...detail.results[0], own_quantity: null, details: '实际作业0条，未登记不代表未返工' }] })
    await user.click(screen.getByRole('tab', { name: '退回/再发（1）' }))
    expect(await screen.findByText(/本项次归属量：未登记\/不适用/)).toBeInTheDocument()
    expect(screen.getByText(/未登记不代表未返工/)).toBeInTheDocument()
    expect(api.overview).toHaveBeenLastCalledWith(2, { section: 'returns', page: 1, page_size: 20 }, expect.anything())
  })

  it('rejects invalid order IDs instead of displaying an endless loading state', async () => {
    renderPage('/order-overview/not-an-id')
    expect(screen.getByText('订单编号无效')).toBeInTheDocument()
    expect(api.overview).not.toHaveBeenCalled()
  })
})
