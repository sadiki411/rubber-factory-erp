import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { OrderFormDrawer } from './OrderFormDrawer'

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({ matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })),
})
class ResizeObserverMock { observe() {} unobserve() {} disconnect() {} }
globalThis.ResizeObserver = ResizeObserverMock

const apiMocks = vi.hoisted(() => ({ create: vi.fn(), update: vi.fn(), listSpecifications: vi.fn() }))
vi.mock('../api/client', () => ({
  orderApi: { create: apiMocks.create, update: apiMocks.update },
  productSpecificationApi: { list: apiMocks.listSpecifications },
  toList: <T,>(payload: T[]) => payload,
}))

function renderDrawer() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><App><OrderFormDrawer open onClose={vi.fn()} /></App></QueryClientProvider>)
}

describe('OrderFormDrawer', () => {
  beforeEach(() => {
    apiMocks.create.mockReset().mockResolvedValue({ id: 1 })
    apiMocks.update.mockReset()
    apiMocks.listSpecifications.mockReset().mockResolvedValue([])
  })

  it('keeps an unrecorded required material separate from an explicit zero receipt', async () => {
    const user = userEvent.setup()
    renderDrawer()

    await user.type(screen.getByLabelText(/订单编号/), 'TEST-ORDER-001')
    await user.type(screen.getByLabelText(/产品名称/), '测试产品A')
    await user.type(screen.getByLabelText(/^规格/), 'TEST-SPEC-A')
    await user.type(screen.getByLabelText(/材质 \/ 胶料/), 'SYN-RUBBER-A')
    await user.type(screen.getByRole('spinbutton', { name: '订单数量' }), '240')
    await user.type(screen.getByLabelText(/手工登记已发胶料/), '0')
    await user.click(screen.getByRole('button', { name: /保\s*存/ }))

    await waitFor(() => expect(apiMocks.create).toHaveBeenCalledTimes(1))
    const body = apiMocks.create.mock.calls[0][0]
    expect(body.manual_received_material_kg).toBe('0')
    expect(body.required_material_kg).toBeUndefined()
    expect(body.production_required).toBeNull()
  }, 20_000)
})
