import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { OrderFormDrawer } from './OrderFormDrawer'

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({ matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })),
})
class ResizeObserverMock { observe() {} unobserve() {} disconnect() {} }
globalThis.ResizeObserver = ResizeObserverMock

const apiMocks = vi.hoisted(() => ({ create: vi.fn(), update: vi.fn(), statusHistory: vi.fn(), listSpecifications: vi.fn() }))
vi.mock('../api/client', () => ({
  orderApi: { create: apiMocks.create, update: apiMocks.update, statusHistory: apiMocks.statusHistory },
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
    apiMocks.statusHistory.mockReset().mockResolvedValue([])
    apiMocks.listSpecifications.mockReset().mockResolvedValue([])
  })

  it('keeps an unrecorded required material separate from an explicit zero receipt', async () => {
    const user = userEvent.setup()
    renderDrawer()

    fireEvent.change(screen.getByLabelText(/订单编号/), { target: { value: 'TEST-ORDER-001' } })
    fireEvent.change(screen.getByLabelText(/产品名称/), { target: { value: '测试产品A' } })
    fireEvent.change(screen.getByLabelText(/^规格/), { target: { value: 'TEST-SPEC-A' } })
    fireEvent.change(screen.getByLabelText(/材质 \/ 胶料/), { target: { value: 'SYN-RUBBER-A' } })
    fireEvent.change(screen.getByRole('spinbutton', { name: '订单数量' }), { target: { value: '240' } })
    fireEvent.change(screen.getByLabelText(/手工登记已发胶料/), { target: { value: '0' } })
    fireEvent.change(screen.getByLabelText(/流程卡（原表内容）/), { target: { value: '无' } })
    fireEvent.change(screen.getByLabelText(/^生产数量/), { target: { value: '120' } })
    fireEvent.change(screen.getByLabelText(/^出货日期/), { target: { value: '2026-08-31' } })
    fireEvent.change(screen.getByLabelText(/^出货数量/), { target: { value: '80' } })
    await user.click(screen.getByRole('button', { name: /保\s*存/ }))

    await waitFor(() => expect(apiMocks.create).toHaveBeenCalledTimes(1))
    const body = apiMocks.create.mock.calls[0][0]
    expect(body.manual_received_material_kg).toBe('0')
    expect(body.required_material_kg).toBeUndefined()
    expect(body.production_required).toBeNull()
    expect(body.process_card_text).toBe('无')
    expect(body.production_quantity).toBe('120')
    expect(body.shipment_date).toBe('2026-08-31')
    expect(body.shipped_quantity).toBe('80')
  }, 20_000)

  it('shows the linked mold model without deriving forming hours from product specifications', async () => {
    apiMocks.listSpecifications.mockResolvedValue([{
      id: 9,
      product_name: '测试产品A',
      customer_product_no: 'TEST-PRODUCT-001',
      specification: 'TEST-SPEC-A',
      material: 'SYN-RUBBER-A',
      primary_curing: '160℃×240秒',
      mold_model_id: 7,
      mold_model: { id: 7, code: 'TEST-MOLD-MODEL-01', product_name: '测试产品A模具', is_active: true },
      mold_size: '500×500',
      is_active: true,
    }])
    const user = userEvent.setup()
    renderDrawer()

    await user.click(screen.getByRole('combobox', { name: /关联产品规格/ }))
    await user.click(await screen.findByText('TEST-PRODUCT-001 · 测试产品A · TEST-SPEC-A'))

    expect(screen.getByLabelText(/成型工时/)).toHaveValue('')
    expect(screen.getByText(/模具型号：TEST-MOLD-MODEL-01/)).toBeInTheDocument()
  }, 20_000)

  it('calculates received cards and covered quantity from standard and tail cards', async () => {
    renderDrawer()

    fireEvent.change(screen.getByLabelText(/标准卡每张数量/), { target: { value: '1000' } })
    fireEvent.change(screen.getByLabelText(/标准卡张数/), { target: { value: '10' } })
    fireEvent.change(screen.getByLabelText(/尾数卡数量/), { target: { value: '800' } })
    // This action is synchronous.  Using userEvent here makes the full Ant
    // Design drawer accessibility walk dominate the test runtime on slower
    // CI runners and can turn a deterministic calculation into a timeout.
    fireEvent.click(screen.getByRole('button', { name: '计算并写入流程卡登记' }))

    expect(screen.getByLabelText(/流程卡张数/)).toHaveValue('11')
    expect(screen.getByLabelText(/流程卡覆盖订单数量/)).toHaveValue('10800')
    expect(screen.getByLabelText(/流程卡（原表内容）/)).toHaveValue('1000×10张＋800×1张＝10800件')
  }, 20_000)
})
