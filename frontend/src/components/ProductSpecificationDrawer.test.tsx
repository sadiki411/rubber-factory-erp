import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ProductSpecificationDrawer } from './ProductSpecificationDrawer'

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({ matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })),
})
class ResizeObserverMock { observe() {} unobserve() {} disconnect() {} }
globalThis.ResizeObserver = ResizeObserverMock

const apiMocks = vi.hoisted(() => ({ create: vi.fn(), update: vi.fn() }))
vi.mock('../api/client', () => ({
  productSpecificationApi: { create: apiMocks.create, update: apiMocks.update },
}))

function renderDrawer() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><App><ProductSpecificationDrawer open onClose={vi.fn()} /></App></QueryClientProvider>)
}

describe('ProductSpecificationDrawer', () => {
  it('keeps original process parameter text unchanged', async () => {
    apiMocks.create.mockResolvedValue({ id: 1, product_name: '测试产品A', is_active: true })
    const user = userEvent.setup()
    renderDrawer()

    await user.type(screen.getByLabelText(/产品名称/), '测试产品A')
    await user.type(screen.getByLabelText(/一次硫化参数/), '160℃×240秒 / 10MPa')
    await user.type(screen.getByLabelText(/裁料重量/), '10.25g（允许±0.1）')
    await user.type(screen.getByLabelText(/总孔数/), '06孔')
    await user.click(screen.getByRole('button', { name: /保\s*存/ }))

    await waitFor(() => expect(apiMocks.create).toHaveBeenCalledTimes(1))
    expect(apiMocks.create).toHaveBeenCalledWith(expect.objectContaining({
      product_name: '测试产品A',
      primary_curing: '160℃×240秒 / 10MPa',
      cut_weight: '10.25g（允许±0.1）',
      total_cavities: '06孔',
    }))
  }, 15_000)
})
