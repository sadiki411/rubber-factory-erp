import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ProductionRun } from '../types'
import { ProductionLogDrawer } from './ProductionLogDrawer'

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
    startRun: vi.fn(),
    completeRun: vi.fn(),
    updateRun: vi.fn(),
    addLog: vi.fn(),
    updateLog: vi.fn(),
    settleRun: vi.fn(),
  }
})

vi.mock('../api/client', () => ({
  ApiError: apiMocks.ApiError,
  productionApi: {
    startRun: apiMocks.startRun,
    completeRun: apiMocks.completeRun,
    updateRun: apiMocks.updateRun,
    addLog: apiMocks.addLog,
    updateLog: apiMocks.updateLog,
    settleRun: apiMocks.settleRun,
  },
}))

const plannedRun = {
  id: 31,
  station: {
    id: 1,
    code: '1',
    group: 'A',
    position_no: 1,
    is_active: true,
    machine: { id: 5, code: '1', name: '1号机台', is_active: true },
  },
  order_no: 'PLAN-031',
  specification: '密封圈',
  material: 'NBR',
  mold: { id: 18, asset_code: 'MOLD-018', model_code: 'MODEL-018', product_name: '密封圈模具', status: 'IN_STOCK' },
  order_quantity: 1000,
  cavities: 4,
  estimated_defect_rate: '3.00',
  planned_mold_count: 258,
  estimated_hours: '8.00',
  status: 'PLANNED',
  daily_logs: [],
  produced_mold_count: 0,
  progress_percent: '0.00',
  remaining_mold_count: 258,
} as ProductionRun

function renderDrawer(run = plannedRun, onRequestCompleteAndPutaway?: (result: ProductionRun) => void) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
  const onRunChange = vi.fn()
  render(
    <QueryClientProvider client={queryClient}>
      <App>
        <ProductionLogDrawer open run={run} onClose={vi.fn()} onEdit={vi.fn()} onRunChange={onRunChange} onRequestCompleteAndPutaway={onRequestCompleteAndPutaway} />
      </App>
    </QueryClientProvider>,
  )
  return { invalidateSpy, onRunChange }
}

async function confirmStart(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: /确认上机/ }))
  const confirmationButtons = await screen.findAllByRole('button', { name: /确认上机/ })
  await user.click(confirmationButtons[confirmationButtons.length - 1])
}

describe('ProductionLogDrawer planned start', () => {
  beforeEach(() => {
    apiMocks.startRun.mockReset()
    apiMocks.completeRun.mockReset()
    apiMocks.updateRun.mockReset()
    apiMocks.addLog.mockReset()
    apiMocks.updateLog.mockReset()
    apiMocks.settleRun.mockReset()
  })

  it('confirms a planned mold on-machine and refreshes all linked caches', async () => {
    const running = { ...plannedRun, status: 'RUNNING' as const, loaded_at: new Date().toISOString(), mold: { ...plannedRun.mold!, status: 'ON_MACHINE' as const } }
    apiMocks.startRun.mockResolvedValue(running)
    const user = userEvent.setup()
    const { invalidateSpy, onRunChange } = renderDrawer()

    expect(screen.getByText('待上机')).toBeInTheDocument()
    expect(screen.getByText('MODEL-018')).toBeInTheDocument()
    await confirmStart(user)

    await waitFor(() => expect(apiMocks.startRun).toHaveBeenCalledTimes(1))
    expect(apiMocks.startRun.mock.calls[0][0]).toBe(plannedRun.id)
    expect(apiMocks.startRun.mock.calls[0][1]).toMatchObject({ confirm_warnings: false })
    expect(apiMocks.startRun.mock.calls[0][1].loaded_at).toEqual(expect.any(String))
    expect(onRunChange).toHaveBeenCalledWith(running)
    const refreshedKeys = invalidateSpy.mock.calls.map(([filters]) => filters?.queryKey?.[0])
    expect(refreshedKeys).toEqual(expect.arrayContaining(['production', 'molds', 'mold', 'racks', 'slots', 'machines', 'analytics']))
  })

  it('requires a second confirmation when leaving a stacked rack position', async () => {
    const warning = '上叠位置仍有模具，请先检查现场。'
    const running = { ...plannedRun, status: 'RUNNING' as const, loaded_at: new Date().toISOString(), mold: { ...plannedRun.mold!, status: 'ON_MACHINE' as const } }
    apiMocks.startRun
      .mockRejectedValueOnce(new apiMocks.ApiError(409, '需要确认叠放风险', { warnings: [warning] }))
      .mockResolvedValueOnce(running)
    const user = userEvent.setup()
    renderDrawer()

    await confirmStart(user)
    expect(await screen.findByText(warning)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '已检查叠放风险，确认上机' }))

    await waitFor(() => expect(apiMocks.startRun).toHaveBeenCalledTimes(2))
    const firstPayload = apiMocks.startRun.mock.calls[0][1]
    const confirmedPayload = apiMocks.startRun.mock.calls[1][1]
    expect(confirmedPayload).toMatchObject({ loaded_at: firstPayload.loaded_at, confirm_warnings: true })
  })

  it('does not offer an executable start action until the plan has a mold', () => {
    renderDrawer({ ...plannedRun, mold: null })
    expect(screen.getByText('请先编辑资料并关联模具')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /确认上机/ })).toBeDisabled()
  })

  it('records a material change without confusing it with stopping or putting the mold away', async () => {
    const running = { ...plannedRun, status: 'RUNNING' as const, loaded_at: new Date().toISOString(), mold: { ...plannedRun.mold!, status: 'ON_MACHINE' as const } }
    const changed = { ...running, material_changed_at: new Date().toISOString() }
    apiMocks.updateRun.mockResolvedValue(changed)
    const user = userEvent.setup()
    const { invalidateSpy, onRunChange } = renderDrawer(running)

    expect(screen.getByRole('button', { name: /停机 \/ 结束生产/ })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /记录当前换料时间/ }))

    await waitFor(() => expect(apiMocks.updateRun).toHaveBeenCalledWith(running.id, { material_changed_at: expect.any(String) }))
    expect(onRunChange).toHaveBeenCalledWith(changed)
    const refreshedKeys = invalidateSpy.mock.calls.map(([filters]) => filters?.queryKey?.[0])
    expect(refreshedKeys).toEqual(expect.arrayContaining(['production', 'analytics']))
  }, 15_000)

  it('opens the atomic finish-and-putaway flow without first completing the run', async () => {
    const running = { ...plannedRun, status: 'RUNNING' as const, loaded_at: new Date().toISOString(), mold: { ...plannedRun.mold!, status: 'ON_MACHINE' as const } }
    const onRequestCompleteAndPutaway = vi.fn()
    const user = userEvent.setup()
    const { onRunChange } = renderDrawer(running, onRequestCompleteAndPutaway)

    await user.click(screen.getByRole('button', { name: /结束生产并下机归位/ }))
    expect(onRequestCompleteAndPutaway).toHaveBeenCalledWith(running)
    expect(apiMocks.completeRun).not.toHaveBeenCalled()
    expect(onRunChange).not.toHaveBeenCalled()
  })
})
