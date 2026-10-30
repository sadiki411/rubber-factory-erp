import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { Order, OrderFinalYield } from '../types'
import { OrderFinalYieldDrawer, OrderYieldCell } from './OrderFinalYieldDrawer'

const mocks = vi.hoisted(() => ({ read: vi.fn(), save: vi.fn() }))
vi.mock('../api/client', () => ({ orderApi: { finalYield: mocks.read, saveFinalYield: mocks.save } }))
const order = { id: 1, order_no: 'TEST-YIELD', item_no: '1', status: 'COMPLETED', specification: '240', material: 'N7200' } as Order
const base: OrderFinalYield = {
  group_key: 'a'.repeat(64), source_token: 'b'.repeat(64), order_ids: [1], run_ids: [10], orders: [order],
  combined: false, status: 'READY', production_quantity: 1000, effective_shipped_quantity: 900,
  remaining_quantity: 0, remaining_recorded: false, saved_remaining_quantity: null,
  scope_changed: false, source_changed: false, yield_percent: '90.00', over_100: false,
  notes: '', confirmed_at: null, confirmed_by_name: null,
}

Object.defineProperty(window, 'matchMedia', { writable: true, value: vi.fn().mockImplementation((query: string) => ({
  matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(),
  addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
})) })
class ResizeObserverMock { observe() {} unobserve() {} disconnect() {} }
globalThis.ResizeObserver = ResizeObserverMock

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const onClose = vi.fn()
  render(<QueryClientProvider client={client}><App><OrderFinalYieldDrawer open order={order} onClose={onClose} /></App></QueryClientProvider>)
  return { onClose, client }
}

async function readyInput() {
  await waitFor(() => expect(screen.getByRole('button', { name: '保存剩余并重算' })).toBeEnabled())
  return screen.getByRole('spinbutton', { name: /本次剩余合格成品数量/ })
}

describe('completed order yield', () => {
  beforeEach(() => { mocks.read.mockReset().mockResolvedValue(base); mocks.save.mockReset().mockResolvedValue(base) })
  afterEach(cleanup)

  it('does not show a fake zero percentage while loading or enable save', async () => {
    mocks.read.mockReturnValue(new Promise(() => {}))
    show()
    expect(screen.getByRole('button', { name: '保存剩余并重算' })).toBeDisabled()
    expect(document.querySelector('.ant-statistic')).not.toBeInTheDocument()
  })

  it('shows an explicit read error instead of a percentage and allows retry', async () => {
    mocks.read.mockRejectedValueOnce(new Error('网络中断')).mockResolvedValueOnce(base)
    show()
    await screen.findByText('网络中断')
    expect(document.querySelector('.ant-statistic')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '保存剩余并重算' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: /重\s*试/ }))
    await waitFor(() => expect(screen.getByRole('button', { name: '保存剩余并重算' })).toBeEnabled())
  })

  it('previews and saves the manual remainder with its current source token', async () => {
    const { onClose } = show()
    const input = await readyInput()
    await userEvent.clear(input)
    await userEvent.type(input, '50')
    expect(document.querySelector('.ant-statistic-content-value')).toHaveTextContent('95.00')
    await userEvent.click(screen.getByRole('button', { name: '保存剩余并重算' }))
    await waitFor(() => expect(mocks.save).toHaveBeenCalledWith(1, {
      remaining_quantity: 50, notes: '', source_token: base.source_token,
    }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it('clearing the optional remainder saves explicit zero', async () => {
    show()
    const input = await readyInput()
    await userEvent.clear(input)
    await userEvent.click(screen.getByRole('button', { name: '保存剩余并重算' }))
    await waitFor(() => expect(mocks.save).toHaveBeenCalledWith(1, expect.objectContaining({ remaining_quantity: 0 })))
  })

  it('locks before async validation so rapid double clicks cannot submit twice', async () => {
    let resolveSave: (value: OrderFinalYield) => void = () => {}
    mocks.save.mockImplementation(() => new Promise(resolve => { resolveSave = resolve }))
    const { onClose } = show()
    await readyInput()
    await userEvent.dblClick(screen.getByRole('button', { name: '保存剩余并重算' }))
    await waitFor(() => expect(mocks.save).toHaveBeenCalledTimes(1))
    resolveSave(base)
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(mocks.save).toHaveBeenCalledTimes(1)
  })

  it('does not close or discard the typed remainder after a source conflict', async () => {
    mocks.save.mockRejectedValue(new Error('生产、出货或剩余数量已变化，请重新读取后再确认。'))
    const { onClose } = show()
    const input = await readyInput()
    await userEvent.clear(input)
    await userEvent.type(input, '50')
    await userEvent.click(screen.getByRole('button', { name: '保存剩余并重算' }))
    await screen.findByText('生产、出货或剩余数量已变化，请重新读取后再确认。')
    expect(onClose).not.toHaveBeenCalled()
    expect(input).toHaveValue('50')
  })

  it('shows the combined group and warns when old scope remainders were ignored', async () => {
    mocks.read.mockResolvedValue({ ...base, combined: true, orders: [order, { ...order, id: 2, order_no: 'SECOND-YIELD' }],
      order_ids: [1, 2], scope_changed: true, saved_remaining_quantity: 50 })
    show()
    await screen.findByText('合并生产良品率')
    expect(screen.getByText('生产范围已变化')).toBeInTheDocument()
    expect(screen.getByText(/原剩余50件未沿用/)).toBeInTheDocument()
    expect(document.querySelector('.ant-statistic-content-value')).toHaveTextContent('90.00')
  })

  it('does not render a percentage when no production was recorded', async () => {
    mocks.read.mockResolvedValue({ ...base, status: 'NO_PRODUCTION', production_quantity: 0, yield_percent: null })
    show()
    await screen.findByText('未记录生产数量')
    expect(document.querySelector('.ant-statistic')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '保存剩余并重算' })).toBeDisabled()
  })

  it('warns above 100 percent without clamping the preview', async () => {
    show()
    const input = await readyInput()
    await userEvent.clear(input)
    await userEvent.type(input, '200')
    expect(document.querySelector('.ant-statistic-content-value')).toHaveTextContent('110.00')
    expect(screen.getByText(/结果超过100%/)).toBeInTheDocument()
  })

  it('labels unknown, missing-production and combined values distinctly in list cells', () => {
    const onOpen = vi.fn()
    const { rerender } = render(<OrderYieldCell onOpen={onOpen} />)
    expect(screen.getByText('良品率未读取')).toBeInTheDocument()
    rerender(<OrderYieldCell value={{ ...base, status: 'NO_PRODUCTION', yield_percent: null }} onOpen={onOpen} />)
    expect(screen.getByText('未记录生产数量')).toBeInTheDocument()
    expect(screen.queryByText('0.00%')).not.toBeInTheDocument()
    rerender(<OrderYieldCell value={{ ...base, combined: true }} onOpen={onOpen} />)
    expect(screen.getByText('合并良品率 90.00%')).toBeInTheDocument()
  })
})
