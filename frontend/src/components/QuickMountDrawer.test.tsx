import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { MoldAsset, ProductionBoardStation } from '../types'
import { QuickMountDrawer } from './QuickMountDrawer'

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
    list: vi.fn(),
    action: vi.fn(),
  }
})

vi.mock('../api/client', () => ({
  ApiError: apiMocks.ApiError,
  moldApi: { list: apiMocks.list, action: apiMocks.action },
  toList: <T,>(payload: T[]) => payload,
}))

const mold = {
  id: 18,
  asset_code: 'MOLD-018',
  mold_model: { id: 3, code: 'MODEL-018', product_name: '密封圈' },
  status: 'IN_STOCK',
  slot: { id: 8, display_code: 'J01-L01-F-P01-S1' },
} satisfies MoldAsset

const otherMold = {
  ...mold,
  id: 19,
  asset_code: 'MOLD-019',
  mold_model: { id: 4, code: 'MODEL-019', product_name: '试模件' },
} satisfies MoldAsset

const station = {
  id: 2,
  code: '2',
  group: 'A',
  position_no: 2,
  is_active: true,
  machine: { id: 12, code: '2', name: '2号机台', is_active: true },
  reminder_status: 'IDLE',
  mounted_molds: [],
  run: null,
} satisfies ProductionBoardStation

describe('QuickMountDrawer', () => {
  beforeEach(() => {
    apiMocks.list.mockReset().mockResolvedValue([mold])
    apiMocks.action.mockReset().mockResolvedValue({ ...mold, status: 'ON_MACHINE', slot: null, machine: station.machine })
  })

  it('loads any selected in-stock mold onto the card machine and refreshes every linked view', async () => {
    const user = userEvent.setup()
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    const onSuccess = vi.fn()
    render(
      <QueryClientProvider client={queryClient}>
        <App>
          <QuickMountDrawer open station={station} onClose={vi.fn()} onSuccess={onSuccess} />
        </App>
      </QueryClientProvider>,
    )

    await user.click(screen.getByRole('combobox', { name: '在库模具' }))
    await user.click(await screen.findByText(/MOLD-018 · MODEL-018 · 密封圈/))
    await user.click(screen.getByRole('button', { name: '确认快速上机' }))

    await waitFor(() => expect(apiMocks.action).toHaveBeenCalledTimes(1))
    expect(apiMocks.action).toHaveBeenCalledWith(mold.id, 'load-machine', {
      machine_id: station.machine.id,
      note: '',
      confirm_warnings: false,
    })
    expect(onSuccess).toHaveBeenCalledWith(expect.objectContaining({ id: mold.id, status: 'ON_MACHINE' }))
    const refreshedKeys = invalidateSpy.mock.calls.map(([filters]) => filters?.queryKey?.[0])
    expect(refreshedKeys).toEqual(expect.arrayContaining(['production', 'molds', 'mold', 'racks', 'slots', 'machines', 'analytics']))
  })

  it('only offers the mold reserved by an existing planned order', async () => {
    apiMocks.list.mockResolvedValue([mold, otherMold])
    const plannedStation: ProductionBoardStation = {
      ...station,
      reminder_status: 'PLANNED',
      run: {
        id: 31,
        order_no: 'PLAN-031',
        station_id: station.id,
        station_code: station.code,
        mold_id: mold.id,
        mold_code: mold.asset_code,
        mold_model_code: mold.mold_model.code,
        mold_product_name: mold.mold_model.product_name,
        specification: '密封圈',
        material: 'NBR',
        order_quantity: 100,
        planned_mold_count: 25,
        produced_mold_count: 0,
        good_quantity: 0,
        progress_percent: 0,
        remaining_mold_count: 25,
        status: 'PLANNED',
        estimated_hours: 8,
      },
    }
    const user = userEvent.setup()
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <App>
          <QuickMountDrawer open station={plannedStation} onClose={vi.fn()} />
        </App>
      </QueryClientProvider>,
    )

    expect(await screen.findByText(/已有待上机计划，只能先上机计划关联的模具/)).toBeInTheDocument()
    await user.click(screen.getByRole('combobox', { name: '在库模具' }))
    expect(await screen.findByText(/MOLD-018 · MODEL-018/)).toBeInTheDocument()
    expect(screen.queryByText(/MOLD-019 · MODEL-019/)).not.toBeInTheDocument()
  })
})
