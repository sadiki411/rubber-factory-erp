import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ProductionDailyLog, ProductionRun } from '../types'
import { ProductionCounterDrawer } from './ProductionCounterDrawer'

Object.defineProperty(window, 'matchMedia', { writable: true, value: vi.fn().mockImplementation((media: string) => ({ matches: false, media, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent() {} })) })
class ResizeObserverMock { observe() {} unobserve() {} disconnect() {} }
globalThis.ResizeObserver = ResizeObserverMock

const api = vi.hoisted(() => ({ listEmployees: vi.fn(), updateCounterLog: vi.fn(), addCounterLog: vi.fn() }))
vi.mock('../api/client', () => ({ productionApi: api }))

function renderCounter(edit = true, recorded = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const run = { id: 1, order_no: 'TEST', cavities: 16, daily_logs: [], planned_mold_count: 100 } as unknown as ProductionRun
  const log = { id: 2, cumulative_mold_count: 10, operator_employee_id: 1, defective_quantity: recorded ? 2 : 0, defective_quantity_status: recorded ? 'RECORDED' : 'HISTORICAL_ZERO_UNCONFIRMED' } as unknown as ProductionDailyLog
  return render(<QueryClientProvider client={client}><App><ProductionCounterDrawer open run={run} log={edit ? log : undefined} initialValues={{ cumulative_mold_count: 10, operator_employee_id: 1 }} onClose={vi.fn()} /></App></QueryClientProvider>)
}

describe('ProductionCounterDrawer defective quantity meaning', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.listEmployees.mockResolvedValue([{ id: 1, name: '测试人员', employee_no: 'EMP-TEST' }])
    api.updateCounterLog.mockResolvedValue({ produced_mold_count: 10 })
    api.addCounterLog.mockResolvedValue({ produced_mold_count: 10 })
  })

  it('does not reinterpret an untouched historical unknown zero', async () => {
    renderCounter()
    fireEvent.change(await screen.findByLabelText(/^备注/), { target: { value: '只改备注' } })
    fireEvent.click(screen.getByRole('button', { name: '保存交接' }))
    await waitFor(() => expect(api.updateCounterLog).toHaveBeenCalled())
    expect(api.updateCounterLog.mock.calls[0][2]).not.toHaveProperty('defective_quantity')
  })

  it('sends zero when explicitly confirming zero defects', async () => {
    renderCounter()
    fireEvent.change(await screen.findByLabelText(/^不良数量/), { target: { value: '0' } })
    fireEvent.click(screen.getByRole('button', { name: '保存交接' }))
    await waitFor(() => expect(api.updateCounterLog).toHaveBeenCalled())
    expect(api.updateCounterLog.mock.calls[0][2]).toHaveProperty('defective_quantity', 0)
  })

  it('sends null when intentionally clearing a previously recorded quantity', async () => {
    renderCounter(true, true)
    fireEvent.change(await screen.findByLabelText(/^不良数量/), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: '保存交接' }))
    await waitFor(() => expect(api.updateCounterLog).toHaveBeenCalled())
    expect(api.updateCounterLog.mock.calls[0][2]).toHaveProperty('defective_quantity', null)
  })

  it('creates an unrecorded quantity when a new log leaves it blank', async () => {
    renderCounter(false)
    fireEvent.click(await screen.findByRole('button', { name: '保存交接' }))
    await waitFor(() => expect(api.addCounterLog).toHaveBeenCalled())
    expect(api.addCounterLog.mock.calls[0][1]).toHaveProperty('defective_quantity', null)
  })
})
