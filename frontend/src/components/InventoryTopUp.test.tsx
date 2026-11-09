import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { InventoryTopUpHistory, InventoryTopUpModal } from './InventoryTopUp'
import { ReadOnlyContext } from '../access'
import { ApiError } from '../api/client'
import type { InventoryLocation } from '../types'

Object.defineProperty(window, 'matchMedia', { writable: true, value: vi.fn().mockImplementation((query: string) => ({ matches: query.includes('max-width'), media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })) })
class ResizeObserverMock { observe() {} unobserve() {} disconnect() {} }
globalThis.ResizeObserver = ResizeObserverMock
const mocks = vi.hoisted(() => ({ topUp: vi.fn(), history: vi.fn(), employees: vi.fn() }))
vi.mock('../api/client', () => ({
  ApiError: class extends Error { status: number; constructor(status: number, message: string) { super(message); this.status = status } },
  inventoryApi: { topUp: mocks.topUp, topUpHistory: mocks.history },
  qualityApi: { listEmployees: mocks.employees },
  toList: (value: any) => Array.isArray(value) ? value : value.results,
}))

const target = {
  id: 1, code: 'K01-L01-P01', rack_code: 'K01', rack_type: 'LARGE', level_no: 1, position_no: 1,
  label: '', is_active: true, allows_basket: true, allows_bag: true,
  container: { id: 81, container_code: 'CT-081', container_type: 'BASKET', batch_no: 'OLD-081',
    product_id: 362, product_code: 'P-362', product_name: '圈', specification: '362', material: 'NBR',
    quantity: 1000, quality_status: 'PASSED', bag_count: 1, inspector_name: '原品检员',
    updated_at: '2026-11-09T01:00:00Z' },
} as InventoryLocation

function mount(node: React.ReactNode, readOnly = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<ReadOnlyContext.Provider value={readOnly}><QueryClientProvider client={client}><App>{node}</App></QueryClientProvider></ReadOnlyContext.Provider>)
}

async function fill() {
  const user = userEvent.setup()
  const dialog = await screen.findByRole('dialog')
  await user.type(within(dialog).getByRole('spinbutton', { name: '本次增加数量（件）' }), '500')
  await user.click(within(dialog).getByRole('combobox', { name: '确认本次检验状态' }))
  await user.click(await screen.findByText('已检合格', { selector: '.ant-select-item-option-content' }))
  await user.click(within(dialog).getByRole('combobox', { name: '本次品检员' }))
  await user.click(await screen.findByText('本次品检员乙', { selector: '.ant-select-item-option-content' }))
  return { user, dialog }
}

