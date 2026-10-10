import { App, Grid } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { OrdersPage } from './OrdersPage'

const apiMocks = vi.hoisted(() => ({ listOrders: vi.fn(), listReceipts: vi.fn() }))
vi.mock('../api/client', () => ({
  orderApi: { list: apiMocks.listOrders },
  materialReceiptApi: { list: apiMocks.listReceipts },
  toList: <T,>(payload: T[]) => payload,
}))
vi.mock('../components/OrderFormDrawer', () => ({ OrderFormDrawer: () => null }))
vi.mock('../components/MaterialReceiptDrawer', () => ({ MaterialReceiptDrawer: () => null }))
vi.mock('../components/BusinessImportDrawer', () => ({ BusinessImportDrawer: () => null }))
vi.mock('../components/BusinessImportHistoryDrawer', () => ({ BusinessImportHistoryDrawer: () => null }))

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
  })),
})
class ResizeObserverMock { observe() {} unobserve() {} disconnect() {} }
globalThis.ResizeObserver = ResizeObserverMock

function pageResponse(page = 1, pageSize = 20) {
  return {
    count: 50, next: page * pageSize < 50 ? `/orders/?page=${page + 1}` : null,
    previous: page > 1 ? `/orders/?page=${page - 1}` : null,
    results: Array.from({ length: Math.min(pageSize, 50 - (page - 1) * pageSize) }, (_, index) => {
      const id = (page - 1) * pageSize + index + 1
      return {
        id, order_no: `TEST-ORDER-${String(id).padStart(3, '0')}`, item_no: '5', specification: '240',
        material: 'N7200', product_name: 'O型圈', order_quantity: 1_000,
        order_date: '2026-10-01', due_date: '2026-10-30', status: 'OPEN',
        material_status: 'NOT_RECEIVED', process_card_status: 'RECEIVED',
        process_card_text: `CARD-${String(id).padStart(3, '0')}`, process_card_count: 1,
      }
    }),
  }
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><App><OrdersPage /></App></QueryClientProvider>)
}

