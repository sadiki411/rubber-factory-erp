import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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
  previewShipmentAllocation: vi.fn(),
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
    previewShipmentAllocation: apiMocks.previewShipmentAllocation,
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
    apiMocks.previewShipmentAllocation.mockReset()
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
    fireEvent.change(screen.getByLabelText('成品单重(g/件)'), { target: { value: '8.7423' } })
    fireEvent.change(screen.getByLabelText('单批实称净重(kg)'), { target: { value: '10.2' } })
    fireEvent.change(screen.getByLabelText('流程卡出货数量'), { target: { value: '1091' } })
    fireEvent.change(screen.getByLabelText(/相同称重批数/), { target: { value: '34' } })
    fireEvent.change(screen.getByLabelText('规格'), { target: { value: '批量规格' } })
    fireEvent.change(screen.getByLabelText('材质 / 胶料'), { target: { value: '批量材质' } })

    await waitFor(() => expect(screen.getByText('最终总出货数').closest('.ant-statistic')).toHaveTextContent('39,678'))
    expect(screen.getByText('累计总净重').closest('.ant-statistic')).toHaveTextContent('346.800')
    await user.click(screen.getByRole('button', { name: '确认出货' }))

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    const payload = onSubmit.mock.calls[0][0]
    expect(payload).toMatchObject({
      shipment_no: undefined,
      inspector_ids: [],
      unit_weight_g: 8.7423,
      single_batch_net_weight_kg: 10.2,
      total_net_weight_kg: 346.8,
      net_weight_kg: 346.8,
      process_card_shipment_quantity: 1091,
      product_batch_count: 34,
      batch_count: 34,
      piece_quantity: 39678,
    })
    expect(payload.lines[0]).toMatchObject({
      unit_weight_g_snapshot: 8.7423,
      single_batch_net_weight_kg: 10.2,
      net_weight_kg: 346.8,
      product_batch_count: 34,
      process_card_shipment_quantity: 1091,
      piece_quantity: 39678,
    })
    expect(JSON.stringify(payload)).not.toContain('346.79999999999995')
  }, 20_000)

  it('uses only authoritative candidates so completed or stale local orders cannot return to the selector', async () => {
    const user = userEvent.setup()
    const completedOrder: QualityOrder = {
      ...order,
      id: 8,
      order_no: 'FULL-ORDER-008',
      item_no: '80',
      weighted_remaining_quantity: 0,
      shipment_status: 'SHIPPED',
    }
    apiMocks.listShipmentCandidates.mockResolvedValue([{ ...order, remaining_quantity: 240 }])
    renderDrawer(undefined, { orders: [order, completedOrder] })

    await waitFor(() => expect(apiMocks.listShipmentCandidates).toHaveBeenCalled())
    await user.click(screen.getByRole('combobox', { name: /候选订单/ }))

    expect(await screen.findByRole('option', { name: /TEST-ORDER-001/ })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /FULL-ORDER-008/ })).not.toBeInTheDocument()
  }, 20_000)

  it('does not fall back to cached orders when the authoritative candidate request fails', async () => {
    const user = userEvent.setup()
    apiMocks.listShipmentCandidates.mockRejectedValue(new Error('候选服务暂不可用'))
    renderDrawer()

    expect(await screen.findByText('候选订单暂时读取失败')).toBeInTheDocument()
    await user.click(screen.getByRole('combobox', { name: /候选订单/ }))

    expect(await screen.findByRole('option', { name: /手工输入/ })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /TEST-ORDER-001/ })).not.toBeInTheDocument()
  }, 20_000)

  it('clears order search, reloads exact specification/material candidates, and refreshes allocation when batch count changes', async () => {
    const user = userEvent.setup()
    const sourceOrder: QualityOrder = { ...order, remaining_quantity: 100 }
    const matchingOrder: QualityOrder = {
      ...order,
      id: 9,
      order_no: 'MATCH-ORDER-009',
      item_no: '20',
      due_date: '2026-08-15',
      remaining_quantity: 500,
    }
    apiMocks.listShipmentCandidates.mockResolvedValue([sourceOrder, matchingOrder])
    apiMocks.previewShipmentAllocation.mockImplementation(async ({ piece_quantity }: { piece_quantity: number }) => ({
      source_order_id: sourceOrder.id,
      requested_quantity: piece_quantity,
      specification: sourceOrder.specification,
      material: sourceOrder.material,
      allocations: [
        { order_id: sourceOrder.id, order_no: sourceOrder.order_no, item_no: sourceOrder.item_no, due_date: sourceOrder.due_date, remaining_before: 100, allocated_quantity: Math.min(100, piece_quantity), remaining_after: Math.max(0, 100 - piece_quantity), is_source: true, is_overflow: false },
        ...(piece_quantity > 100 ? [{ order_id: matchingOrder.id, order_no: matchingOrder.order_no, item_no: matchingOrder.item_no, due_date: matchingOrder.due_date, remaining_before: 500, allocated_quantity: piece_quantity - 100, remaining_after: 600 - piece_quantity, is_source: false, is_overflow: false }] : []),
      ],
      matching_allocated_quantity: Math.max(0, piece_quantity - 100),
      overflow_quantity: 0,
      total_allocated_quantity: piece_quantity,
    }))
    const confirmed = {
      id: 77,
      shipment_no: 'QS-AUTO-ALLOCATED',
      lines: [
        { id: 1, order_id: sourceOrder.id, order: sourceOrder, piece_quantity: 100, net_weight_kg: 2.5 },
        { id: 2, order_id: matchingOrder.id, order: matchingOrder, piece_quantity: 200, net_weight_kg: 5 },
      ],
    }
    const onSubmit = vi.fn().mockResolvedValue(confirmed)
    const onSaved = vi.fn().mockResolvedValue(undefined)
    renderDrawer(onSubmit, { orders: [sourceOrder, matchingOrder], onSaved })

    await waitFor(() => expect(apiMocks.listShipmentCandidates).toHaveBeenCalled())
    const orderSelector = screen.getByRole('combobox', { name: /候选订单/ })
    await user.click(orderSelector)
    await user.type(orderSelector, 'TEST-ORDER')
    const searchedOrderOption = await screen.findByText((_, element) => Boolean(
      element?.classList.contains('ant-select-item-option-content')
      && element.textContent?.includes('TEST-ORDER-001'),
    ))
    await user.click(searchedOrderOption)

    await waitFor(() => expect(apiMocks.listShipmentCandidates).toHaveBeenCalledWith(expect.objectContaining({
      q: undefined,
      specification: order.specification,
      material: order.material,
    })))
    expect(orderSelector).toHaveValue('')
    expect(await screen.findByText('剩余数量')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('成品单重(g/件)'), { target: { value: '25' } })
    fireEvent.change(screen.getByLabelText('单批实称净重(kg)'), { target: { value: '2.5' } })
    fireEvent.change(screen.getByLabelText('流程卡出货数量'), { target: { value: '100' } })
    fireEvent.change(screen.getByLabelText(/相同称重批数/), { target: { value: '3' } })

    await waitFor(() => expect(apiMocks.previewShipmentAllocation).toHaveBeenLastCalledWith({ order_id: sourceOrder.id, piece_quantity: 300 }))
    const previewCard = screen.getByText('订单自动分配预览').closest('.ant-card') as HTMLElement
    expect(within(previewCard).getByText(/MATCH-ORDER-009/)).toBeInTheDocument()
    expect(within(previewCard).getByText('200 件')).toBeInTheDocument()
    expect(within(previewCard).getByText(/超出部分已预分配 200 件/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '确认出货' }))
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(confirmed))
    expect(await screen.findByText('重量出货已保存，系统已自动分配到 2 个订单')).toBeInTheDocument()
  }, 30_000)

  it('clearly allows overflow on the source order when no matching order can receive it', async () => {
    const user = userEvent.setup()
    const sourceOrder: QualityOrder = { ...order, remaining_quantity: 100 }
    apiMocks.listShipmentCandidates.mockResolvedValue([sourceOrder])
    apiMocks.previewShipmentAllocation.mockResolvedValue({
      source_order_id: sourceOrder.id,
      requested_quantity: 300,
      specification: sourceOrder.specification,
      material: sourceOrder.material,
      allocations: [
        { order_id: sourceOrder.id, order_no: sourceOrder.order_no, item_no: sourceOrder.item_no, due_date: sourceOrder.due_date, remaining_before: 100, allocated_quantity: 300, remaining_after: 0, is_source: true, is_overflow: true },
      ],
      matching_allocated_quantity: 0,
      overflow_quantity: 200,
      total_allocated_quantity: 300,
    })
    renderDrawer(undefined, { orders: [sourceOrder] })

    await waitFor(() => expect(apiMocks.listShipmentCandidates).toHaveBeenCalled())
    await user.click(screen.getByRole('combobox', { name: /候选订单/ }))
    const sourceOrderOption = await screen.findByText((_, element) => Boolean(
      element?.classList.contains('ant-select-item-option-content')
      && element.textContent?.includes('TEST-ORDER-001'),
    ))
    await user.click(sourceOrderOption)
    expect(await screen.findByText('剩余数量')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('成品单重(g/件)'), { target: { value: '25' } })
    fireEvent.change(screen.getByLabelText('单批实称净重(kg)'), { target: { value: '2.5' } })
    fireEvent.change(screen.getByLabelText('流程卡出货数量'), { target: { value: '100' } })
    fireEvent.change(screen.getByLabelText(/相同称重批数/), { target: { value: '3' } })

    expect(await screen.findByText('没有可补足的匹配订单，允许当前订单超额出货')).toBeInTheDocument()
    expect(screen.getByText(/仍有 200 件，将作为当前订单超额数量记录/)).toBeInTheDocument()
    expect(screen.getByText('当前订单超额')).toBeInTheDocument()
  }, 20_000)

  it('saves an unfinished shipment as a server-side draft', async () => {
    const onSaved = vi.fn().mockResolvedValue(undefined)
    const draft = { id: 77, shipment_no: 'CK-DRAFT-SAVE', status: 'DRAFT' }
    apiMocks.createShipmentBatch.mockResolvedValue(draft)
    renderDrawer(vi.fn(), { onSaved })

    fireEvent.change(screen.getByLabelText(/出货单号/), { target: { value: draft.shipment_no } })
    fireEvent.change(screen.getByLabelText('成品单重(g/件)'), { target: { value: '8.7423' } })
    fireEvent.change(screen.getByLabelText('单批实称净重(kg)'), { target: { value: '10.2' } })
    fireEvent.change(screen.getByLabelText('流程卡出货数量'), { target: { value: '1091' } })
    fireEvent.change(screen.getByLabelText(/相同称重批数/), { target: { value: '34' } })
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }))

    await waitFor(() => expect(apiMocks.createShipmentBatch).toHaveBeenCalledTimes(1))
    expect(apiMocks.createShipmentBatch.mock.calls[0][0]).toMatchObject({
      shipment_no: draft.shipment_no,
      single_batch_net_weight_kg: 10.2,
      total_net_weight_kg: 346.8,
      net_weight_kg: 346.8,
      product_batch_count: 34,
      batch_count: 34,
      lines: [],
    })
    expect(onSaved).toHaveBeenCalledTimes(1)
  }, 20_000)

  it('preserves a legacy draft total and quantity when no single-batch weight was stored', async () => {
    const user = userEvent.setup()
    const draft: QualityShipmentBatch = {
      id: 79,
      shipment_no: 'CK-DRAFT-THIRDS',
      shipment_date: '2026-08-21',
      status: 'DRAFT',
      order_id: order.id,
      order,
      specification_snapshot: order.specification,
      material_snapshot: order.material,
      unit_weight_g: 1,
      total_net_weight_kg: 1,
      product_batch_count: 3,
      process_card_shipment_quantity: 333,
      lines: [{
        id: 903,
        order_id: order.id,
        order,
        net_weight_kg: 1,
        unit_weight_g_snapshot: 1,
        product_batch_count: 3,
        process_card_shipment_quantity: 333,
        piece_quantity: 1000,
        specification_snapshot: order.specification,
        material_snapshot: order.material,
      }],
    }
    apiMocks.updateShipmentBatch.mockResolvedValue(draft)
    apiMocks.confirmShipmentBatch.mockResolvedValue({ ...draft, status: 'CONFIRMED' })
    renderDrawer(vi.fn(), { batch: draft })

    expect(await screen.findByText('已选择 1 张流程卡')).toBeInTheDocument()
    const singleWeightField = screen.getByText('单批实称净重(kg)').closest('.ant-form-item')?.querySelector('input')
    expect(singleWeightField).toHaveValue('')
    expect(screen.getByText(/最终 1000 件 \/ 1\.000 kg/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '确认出货' }))

    await waitFor(() => expect(apiMocks.updateShipmentBatch).toHaveBeenCalledTimes(1))
    const payload = apiMocks.updateShipmentBatch.mock.calls[0][1]
    expect(payload.lines[0]).toMatchObject({
      single_batch_net_weight_kg: undefined,
      actual_weight_kg: 1,
      net_weight_kg: 1,
      product_batch_count: 3,
      piece_quantity: 1000,
    })
    expect(payload.lines[0]).not.toHaveProperty('single_batch_net_weight_kg', 0.333)
  }, 20_000)

  it('uses a legacy one-batch header total as the unchanged single weight', async () => {
    const user = userEvent.setup()
    const draft: QualityShipmentBatch = {
      id: 80,
      shipment_no: 'CK-DRAFT-ONE-BATCH',
      shipment_date: '2026-08-21',
      status: 'DRAFT',
      order_id: order.id,
      order,
      specification_snapshot: order.specification,
      material_snapshot: order.material,
      unit_weight_g: 25,
      total_net_weight_kg: 2.5,
      process_card_shipment_quantity: 100,
      lines: [],
    }
    apiMocks.updateShipmentBatch.mockResolvedValue(draft)
    apiMocks.confirmShipmentBatch.mockResolvedValue({ ...draft, status: 'CONFIRMED' })
    renderDrawer(vi.fn(), { batch: draft })

    expect(await screen.findByLabelText('单批实称净重(kg)')).toHaveValue('2.500')
    expect(screen.getByText('累计总净重').closest('.ant-statistic')).toHaveTextContent('2.500')
    await user.click(screen.getByRole('button', { name: '确认出货' }))

    await waitFor(() => expect(apiMocks.updateShipmentBatch).toHaveBeenCalledTimes(1))
    const payload = apiMocks.updateShipmentBatch.mock.calls[0][1]
    expect(payload).toMatchObject({
      single_batch_net_weight_kg: 2.5,
      total_net_weight_kg: 2.5,
      net_weight_kg: 2.5,
      product_batch_count: 1,
    })
    expect(payload.lines[0]).toMatchObject({
      single_batch_net_weight_kg: 2.5,
      net_weight_kg: 2.5,
      product_batch_count: 1,
    })
  }, 20_000)

  it('loads an existing draft number, updates that row, confirms it, and notifies the parent', async () => {
    const user = userEvent.setup()
    const onSaved = vi.fn().mockResolvedValue(undefined)
    const draftOrder: QualityOrder = { ...order, weighted_remaining_quantity: 0, shipment_status: 'SHIPPED' }
    const draft: QualityShipmentBatch = {
      id: 88,
      shipment_no: 'CK-DRAFT-001',
      shipment_date: '2026-08-20',
      status: 'DRAFT',
      order_id: order.id,
      order: draftOrder,
      product_name_snapshot: draftOrder.product_name,
      specification_snapshot: draftOrder.specification,
      material_snapshot: draftOrder.material,
      unit_weight_g: 25,
      total_net_weight_kg: 2.5,
      inspector_ids: [1],
      lines: [{
        id: 901,
        order_id: order.id,
        net_weight_kg: 2.5,
        piece_quantity: 100,
        unit_weight_g_snapshot: 25,
        specification_snapshot: draftOrder.specification,
        material_snapshot: draftOrder.material,
      }],
    }
    apiMocks.getShipmentBatch.mockResolvedValue(draft)
    apiMocks.updateShipmentBatch.mockResolvedValue(draft)
    apiMocks.confirmShipmentBatch.mockResolvedValue({ ...draft, status: 'CONFIRMED' })
    renderDrawer(vi.fn(), { orders: [draftOrder], existingBatches: [draft], onSaved })

    fireEvent.change(screen.getByLabelText(/出货单号/), { target: { value: draft.shipment_no } })
    fireEvent.blur(screen.getByLabelText(/出货单号/))
    expect(await screen.findByText(`发现未完成草稿：${draft.shipment_no}`)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '继续填写草稿' }))
    expect(await screen.findByText(`继续填写草稿 · ${draft.shipment_no}`)).toBeInTheDocument()
    expect(await screen.findByText('已选择 1 张流程卡')).toBeInTheDocument()
    const restoredSingleWeight = screen.getByText('单批实称净重(kg)').closest('.ant-form-item')?.querySelector('input')
    expect(restoredSingleWeight).toHaveValue('2.500')
    await user.click(screen.getByRole('combobox', { name: /候选订单/ }))
    expect(await screen.findByRole('option', { name: /TEST-ORDER-001.*原草稿订单/ })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '确认出货' }))
    await waitFor(() => expect(apiMocks.updateShipmentBatch).toHaveBeenCalledWith(88, expect.any(Object)))
    expect(apiMocks.updateShipmentBatch.mock.calls[0][1].lines[0]).toMatchObject({
      single_batch_net_weight_kg: 2.5,
      net_weight_kg: 2.5,
      product_batch_count: 1,
    })
    expect(apiMocks.confirmShipmentBatch).toHaveBeenCalledWith(88)
    expect(onSaved).toHaveBeenCalledTimes(1)
  }, 20_000)
})
