import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MoldFormDrawer } from './MoldFormDrawer'
import type { MoldAsset, RackSlot } from '../types'

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

const apiMocks = vi.hoisted(() => ({
  create: vi.fn(),
  update: vi.fn(),
  listSlots: vi.fn(),
  listStations: vi.fn(),
}))

vi.mock('../api/client', () => ({
  ApiError: class ApiError extends Error {
    status = 400
    data = undefined
  },
  moldApi: { create: apiMocks.create, update: apiMocks.update },
  slotApi: { list: apiMocks.listSlots },
  productionApi: { stations: apiMocks.listStations },
  toList: <T,>(payload: T[]) => payload,
}))

const initialSlot: RackSlot = {
  id: 12,
  display_code: 'J01-L01-A-P02-S1',
  position_no: 2,
  stack_level: 1,
  active: true,
}

function renderDrawer(props: { mold?: MoldAsset; initialSlot?: RackSlot } = { initialSlot }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <App>
        <MoldFormDrawer open mold={props.mold} initialSlot={props.initialSlot} onClose={vi.fn()} />
      </App>
    </QueryClientProvider>,
  )
}

describe('MoldFormDrawer', () => {
  beforeEach(() => {
    apiMocks.create.mockReset()
    apiMocks.update.mockReset()
    apiMocks.listSlots.mockReset().mockResolvedValue([])
    apiMocks.listStations.mockReset().mockResolvedValue([])
  })

  it('accepts a manually typed model and creates it in the selected rack slot', async () => {
    const created = {
      id: 3,
      asset_code: 'ABC-100-01',
      mold_model: { id: 2, code: 'ABC-100', product_name: '密封圈' },
      status: 'IN_STOCK',
      slot: { id: initialSlot.id, display_code: initialSlot.display_code },
    } satisfies MoldAsset
    apiMocks.create.mockResolvedValue(created)
    const user = userEvent.setup()
    renderDrawer()

    expect(screen.getByRole('dialog', { name: `在 ${initialSlot.display_code} 放入模具` })).toBeInTheDocument()
    await user.type(screen.getByLabelText('模具型号'), 'ABC-100')
    await user.type(screen.getByLabelText(/产品名称（可选）/), '密封圈')
    await user.click(screen.getByRole('button', { name: /保\s*存/ }))

    await waitFor(() => expect(apiMocks.create).toHaveBeenCalledTimes(1))
    const body = apiMocks.create.mock.calls[0][0] as FormData
    expect(body.get('model_code')).toBe('ABC-100')
    expect(body.get('product_name')).toBe('密封圈')
    expect(body.get('initial_status')).toBe('IN_STOCK')
    expect(body.get('slot_id')).toBe(String(initialSlot.id))
    expect(body.get('asset_code')).toBeNull()
  })

  it('allows correcting or clearing a generated code and removes an existing image', async () => {
    const mold = {
      id: 7,
      asset_code: 'AUTO-100-01',
      mold_model: { id: 2, code: 'AUTO-100', product_name: '自动编号模具' },
      status: 'IN_STOCK',
      slot: { id: initialSlot.id, display_code: initialSlot.display_code },
      main_image: '/media/molds/old.jpg',
    } satisfies MoldAsset
    apiMocks.update.mockResolvedValue({ ...mold, asset_code: 'AUTO-100-02', main_image: null })
    const user = userEvent.setup()
    renderDrawer({ mold })

    const codeInput = screen.getByLabelText(/模具编号/)
    expect(codeInput).toBeEnabled()
    await user.clear(codeInput)
    await user.click(screen.getByTitle(/remove file|删除文件/i))
    await user.click(screen.getByRole('button', { name: /保\s*存/ }))

    await waitFor(() => expect(apiMocks.update).toHaveBeenCalledTimes(1))
    const [id, body] = apiMocks.update.mock.calls[0] as [number, FormData]
    expect(id).toBe(mold.id)
    expect(body.get('asset_code')).toBe('')
    expect(body.get('remove_image')).toBe('true')
  })
})