describe('order server pagination and table navigation', () => {
  beforeEach(() => {
    apiMocks.listOrders.mockReset().mockImplementation(({ page = 1, page_size = 20 }) => Promise.resolve(pageResponse(page, page_size)))
    apiMocks.listReceipts.mockReset().mockResolvedValue({ count: 0, results: [] })
    vi.spyOn(Grid, 'useBreakpoint').mockReturnValue({ md: true, lg: true, xl: true })
  })
  afterEach(() => { cleanup(); vi.restoreAllMocks() })

  it('moves to pages 2 and 3 without treating the existing due-date sorter as a sort event', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('TEST-ORDER-001 / 5')
    await user.click(screen.getByTitle('2'))
    expect(await screen.findByText('TEST-ORDER-021 / 5')).toBeInTheDocument()
    expect(apiMocks.listOrders).toHaveBeenCalledWith(
      expect.objectContaining({ page: 2, page_size: 20, ordering: 'due_date,process_card_status,order_date' }),
      expect.objectContaining({ signal: expect.anything() }),
    )
    await user.click(screen.getByTitle('3'))
    expect(await screen.findByText('TEST-ORDER-041 / 5')).toBeInTheDocument()
    expect(screen.getByTitle('3')).toHaveClass('ant-pagination-item-active')
    expect(screen.queryByText('TEST-ORDER-001 / 5')).not.toBeInTheDocument()
    await user.click(screen.getByTitle('Previous Page').querySelector('button')!)
    expect(await screen.findByText('TEST-ORDER-021 / 5')).toBeInTheDocument()
  })

  it('keeps pagination and the previous rows visible while the next page loads', async () => {
    const user = userEvent.setup()
    let resolveNextPage: (value: ReturnType<typeof pageResponse>) => void = () => {}
    apiMocks.listOrders.mockImplementation(({ page }) => page === 2
      ? new Promise((resolve) => { resolveNextPage = resolve }) : Promise.resolve(pageResponse()))
    renderPage()
    await screen.findByText('TEST-ORDER-001 / 5')
    await user.click(screen.getByTitle('2'))
    await waitFor(() => expect(apiMocks.listOrders).toHaveBeenCalledWith(
      expect.objectContaining({ page: 2 }), expect.anything(),
    ))
    expect(screen.getByTitle('2')).toHaveClass('ant-pagination-item-active')
    expect(screen.getByText('TEST-ORDER-001 / 5')).toBeInTheDocument()
    expect(document.querySelector('.orders-table .ant-spin-spinning')).toBeInTheDocument()
    await act(async () => { resolveNextPage(pageResponse(2)) })
    expect(await screen.findByText('TEST-ORDER-021 / 5')).toBeInTheDocument()
    expect(screen.queryByText('TEST-ORDER-001 / 5')).not.toBeInTheDocument()
  })

  it('resets the page for a genuine column sort, not for pagination', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('TEST-ORDER-001 / 5')
    await user.click(screen.getByTitle('2'))
    await screen.findByText('TEST-ORDER-021 / 5')
    await user.click(screen.getByRole('columnheader', { name: /交期/ }))
    await waitFor(() => expect(apiMocks.listOrders).toHaveBeenCalledWith(
      expect.objectContaining({ page: 1, ordering: '-due_date,process_card_status,order_date' }), expect.anything(),
    ))
    expect(await screen.findByText('TEST-ORDER-001 / 5')).toBeInTheDocument()
  })

  it('resets the page when search criteria change and retains the sorting choice', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('TEST-ORDER-001 / 5')
    await user.click(screen.getByTitle('3'))
    await screen.findByText('TEST-ORDER-041 / 5')
    await user.type(screen.getByPlaceholderText('搜索订单号、项次、产品、规格、材质或批次'), '240 N7200')
    await waitFor(() => expect(apiMocks.listOrders).toHaveBeenCalledWith(
      expect.objectContaining({ page: 1, q: '240 N7200', ordering: 'due_date,process_card_status,order_date' }),
      expect.anything(),
    ))
  })

  it('does not display the previous product results while a different search loads', async () => {
    const user = userEvent.setup()
    let resolveSearch: (value: ReturnType<typeof pageResponse>) => void = () => {}
    apiMocks.listOrders.mockImplementation(({ page, page_size, q }) => q
      ? new Promise((resolve) => { resolveSearch = resolve }) : Promise.resolve(pageResponse(page, page_size)))
    renderPage()
    await screen.findByText('TEST-ORDER-001 / 5')
    await user.click(screen.getByTitle('3'))
    await screen.findByText('TEST-ORDER-041 / 5')
    await user.type(screen.getByPlaceholderText('搜索订单号、项次、产品、规格、材质或批次'), '449')
    await waitFor(() => expect(apiMocks.listOrders).toHaveBeenCalledWith(
      expect.objectContaining({ page: 1, q: '449' }), expect.anything(),
    ))
    expect(screen.queryByText('TEST-ORDER-041 / 5')).not.toBeInTheDocument()
    expect(screen.queryByText('TEST-ORDER-001 / 5')).not.toBeInTheDocument()
    await act(async () => { resolveSearch(pageResponse()) })
    expect(await screen.findByText('TEST-ORDER-001 / 5')).toBeInTheDocument()
  })

  it.each([false, true])('resets to page 1 when page size changes and keeps the size selector (mobile: %s)', async (mobile) => {
    vi.mocked(Grid.useBreakpoint).mockReturnValue({ md: !mobile, lg: !mobile, xl: !mobile })
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('TEST-ORDER-001 / 5')
    await user.click(screen.getByTitle('3'))
    await screen.findByText('TEST-ORDER-041 / 5')
    await user.click(document.querySelector('.ant-pagination-options .ant-select')!)
    await user.click(await screen.findByText('100 / page'))
    await waitFor(() => expect(apiMocks.listOrders).toHaveBeenCalledWith(
      expect.objectContaining({ page: 1, page_size: 100 }), expect.anything(),
    ))
    expect(await screen.findByText('TEST-ORDER-001 / 5')).toBeInTheDocument()
    expect(document.querySelector('.ant-pagination-options .ant-select')).toBeInTheDocument()
    await user.click(document.querySelector('.ant-pagination-options .ant-select')!)
    await user.click(await screen.findByText('20 / page'))
    await waitFor(() => expect(apiMocks.listOrders).toHaveBeenLastCalledWith(
      expect.objectContaining({ page: 1, page_size: 20 }), expect.anything(),
    ))
    expect(screen.getByTitle('3')).toBeInTheDocument()
  })

  it('fixes the four identity columns on the left and renders a sticky header on desktop', async () => {
    renderPage()
    await screen.findByText('TEST-ORDER-001 / 5')
    for (const name of ['订单号 / 项次', '流程卡', '规格 / 产品', '材质']) {
      expect(screen.getByRole('columnheader', { name })).toHaveClass('ant-table-cell-fix-start')
    }
    expect(document.querySelector('.orders-table .ant-table-sticky-holder')).toBeInTheDocument()
  })

  it('supports mobile pagination without a horizontal table', async () => {
    vi.mocked(Grid.useBreakpoint).mockReturnValue({ md: false, lg: false, xl: false })
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('TEST-ORDER-001 / 5')
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    await user.click(screen.getByTitle('3'))
    expect(await screen.findByText('TEST-ORDER-041 / 5')).toBeInTheDocument()
    expect(screen.getByTitle('3')).toHaveClass('ant-pagination-item-active')
    expect(screen.queryByText('TEST-ORDER-001 / 5')).not.toBeInTheDocument()
  })
})