describe('Inventory top-up', () => {
  beforeEach(() => {
    mocks.topUp.mockReset().mockResolvedValue({ quantity: 1500, top_up_receipt: { quantity: 500 }, replayed: false })
    mocks.employees.mockReset().mockResolvedValue([{ id: 2, name: '本次品检员乙', is_active: true }, { id: 3, name: '停用品检员', is_active: false }])
    mocks.history.mockReset().mockResolvedValue({ count: 0, results: [] })
  })

  it('shows mobile-safe fields, previews delta and sends independent inspector with immutable version', async () => {
    const close = vi.fn()
    mount(<InventoryTopUpModal target={target} onClose={close} />)
    const { user, dialog } = await fill()
    expect(dialog).toHaveClass('inventory-modal')
    expect(dialog.querySelectorAll('.ant-col-xs-24').length).toBeGreaterThan(0)
    expect(within(dialog).getByText('原有 1,000 ＋ 本次 500 ＝ 追加后 1,500 件')).toBeInTheDocument()
    expect(within(dialog).queryByText('原品检员')).not.toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: '确认追加' }))
    await waitFor(() => expect(close).toHaveBeenCalledOnce())
    expect(mocks.topUp).toHaveBeenCalledWith(81, expect.objectContaining({
      quantity: 500, inspector_id: 2, quality_status: 'PASSED', expected_quantity: 1000,
      expected_location_id: 1, expected_updated_at: '2026-11-09T01:00:00Z',
      product_identity: { id: 362, product_code: 'P-362', product_name: '圈', specification: '362', material: 'NBR' },
      received_on: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/), request_id: expect.any(String),
    }))
  }, 30000)

  it('does not inherit original inspector or default the quality confirmation', async () => {
    mount(<InventoryTopUpModal target={target} onClose={vi.fn()} />)
    const user = userEvent.setup()
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByRole('spinbutton', { name: '本次增加数量（件）' }), '10')
    await user.click(within(dialog).getByRole('button', { name: '确认追加' }))
    expect(await within(dialog).findByText('请确认本次与原筐的检验状态一致')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('combobox', { name: '确认本次检验状态' }))
    await user.click(await screen.findByText('已检合格', { selector: '.ant-select-item-option-content' }))
    await user.click(within(dialog).getByRole('button', { name: '确认追加' }))
    expect(await within(dialog).findByText('请选择本次品检员，不自动沿用原记录')).toBeInTheDocument()
    expect(mocks.topUp).not.toHaveBeenCalled()
  }, 30000)

  it('retries a network failure with the exact original request and key', async () => {
    mocks.topUp.mockRejectedValueOnce(new Error('网络中断'))
    const close = vi.fn()
    mount(<InventoryTopUpModal target={target} onClose={close} />)
    const { user, dialog } = await fill()
    await user.click(within(dialog).getByRole('button', { name: '确认追加' }))
    const retry = await within(dialog).findByRole('button', { name: '重试本次提交' })
    expect(within(dialog).getByRole('spinbutton', { name: '本次增加数量（件）' })).toBeDisabled()
    await user.click(retry)
    await waitFor(() => expect(close).toHaveBeenCalledOnce())
    expect(mocks.topUp).toHaveBeenCalledTimes(2)
    expect(mocks.topUp.mock.calls[1]).toEqual(mocks.topUp.mock.calls[0])
  }, 30000)

  it('requires refresh on conflict and prevents repeated confirm while pending', async () => {
    let reject!: (reason: Error) => void
    mocks.topUp.mockReturnValueOnce(new Promise((_, no) => { reject = no }))
    mount(<InventoryTopUpModal target={target} onClose={vi.fn()} />)
    const { user, dialog } = await fill()
    await user.click(within(dialog).getByRole('button', { name: '确认追加' }))
    await waitFor(() => expect(mocks.topUp).toHaveBeenCalledOnce())
    await user.click(within(dialog).getByRole('button', { name: /确认追加/ }))
    expect(mocks.topUp).toHaveBeenCalledOnce()
    reject(new ApiError(409, '原库存已经变化'))
    expect(await within(dialog).findByRole('button', { name: '重试本次提交' })).toBeDisabled()
  }, 30000)

  it('read-only accounts cannot confirm top-up', async () => {
    mount(<InventoryTopUpModal target={target} onClose={vi.fn()} />, true)
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('button', { name: '确认追加' })).toBeDisabled()
    expect(mocks.topUp).not.toHaveBeenCalled()
  })

  it('distinguishes loading, failed retrieval and real empty history with retry', async () => {
    let reject!: (reason: Error) => void
    mocks.history.mockReturnValueOnce(new Promise((_, no) => { reject = no }))
    mount(<InventoryTopUpHistory containerId={81} />)
    expect(screen.getByText('正在读取追加记录')).toBeInTheDocument()
    expect(screen.queryByText('暂无追加入库记录')).not.toBeInTheDocument()
    reject(new Error('读取失败'))
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: '重试追加记录' }))
    expect(await screen.findByText('暂无追加入库记录')).toBeInTheDocument()
  })

  it('shows the independent receipt facts and paginates history', async () => {
    mocks.history.mockResolvedValue({ count: 31, results: [{ id: 1, received_on: '2026-11-09',
      quantity: 500, quantity_before: 1000, quantity_after: 1500,
      product_snapshot: { specification: '362', material: 'NBR' }, quality_status: 'PASSED',
      inspector_name: '本次品检员乙', inspected_on: '2026-11-08', notes: '追加', created_by_name: '管理员' }] })
    const { container } = mount(<InventoryTopUpHistory containerId={81} />)
    expect(await screen.findByText('已检合格 · 本次品检员：本次品检员乙')).toBeInTheDocument()
    expect(screen.getByText('1,000 → 1,500 件（本次入库时）')).toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(container.querySelector('[title="2"]')!)
    await waitFor(() => expect(mocks.history).toHaveBeenCalledWith(81, 2))
  })
})
