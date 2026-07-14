import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ProductionRun } from '../types'
import { CompleteAndPutawayDrawer } from './CompleteAndPutawayDrawer'

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

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = ResizeObserverMock

const apiMocks = vi.hoisted(() => {
  class MockApiError extends Error {
    status: number
    data: any

    constructor(status: number, message: string, data?: any) {
      super(message)
      this.status = status
      this.data = data
    }
  }
  return {
    ApiError: MockApiError,
    completeAndPutaway: vi.fn(),
    listSlots: vi.fn(),
  }
})

vi.mock('../api/client', () => ({
  ApiError: apiMocks.ApiError,
  productionApi: { completeAndPutaway: apiMocks.completeAndPutaway },
  slotApi: { list: apiMocks.listSlots },
  toList: <T,>(payload: T[]) => payload,
}))

const running = {
  id: 31,
  station: {
    id: 1,
    code: '1',
    group: 'A',
    position_no: 1,
    is_active: true,
    machine: { id: 5, code: '1', name: '1号机台', is_active: true },
  },
  order_no: 'RUN-031',
  specification: '密封圈',
  material: 'NBR',
  mold: { id: 18, asset_code: 'MOLD-018', model_code: 'MODEL-018', product_name: '密封圈模具', status: 'ON_MACHINE' },
  order_quantity: 1000,
  cavities: 4,
  estimated_defect_rate: '3.00',
  planned_mold_count: 258,
  estimated_hours: '8.00',
  loaded_at: new Date().toISOString(),
  status: 'RUNNING',
  daily_logs: [],
} as ProductionRun

function renderDrawer(onSuccess = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
  render(
    <QueryClientProvider client={queryClient}>
      <App>
        <CompleteAndPutawayDrawer open run={running} onClose={vi.fn()} onSuccess={onSuccess} />
      </App>
    </QueryClientProvider>,
  )
  return { invalidateSpy, onSuccess }
}

describe('CompleteAndPutawayDrawer', () => {
  beforeEach(() => {
    apiMocks.listSlots.mockReset().mockResolvedValue([{ id: 81, display_code: 'J01-L01-F-P01-S1', active: true, available: true, position_no: 1, stack_level: 1 }])
    apiMocks.completeAndPutaway.mockReset()
  })

  it('uses one atomic request to finish production and put the mold into the selected slot', async () => {
    const completed = { ...running, status: 'COMPLETED' as const, unloaded_at: new Date().toISOString(), mold: { ...running.mold!, status: 'IN_STOCK' as const } }
    apiMocks.completeAndPutaway.mockResolvedValue(completed)
    const user = userEvent.setup()
    const { invalidateSpy, onSuccess } = renderDrawer()

    await user.click(screen.getByRole('combobox', { name: '目标库位' }))
    await user.click(await screen.findByText('J01-L01-F-P01-S1'))
    await user.click(screen.getByRole('button', { name: /确认结束并归位/ }))

    await waitFor(() => expect(apiMocks.completeAndPutaway).toHaveBeenCalledTimes(1))
    expect(apiMocks.completeAndPutaway).toHaveBeenCalledWith(running.id, {
      slot_id: 81,
      unloaded_at: expect.any(String),
      note: '',
      confirm_warnings: false,
    })
    expect(onSuccess).toHaveBeenCalledWith(completed)
    const refreshedKeys = invalidateSpy.mock.calls.map(([filters]) => filters?.queryKey?.[0])
    expect(refreshedKeys).toEqual(expect.arrayContaining(['production', 'molds', 'mold', 'racks', 'slots', 'machines', 'analytics']))
  }, 15_000)

  it('retries the same atomic operation only after a stacking warning is confirmed', async () => {
    const warning = '上叠位置下方没有模具。'
    const completed = { ...running, status: 'COMPLETED' as const, mold: { ...running.mold!, status: 'IN_STOCK' as const } }
    apiMocks.completeAndPutaway
      .mockRejectedValueOnce(new apiMocks.ApiError(409, '需要确认叠放风险', { warnings: [warning] }))
      .mockResolvedValueOnce(completed)
    const user = userEvent.setup()
    renderDrawer()

    await user.click(screen.getByRole('combobox', { name: '目标库位' }))
    await user.click(await screen.findByText('J01-L01-F-P01-S1'))
    await user.click(screen.getByRole('button', { name: /确认结束并归位/ }))
    expect(await screen.findByText(warning)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '已检查，结束生产并归位' }))

    await waitFor(() => expect(apiMocks.completeAndPutaway).toHaveBeenCalledTimes(2))
    expect(apiMocks.completeAndPutaway.mock.calls[1][1]).toMatchObject({
      slot_id: 81,
      unloaded_at: apiMocks.completeAndPutaway.mock.calls[0][1].unloaded_at,
      confirm_warnings: true,
    })
  }, 15_000)
})
