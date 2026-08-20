import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { QualityEmployee, QualityOrder, QualityShipmentBatch } from '../types'
import { QualityWeightShipmentDrawer } from './QualityWeightShipmentDrawer'

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({ matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })),
})
class ResizeObserverMock { observe() {} unobserve() {} disconnect() {} }
globalThis.ResizeObserver = ResizeObserverMock

const apiMocks = vi.hoisted(() => ({
  checkShipmentNo: vi.fn(),
  listShipmentCandidates: vi.fn(),
  getShipmentBatch: vi.fn(),
  createShipmentBatch: vi.fn(),
  updateShipmentBatch: vi.fn(),
  confirmShipmentBatch: vi.fn(),
}))
vi.mock('../api/client', () => ({
  qualityApi: { checkShipmentNo: apiMocks.checkShipmentNo },
  qualityWorkflowApi: {
    checkShipmentNo: apiMocks.checkShipmentNo,
    listShipmentCandidates: apiMocks.listShipmentCandidates,
    getShipmentBatch: apiMocks.getShipmentBatch,
    createShipmentBatch: apiMocks.createShipmentBatch,
    updateShipmentBatch: apiMocks.updateShipmentBatch,
    confirmShipmentBatch: apiMocks.confirmShipmentBatch,
  },
  toList: <T,>(payload: T[] | { results?: T[] }) => Array.isArray(payload) ? payload : payload.results || [],
}))

const order: QualityOrder = {
  id: 7,
  order_no: 'TEST-ORDER-001',
  item_no: '10',
  batch_no: 'B-01',
  product_code: 'TEST-PRODUCT-001',
  product_name: '测试产品A',
  specification: 'TEST-SPEC-A',
  material: 'SYN-RUBBER-A',
  order_quantity: 240,
  order_date: '2026-08-03',
  status: 'OPEN',
}

const employees: QualityEmployee[] = [
  { id: 1, employee_no: 'Q001', name: '张三', role: 'INSPECTOR', is_active: true },
  { id: 2, employee_no: 'Q002', name: '李四', role: 'BOTH', is_active: true },
]

function renderDrawer(onSubmit = vi.fn().mockResolvedValue({ id: 1 }), props: Partial<React.ComponentProps<typeof QualityWeightShipmentDrawer>> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(<QueryClientProvider client={client}><App><QualityWeightShipmentDrawer open orders={[order]} employees={employees} onClose={vi.fn()} onSubmit={onSubmit} {...props} /></App></QueryClientProvider>)
  return { onSubmit }
}

