import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ProductSpecificationDrawer } from './ProductSpecificationDrawer'

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({ matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })),
})
class ResizeObserverMock { observe() {} unobserve() {} disconnect() {} }
globalThis.ResizeObserver = ResizeObserverMock

const apiMocks = vi.hoisted(() => ({ create: vi.fn(), update: vi.fn(), listMoldModels: vi.fn() }))
vi.mock('../api/client', () => ({
  productSpecificationApi: { create: apiMocks.create, update: apiMocks.update },
  masterApi: () => ({ list: apiMocks.listMoldModels }),
  toList: <T,>(payload: T[]) => payload,
}))

function renderDrawer(specification?: Parameters<typeof ProductSpecificationDrawer>[0]['specification']) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><App><ProductSpecificationDrawer open specification={specification} onClose={vi.fn()} /></App></QueryClientProvider>)
}

describe('ProductSpecificationDrawer', () => {
  beforeEach(() => {
    apiMocks.create.mockReset()
    apiMocks.update.mockReset().mockResolvedValue({ id: 8 })
    apiMocks.listMoldModels.mockReset().mockResolvedValue([{
      id: 7,
      code: 'TEST-MOLD-MODEL-01',
      product_name: '测试产品A模具',
      is_active: true,
    }])
  })

  it('keeps original process parameter text unchanged', async () => {
    apiMocks.create.mockResolvedValue({ id: 1, product_name: '测试产品A', is_active: true })
    const user = userEvent.setup()
    renderDrawer()

    await user.type(screen.getByLabelText(/产品名称/), '测试产品A')
    await user.type(screen.getByLabelText(/一次硫化参数/), '160℃×240秒 / 10MPa')
    await user.type(screen.getByLabelText(/裁料重量/), '10.25g（允许±0.1）')
    await user.type(screen.getByLabelText(/总孔数/), '06孔')
    await user.click(screen.getByRole('combobox', { name: /关联模具型号/ }))
    await user.click(await screen.findByText('TEST-MOLD-MODEL-01 · 测试产品A模具'))
    await user.click(screen.getByRole('button', { name: /保\s*存/ }))

    await waitFor(() => expect(apiMocks.create).toHaveBeenCalledTimes(1))
    expect(apiMocks.create).toHaveBeenCalledWith(expect.objectContaining({
      product_name: '测试产品A',
      primary_curing: '160℃×240秒 / 10MPa',
      cut_weight: '10.25g（允许±0.1）',
      total_cavities: '06孔',
      mold_model_id: 7,
    }))
  }, 15_000)

  it('can clear an existing mold-model association', async () => {
    const user = userEvent.setup()
    const existing = {
      id: 8,
      product_name: '测试产品A',
      mold_model_id: 7,
      mold_model: { id: 7, code: 'TEST-MOLD-MODEL-01', product_name: '测试产品A模具', is_active: true },
      is_active: true,
    }
    renderDrawer(existing)

    await screen.findByText('TEST-MOLD-MODEL-01 · 测试产品A模具')
    const clearButton = document.querySelector('.ant-select-clear')
    expect(clearButton).not.toBeNull()
    fireEvent.mouseDown(clearButton as Element)
    fireEvent.click(clearButton as Element)
    await user.click(screen.getByRole('button', { name: /保\s*存/ }))

    await waitFor(() => expect(apiMocks.update).toHaveBeenCalledWith(existing.id, expect.objectContaining({ mold_model_id: null })))
  }, 15_000)
})
