import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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
  returnAllocationPreview: vi.fn(),
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
      <button type="button" onClick={() => { for (let i = 1; i <= 20; i++) void onScan(`04-M003-261013${String(i).padStart(4, '0')}`) }}>加入20张测试卡</button>
      <button type="button" onClick={() => { for (let i = 1; i <= 50; i++) void onScan(`04-M003-261013${String(i).padStart(4, '0')}`) }}>加入50张测试卡</button>
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
  it.each([false, true])('reconciles a lost submission response without guessing success (committed=%s)', async (committed) => {
    const user = userEvent.setup()
    const cardNo = 'CARD-LOST-RESPONSE'
    const lookup = { found: true, scanned_card: { id: 1, card_no: cardNo, order_id: 107,
      unit_binding: { shipment_batch_id: 1, shipment_unit_no: 1, inspectors: [{ id: 1, name: '王品检' }] } } }
    apiMocks.listReturnReasons.mockResolvedValue([{ id: 1, name: '拔破' }])
    apiMocks.scanProcessCard.mockResolvedValue(lookup)
    apiMocks.scanReturn.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    renderDrawer()
    fireEvent.change(await screen.findByPlaceholderText('输入退货流程卡'), { target: { value: cardNo } })
    fireEvent.click(screen.getByRole('button', { name: '加入退货卡' }))
    await screen.findByText('已锁定原出货')
    await user.click(screen.getByLabelText('主要退货原因'))
    await user.click(await screen.findByText('拔破'))
    fireEvent.click(screen.getByRole('button', { name: /确认登记/ }))
    await screen.findByText('提交结果未确认，不能直接再次登记')
    expect(screen.getByText(cardNo)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /确认登记/ })).toBeDisabled()
    apiMocks.scanProcessCard.mockRejectedValueOnce(new TypeError('still offline'))
    fireEvent.click(screen.getByRole('button', { name: '重新核对提交结果' }))
    await screen.findByText(/提交结果仍未确认，队列已保留/)
    expect(screen.getByRole('button', { name: /确认登记/ })).toBeDisabled()
    apiMocks.scanProcessCard.mockResolvedValue(committed ? { ...lookup,
      current_return: { id: 99, case_no: 'R99', status: 'WAITING_REWORK' } } : lookup)
    fireEvent.click(screen.getByRole('button', { name: '重新核对提交结果' }))
    await waitFor(() => expect(screen.queryByText('提交结果未确认，不能直接再次登记')).not.toBeInTheDocument())
    if (committed) {
      expect(screen.getByText(/服务器已有退货记录 R99/)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /确认登记/ })).toBeDisabled()
      expect(apiMocks.scanReturn).toHaveBeenCalledTimes(1)
    } else {
      expect(screen.getByRole('button', { name: /确认登记/ })).toBeEnabled()
      fireEvent.click(screen.getByRole('button', { name: /确认登记/ }))
      await waitFor(() => expect(apiMocks.scanReturn).toHaveBeenCalledTimes(2))
    }
  }, 30_000)
  it.each([20, 50])('pages a %s-card queue without dropping any card from the bulk submission', async (size) => {
    const user = userEvent.setup()
    apiMocks.listReturnReasons.mockResolvedValue([{ id: 1, name: '拔破', is_active: true }])
    apiMocks.scanProcessCard.mockImplementation(async (cardNo: string) => ({ found: true, scanned_card: { id: cardNo, card_no: cardNo, order_id: 107, unit_binding: { shipment_batch_id: Number(cardNo.slice(-4)), shipment_unit_no: 1, inspectors: [{ id: 1, name: '王品检' }] } } }))
    renderDrawer()
    fireEvent.click(await screen.findByRole('button', { name: `加入${size}张测试卡` }))
    await waitFor(() => expect(apiMocks.scanProcessCard).toHaveBeenCalledTimes(size))
    expect(await screen.findByText(new RegExp(`共 ${size} 批，分页仅影响显示`))).toBeInTheDocument()
    expect(screen.queryByText('04-M003-2610130001', { exact: true })).not.toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: /确认登记/ })).toBeEnabled())
    await user.click(screen.getByLabelText('主要退货原因'))
    await user.click(await screen.findByText('拔破'))
    fireEvent.click(screen.getByRole('button', { name: /确认登记/ }))
    await waitFor(() => expect(apiMocks.bulkScanReturn).toHaveBeenCalled())
    expect(apiMocks.bulkScanReturn.mock.calls[0][0].cards).toHaveLength(size)
  }, 30_000)
  it.each([20, 50])('blocks all %s cards when an off-page card is pending or fails', async (size) => {
    let reject!: (reason: Error) => void
    apiMocks.scanProcessCard.mockImplementation((cardNo: string) => cardNo.endsWith('0001')
      ? new Promise((_, fail) => { reject = fail })
      : Promise.resolve({ found: true, scanned_card: { id: cardNo, card_no: cardNo,
        unit_binding: { shipment_batch_id: Number(cardNo.slice(-4)), shipment_unit_no: 1, inspectors: [{ id: 1, name: '王品检' }] } } }))
    renderDrawer()
    fireEvent.click(await screen.findByRole('button', { name: `加入${size}张测试卡` }))
    await waitFor(() => expect(apiMocks.scanProcessCard).toHaveBeenCalledTimes(size))
    expect(screen.queryByText('04-M003-2610130001', { exact: true })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /确认登记/ })).toBeDisabled()
    reject(new Error('first card failed off-page'))
    await screen.findByText('部分流程卡核对失败')
    expect(screen.getByRole('button', { name: /确认登记/ })).toBeDisabled()
    expect(apiMocks.bulkScanReturn).not.toHaveBeenCalled()
  }, 30_000)
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.listReturnReasons.mockResolvedValue([])
    apiMocks.listReturnableBatches.mockResolvedValue([])
    apiMocks.scanReturn.mockResolvedValue({ id: 1 })
    apiMocks.bulkScanReturn.mockResolvedValue({ results: [] })
  })

  it('keeps the queue and displays required fields when confirmation is incomplete', async () => {
    apiMocks.scanProcessCard.mockResolvedValue({ found: true, scanned_card: { id: 1, card_no: 'CARD-MISSING-REASON', order_id: 107, unit_binding: { shipment_batch_id: 1, shipment_unit_no: 1, inspectors: [{ id: 1, name: '王品检' }] } } })
    renderDrawer()
    fireEvent.change(await screen.findByPlaceholderText('输入退货流程卡'), { target: { value: 'CARD-MISSING-REASON' } })
    fireEvent.click(screen.getByRole('button', { name: '加入退货卡' }))
    await screen.findByText('已锁定原出货')
    fireEvent.click(screen.getByRole('button', { name: /确认登记/ }))
    expect(await screen.findByText('请选择一个主要原因')).toBeInTheDocument()
    expect(screen.getByText('CARD-MISSING-REASON')).toBeInTheDocument()
    expect(apiMocks.scanReturn).not.toHaveBeenCalled()
    expect(apiMocks.bulkScanReturn).not.toHaveBeenCalled()
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
    expect(screen.queryByText('首次扫描，尚未绑定订单')).not.toBeInTheDocument()
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
          order_allocations: [
            { order_id: 107, order_no: 'XB-TEST', item_no: '10', piece_quantity: 1000, net_weight_kg: '3.752' },
            { order_id: 108, order_no: 'XB-TEST', item_no: '11', piece_quantity: 306, net_weight_kg: '1.148' },
          ],
          inspectors: [{ id: 1, employee_no: 'Q001', name: '王品检', role: 'INSPECTOR', is_active: true }],
        },
      },
    })

    expect(await screen.findByText('已锁定原出货')).toBeInTheDocument()
    expect(screen.getByText('责任品检员已从原出货自动带入')).toBeInTheDocument()
    expect(screen.getByText(/原出货 QS-20260927-BA598527/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /确认登记/ })).toBeEnabled()
    expect(apiMocks.listReturnableBatches).not.toHaveBeenCalled()
    await user.click(screen.getByText('查看实际交付分摊及本次整批回冲'))
    expect(await screen.findByText(/1,000 件/)).toBeInTheDocument()
    expect(screen.getByText(/306 件/)).toBeInTheDocument()
  })

  it('offers the added reasons as primary and secondary tags and submits their IDs', async () => {
    const user = userEvent.setup()
    const reasons = ['拔破', '空洞', '脏', '变形'].map((name, index) => ({ id: index + 1, name, is_active: true }))
    apiMocks.listReturnReasons.mockResolvedValue(reasons)
    apiMocks.scanProcessCard.mockResolvedValue({
      found: true,
      scanned_card: {
        id: 220, card_no: 'CARD-REASONS', order_id: 107,
        unit_binding: {
          shipment_batch_id: 386, shipment_unit_no: 1,
          inspectors: [{ id: 1, name: '王品检' }],
        },
      },
    })
    renderDrawer()
    await user.type(await screen.findByPlaceholderText('输入退货流程卡'), 'CARD-REASONS')
    await user.click(screen.getByRole('button', { name: '加入退货卡' }))
    await screen.findByText('已锁定原出货')
    await user.click(screen.getByLabelText('主要退货原因'))
    for (const reason of reasons) expect(await screen.findByText(reason.name)).toBeInTheDocument()
    await user.click(screen.getByText('拔破'))
    await user.click(screen.getByLabelText(/次要问题标签/))
    await user.click(screen.getAllByText('空洞').at(-1)!)
    await user.click(screen.getAllByText('脏').at(-1)!)
    await user.click(screen.getAllByText('变形').at(-1)!)
    await user.click(screen.getByText('本次共同信息'))
    await user.click(screen.getByRole('button', { name: /确认登记/ }))
    await waitFor(() => expect(apiMocks.scanReturn).toHaveBeenCalledWith(
      expect.objectContaining({ primary_reason_id: 1, secondary_reason_ids: [2, 3, 4] }),
    ))
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