describe('QualityWeightShipmentDrawer', () => {
  beforeEach(() => {
    apiMocks.checkShipmentNo.mockReset().mockResolvedValue({ exists: false })
    apiMocks.listShipmentCandidates.mockReset().mockResolvedValue([])
    apiMocks.getShipmentBatch.mockReset()
    apiMocks.createShipmentBatch.mockReset()
    apiMocks.updateShipmentBatch.mockReset()
    apiMocks.confirmShipmentBatch.mockReset()
  })

  it('shows candidate-order and manual snapshot fields with multiple inspectors', () => {
    renderDrawer()
    expect(screen.getByText('新增重量出货')).toBeInTheDocument()
    expect(screen.getByLabelText('规格')).toBeInTheDocument()
    expect(screen.getByLabelText('材质 / 胶料')).toBeInTheDocument()
    expect(screen.getByLabelText(/品检员（选填，可后续补录）/)).toBeInTheDocument()
    expect(screen.getByLabelText('流程卡出货数量')).toBeInTheDocument()
    expect(screen.getByText('批数快捷计算：')).toBeInTheDocument()
  })

  it('calculates pieces from one weighed batch and submits snapshots plus inspector ids', async () => {
    const user = userEvent.setup()
    const { onSubmit } = renderDrawer()
    fireEvent.change(screen.getByLabelText(/出货单号/), { target: { value: 'CK-202608-001' } })
    fireEvent.change(screen.getByLabelText('成品单重(g/件)'), { target: { value: '25' } })
    fireEvent.change(screen.getByLabelText('单批实称净重(kg)'), { target: { value: '2.5' } })
    fireEvent.change(screen.getByLabelText('流程卡出货数量'), { target: { value: '100' } })
    fireEvent.change(screen.getByLabelText('规格'), { target: { value: '手工规格' } })
    fireEvent.change(screen.getByLabelText('材质 / 胶料'), { target: { value: '手工材质' } })
    await user.click(screen.getByRole('combobox', { name: /品检员（选填，可后续补录）/ }))
    await user.click(await screen.findByText(/Q001 · 张三/))
    await user.click(screen.getByRole('button', { name: '确认出货' }))

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    const payload = onSubmit.mock.calls[0][0]
    expect(payload.piece_quantity).toBe(100)
    expect(payload.single_batch_net_weight_kg).toBe(2.5)
    expect(payload.total_net_weight_kg).toBe(2.5)
    expect(payload.process_card_shipment_quantity).toBe(100)
    expect(payload.inspector_ids).toEqual([1])
    expect(payload.specification_snapshot).toBe('手工规格')
    expect(payload.material_snapshot).toBe('手工材质')
    expect(payload.lines[0].net_weight_kg).toBe(2.5)
  }, 20_000)

  it('updates final pieces and cumulative weight immediately when batch count changes', async () => {
    const user = userEvent.setup()
    const { onSubmit } = renderDrawer()
    fireEvent.change(screen.getByLabelText('成品单重(g/件)'), { target: { value: '25' } })
    fireEvent.change(screen.getByLabelText('单批实称净重(kg)'), { target: { value: '2.5' } })
    fireEvent.change(screen.getByLabelText('流程卡出货数量'), { target: { value: '100' } })
    fireEvent.change(screen.getByLabelText(/相同称重批数/), { target: { value: '3' } })
    fireEvent.change(screen.getByLabelText('规格'), { target: { value: '批量规格' } })
    fireEvent.change(screen.getByLabelText('材质 / 胶料'), { target: { value: '批量材质' } })

    await waitFor(() => expect(screen.getByText('最终总出货数').closest('.ant-statistic')).toHaveTextContent('300'))
    expect(screen.getByText('累计总净重').closest('.ant-statistic')).toHaveTextContent('7.500')
    await user.click(screen.getByRole('button', { name: '确认出货' }))

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    expect(onSubmit.mock.calls[0][0]).toMatchObject({
      shipment_no: undefined,
      inspector_ids: [],
      single_batch_net_weight_kg: 2.5,
      total_net_weight_kg: 7.5,
      process_card_shipment_quantity: 100,
      product_batch_count: 3,
      piece_quantity: 300,
    })
  }, 20_000)

  it('saves an unfinished shipment as a server-side draft', async () => {
    const onSaved = vi.fn().mockResolvedValue(undefined)
    const draft = { id: 77, shipment_no: 'CK-DRAFT-SAVE', status: 'DRAFT' }
    apiMocks.createShipmentBatch.mockResolvedValue(draft)
    renderDrawer(vi.fn(), { onSaved })

    fireEvent.change(screen.getByLabelText(/出货单号/), { target: { value: draft.shipment_no } })
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }))

    await waitFor(() => expect(apiMocks.createShipmentBatch).toHaveBeenCalledTimes(1))
    expect(apiMocks.createShipmentBatch.mock.calls[0][0]).toMatchObject({
      shipment_no: draft.shipment_no,
      lines: [],
    })
    expect(onSaved).toHaveBeenCalledTimes(1)
  }, 20_000)

  it('loads an existing draft number, updates that row, confirms it, and notifies the parent', async () => {
    const user = userEvent.setup()
    const onSaved = vi.fn().mockResolvedValue(undefined)
    const draft: QualityShipmentBatch = {
      id: 88,
      shipment_no: 'CK-DRAFT-001',
      shipment_date: '2026-08-20',
      status: 'DRAFT',
      order_id: order.id,
      order,
      product_name_snapshot: order.product_name,
      specification_snapshot: order.specification,
      material_snapshot: order.material,
      unit_weight_g: 25,
      total_net_weight_kg: 2.5,
      inspector_ids: [1],
      lines: [{
        id: 901,
        order_id: order.id,
        net_weight_kg: 2.5,
        piece_quantity: 100,
        unit_weight_g_snapshot: 25,
        specification_snapshot: order.specification,
        material_snapshot: order.material,
      }],
    }
    apiMocks.getShipmentBatch.mockResolvedValue(draft)
    apiMocks.updateShipmentBatch.mockResolvedValue(draft)
    apiMocks.confirmShipmentBatch.mockResolvedValue({ ...draft, status: 'CONFIRMED' })
    renderDrawer(vi.fn(), { existingBatches: [draft], onSaved })

    fireEvent.change(screen.getByLabelText(/出货单号/), { target: { value: draft.shipment_no } })
    fireEvent.blur(screen.getByLabelText(/出货单号/))
    expect(await screen.findByText(`发现未完成草稿：${draft.shipment_no}`)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '继续填写草稿' }))
    expect(await screen.findByText(`继续填写草稿 · ${draft.shipment_no}`)).toBeInTheDocument()
    expect(await screen.findByText('已选择 1 张流程卡')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '确认出货' }))
    await waitFor(() => expect(apiMocks.updateShipmentBatch).toHaveBeenCalledWith(88, expect.any(Object)))
    expect(apiMocks.confirmShipmentBatch).toHaveBeenCalledWith(88)
    expect(onSaved).toHaveBeenCalledTimes(1)
  }, 20_000)
})
