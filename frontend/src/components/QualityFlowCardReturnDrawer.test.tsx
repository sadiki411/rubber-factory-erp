import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import React from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { QualityFlowCardReturnDrawer } from './QualityFlowCardReturnDrawer'

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
  listReturnReasons: vi.fn(),
  listReturnableBatches: vi.fn(),
  scanProcessCard: vi.fn(),
  scanReturn: vi.fn(),
  bulkScanReturn: vi.fn(),
}))

vi.mock('../api/client', () => ({
  qualityWorkflowApi: apiMocks,
  toList: <T,>(payload: T[] | { results?: T[] }) => Array.isArray(payload) ? payload : payload.results || [],
}))

vi.mock('./QualityQrScanner', () => ({
  QualityQrScanner: function QualityQrScannerTestDouble({
    open,
    onScan,
  }: {
    open: boolean
    onScan: (cardNo: string) => boolean | void | Promise<boolean | void>
  }) {
    const [value, setValue] = React.useState('')
    if (!open) return null
    return <section role="dialog" aria-label="连续扫描退货流程卡">
      <input value={value} onChange={(event) => setValue(event.target.value)} placeholder="输入退货流程卡" />
      <button type="button" onClick={() => void onScan(value)}>加入退货卡</button>
    </section>
  },
}))

function renderDrawer() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <App>
        <QualityFlowCardReturnDrawer
          open
          employees={[]}
          onClose={vi.fn()}
          onSaved={vi.fn()}
          onBackfillShipment={vi.fn()}
        />
      </App>
    </QueryClientProvider>,
  )
}

describe('QualityFlowCardReturnDrawer scan performance', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.listReturnReasons.mockResolvedValue([])
    apiMocks.listReturnableBatches.mockResolvedValue([])
    apiMocks.scanReturn.mockResolvedValue({ id: 1 })
    apiMocks.bulkScanReturn.mockResolvedValue({ results: [] })
  })

  it('shows the scanned card immediately while a slow lookup is still pending', async () => {
    const user = userEvent.setup()
    const cardNo = '04-M003-2608270020'
    let resolveScan!: (value: unknown) => void
    apiMocks.scanProcessCard.mockImplementation(() => new Promise((resolve) => {
      resolveScan = resolve
    }))
    renderDrawer()

    await user.type(await screen.findByPlaceholderText('输入退货流程卡'), cardNo)
    await user.click(screen.getByRole('button', { name: '加入退货卡' }))

    expect(await screen.findByText(cardNo)).toBeInTheDocument()
    expect(screen.getByText('校验中')).toBeInTheDocument()
    expect(screen.getByText('正在核对原出货责任品检员')).toBeInTheDocument()
    expect(screen.queryByText('责任品检员已从原出货自动带入')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /确认登记/ })).toBeDisabled()
    expect(apiMocks.listReturnableBatches).not.toHaveBeenCalled()

    resolveScan({
      found: true,
      scanned_card: {
        id: 220,
        card_no: cardNo,
        order_id: 107,
        unit_binding: {
          id: 220,
          shipment_batch_id: 386,
          shipment_no: 'QS-20260927-BA598527',
          shipment_unit_no: 1,
          order_no: 'XB-TEST',
          item_no: '10',
          piece_quantity: 1306,
          net_weight_kg: '4.900',
          inspectors: [{ id: 1, employee_no: 'Q001', name: '王品检', role: 'INSPECTOR', is_active: true }],
        },
      },
    })

    expect(await screen.findByText('已锁定原出货')).toBeInTheDocument()
    expect(screen.getByText('责任品检员已从原出货自动带入')).toBeInTheDocument()
    expect(screen.getByText(/原出货 QS-20260927-BA598527/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /确认登记/ })).toBeEnabled()
    expect(apiMocks.listReturnableBatches).not.toHaveBeenCalled()
  })

  it('loads historical candidates only after a validated card really has no binding', async () => {
    const user = userEvent.setup()
    const cardNo = '04-M003-2608270099'
    apiMocks.scanProcessCard.mockResolvedValue({ found: false, card_no: cardNo, binding_required: true })
    renderDrawer()

    expect(apiMocks.listReturnableBatches).not.toHaveBeenCalled()
    await user.type(await screen.findByPlaceholderText('输入退货流程卡'), cardNo)
    await user.click(screen.getByRole('button', { name: '加入退货卡' }))

    expect(await screen.findByText('首次绑定')).toBeInTheDocument()
    await waitFor(() => expect(apiMocks.listReturnableBatches).toHaveBeenCalledWith({ page_size: 200 }))
  })

  it('retains failed cards for explicit retry without falling back to first binding', async () => {
    const user = userEvent.setup()
    const cardNo = '04-M003-2608270088'
    apiMocks.scanProcessCard.mockRejectedValue(new Error('服务器校验失败'))
    renderDrawer()

    await user.type(await screen.findByPlaceholderText('输入退货流程卡'), cardNo)
    await user.click(screen.getByRole('button', { name: '加入退货卡' }))

    expect(await screen.findByText('核对失败')).toBeInTheDocument()
    expect(screen.getByText(cardNo)).toBeInTheDocument()
    expect(screen.queryByText('首次绑定')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /确认登记/ })).toBeDisabled()
    expect(apiMocks.listReturnableBatches).not.toHaveBeenCalled()
    apiMocks.scanProcessCard.mockResolvedValue({ found: false, card_no: cardNo, binding_required: true })
    await user.click(screen.getByRole('button', { name: '重试此卡' }))
    expect(await screen.findByText('首次绑定')).toBeInTheDocument()
    expect(apiMocks.scanProcessCard).toHaveBeenCalledTimes(2)
  })
})
