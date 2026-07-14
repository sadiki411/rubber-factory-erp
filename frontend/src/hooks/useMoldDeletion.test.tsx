import { App, Button } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { MoldAsset } from '../types'
import { useMoldDeletion } from './useMoldDeletion'

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
  class ApiError extends Error {
    status: number
    data: unknown

    constructor(status: number, message: string, data?: unknown) {
      super(message)
      this.status = status
      this.data = data
    }
  }
  return { remove: vi.fn(), ApiError }
})

vi.mock('../api/client', () => ({
  ApiError: apiMocks.ApiError,
  moldApi: { remove: apiMocks.remove },
}))

const mold = {
  id: 19,
  asset_code: 'ERROR-019',
  mold_model: { id: 4, code: 'ERROR', product_name: '误录模具' },
  status: 'IN_STOCK',
  slot: { id: 5, display_code: 'J02-L03-A-P02' },
} satisfies MoldAsset

function DeleteHarness({ onDeleted }: { onDeleted: () => void }) {
  const { confirmDelete } = useMoldDeletion()
  return <Button onClick={() => confirmDelete(mold, { onSuccess: onDeleted })}>发起删除</Button>
}

describe('useMoldDeletion', () => {
  beforeEach(() => apiMocks.remove.mockReset().mockResolvedValue(undefined))

  it('confirms deletion, calls the API and refreshes mold, rack and slot queries', async () => {
    const user = userEvent.setup()
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    const onDeleted = vi.fn()
    render(
      <QueryClientProvider client={queryClient}>
        <App><DeleteHarness onDeleted={onDeleted} /></App>
      </QueryClientProvider>,
    )

    await user.click(screen.getByRole('button', { name: '发起删除' }))
    expect(screen.getByText(/该记录会从活动台账中删除/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '确认删除误录记录' }))

    await waitFor(() => expect(apiMocks.remove).toHaveBeenCalledWith(mold.id, false))
    await waitFor(() => expect(onDeleted).toHaveBeenCalledOnce())
    const refreshedKeys = invalidateSpy.mock.calls.map(([filters]) => filters?.queryKey?.[0])
    expect(refreshedKeys).toEqual(expect.arrayContaining(['molds', 'mold', 'racks', 'slots', 'machines', 'production']))
  })

  it('asks for a second confirmation and retries when deleting a stacked lower mold', async () => {
    apiMocks.remove
      .mockRejectedValueOnce(new apiMocks.ApiError(409, '需要确认叠放风险', { warnings: ['上叠位置仍有模具 UPPER-01。'] }))
      .mockResolvedValueOnce(undefined)
    const user = userEvent.setup()
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    const onDeleted = vi.fn()
    render(
      <QueryClientProvider client={queryClient}>
        <App><DeleteHarness onDeleted={onDeleted} /></App>
      </QueryClientProvider>,
    )

    await user.click(screen.getByRole('button', { name: '发起删除' }))
    await user.click(screen.getByRole('button', { name: '确认删除误录记录' }))
    expect(await screen.findByText('上叠位置仍有模具 UPPER-01。')).toBeInTheDocument()
    expect(apiMocks.remove).toHaveBeenNthCalledWith(1, mold.id, false)

    await user.click(screen.getByRole('button', { name: '已检查叠放风险，继续删除' }))
    await waitFor(() => expect(apiMocks.remove).toHaveBeenNthCalledWith(2, mold.id, true))
    await waitFor(() => expect(onDeleted).toHaveBeenCalledOnce())
  })
})
