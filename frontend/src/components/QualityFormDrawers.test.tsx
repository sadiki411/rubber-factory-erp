import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QualityReworkDrawer, QualityShipmentDrawer } from './QualityFormDrawers'
import type { QualityOrder, QualityShipment, ReturnRework } from '../types'

const api = vi.hoisted(() => ({
  listOrders: vi.fn(), listShipments: vi.fn(), createShipment: vi.fn(),
  updateShipment: vi.fn(), createRework: vi.fn(), updateRework: vi.fn(),
}))
vi.mock('../api/client', () => ({
  orderApi: { list: api.listOrders },
  qualityApi: api,
  toList: (data: unknown[] | { results: unknown[] }) => Array.isArray(data) ? data : data.results,
}))
vi.mock('./QualityWeightShipmentDrawer', () => ({ QualityWeightShipmentDrawer: ({ open }: { open: boolean }) => open ? <div>翔邦原称重抽屉</div> : null }))

Object.defineProperty(window, 'matchMedia', { writable: true, value: vi.fn().mockImplementation(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })) })
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }

const order = {
  id: 907, source_system: '艾斯霖', order_no: 'ASL-REMOTE-907', item_no: '20',
  product_name: '包装密封圈', specification: '20×30', material: 'NBR', order_quantity: 100,
} as QualityOrder & { source_system: string }
const shipment = {
  id: 807, order, shipment_no: 'ASL-REMOTE-SHIP', shipment_date: '2026-10-21',
  shipped_quantity: 100, returned_quantity: 30, inspectors: [],
  inspection_quantity: 100, qualified_quantity: 100, defective_quantity: 0,
} as unknown as QualityShipment

function wrapper(element: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  render(<QueryClientProvider client={client}><App>{element}</App></QueryClientProvider>)
  return { client, invalidate }
}

async function choose(label: string, pattern: RegExp) {
  fireEvent.mouseDown(screen.getByLabelText(label))
  await userEvent.click(await screen.findByText(pattern))
}

function confirmQualifiedPieces() {
  fireEvent.click(screen.getByRole('checkbox', { name: /确认本次出货均为已品检合格的包装成品/ }))
}

beforeEach(() => {
  vi.clearAllMocks()
  api.listOrders.mockResolvedValue({ results: [order] })
  api.listShipments.mockResolvedValue({ results: [shipment] })
  api.createShipment.mockResolvedValue(shipment)
  api.updateShipment.mockResolvedValue(shipment)
  api.createRework.mockResolvedValue({ id: 2 })
  api.updateRework.mockResolvedValue({ id: 2 })
})

it('keeps the original weighted default', () => {
  wrapper(<QualityShipmentDrawer open orders={[]} employees={[]} onClose={vi.fn()} />)
  expect(screen.getByText('翔邦原称重抽屉')).toBeInTheDocument()
  expect(api.listOrders).not.toHaveBeenCalled()
})

it('creates piece shipment with one selected remote purchase item and no employee, card or weight', async () => {
  const onClose = vi.fn()
  const onSaved = vi.fn()
  const { invalidate } = wrapper(<QualityShipmentDrawer open mode="PIECES" orders={[]} employees={[]} onClose={onClose} onSaved={onSaved} />)
  await choose('采购单 / 项次', /ASL-REMOTE-907/)
  expect((screen.getByLabelText(/出货单号/) as HTMLInputElement).value).toMatch(/^QS-COUNT-/)
  fireEvent.change(screen.getByLabelText('本次出货件数'), { target: { value: '80' } })
  confirmQualifiedPieces()
  fireEvent.click(screen.getByRole('button', { name: '保 存' }))
  await waitFor(() => expect(api.createShipment).toHaveBeenCalledTimes(1))
  const payload = api.createShipment.mock.calls[0][0]
  expect(payload).toEqual(expect.objectContaining({ order_id: 907, shipped_quantity: 80, inspection_quantity: 80, qualified_quantity: 80, defective_quantity: 0, inspector_id: null }))
  expect(payload).not.toHaveProperty('process_card_id')
  expect(payload).not.toHaveProperty('net_weight_kg')
  expect(payload).not.toHaveProperty('confirmed_qualified_pieces')
  await waitFor(() => expect(onSaved).toHaveBeenCalledWith(shipment))
  expect(onClose).toHaveBeenCalled()
  for (const key of ['quality', 'orders', 'analytics', 'dashboard']) expect(invalidate).toHaveBeenCalledWith({ queryKey: [key] })
})

it('retains the form and shows backend remaining-quantity errors', async () => {
  api.createShipment.mockRejectedValue(new Error('采购单剩余件数不足'))
  const onClose = vi.fn()
  wrapper(<QualityShipmentDrawer open mode="PIECES" orders={[]} employees={[]} onClose={onClose} />)
  await choose('采购单 / 项次', /ASL-REMOTE-907/)
  fireEvent.change(screen.getByLabelText(/出货单号/), { target: { value: 'OVER-001' } })
  fireEvent.change(screen.getByLabelText('本次出货件数'), { target: { value: '101' } })
  confirmQualifiedPieces()
  fireEvent.click(screen.getByRole('button', { name: '保 存' }))
  expect(await screen.findByText('采购单剩余件数不足')).toBeInTheDocument()
  expect(onClose).not.toHaveBeenCalled()
})

it('blocks rapid double submit while saving', async () => {
  api.createShipment.mockReturnValue(new Promise(() => {}))
  wrapper(<QualityShipmentDrawer open mode="PIECES" orders={[order]} employees={[]} onClose={vi.fn()} />)
  await choose('采购单 / 项次', /ASL-REMOTE-907/)
  fireEvent.change(screen.getByLabelText(/出货单号/), { target: { value: 'ONCE' } })
  fireEvent.change(screen.getByLabelText('本次出货件数'), { target: { value: '20' } })
  confirmQualifiedPieces()
  const save = screen.getByRole('button', { name: '保 存' })
  fireEvent.click(save)
  fireEvent.click(save)
  await waitFor(() => expect(api.createShipment).toHaveBeenCalledTimes(1))
})

it('finds original quantity shipment and records a return without staff', async () => {
  const { invalidate } = wrapper(<QualityReworkDrawer open shipments={[]} employees={[]} onClose={vi.fn()} />)
  await choose('原件数出货记录', /ASL-REMOTE-SHIP/)
  fireEvent.change(screen.getByLabelText('退货数量'), { target: { value: '20' } })
  fireEvent.change(screen.getByLabelText('退货 / 返工原因'), { target: { value: '包装退回' } })
  fireEvent.click(screen.getByRole('button', { name: '保 存' }))
  await waitFor(() => expect(api.createRework).toHaveBeenCalledTimes(1))
  expect(api.createRework).toHaveBeenCalledWith(expect.objectContaining({ shipment_id: 807, returned_quantity: 20, responsible_inspector_id: null, rework_employee_id: null }))
  for (const key of ['quality', 'orders', 'analytics', 'dashboard']) expect(invalidate).toHaveBeenCalledWith({ queryKey: [key] })
  expect(api.listShipments).toHaveBeenCalledWith({ q: '', page_size: 1000 })
})

it('updates the existing quantity return and keeps original shipment locked', async () => {
  const rework = { id: 2, shipment, rework_date: '2026-10-21', reason_category: 'PACKAGING', reason: '退回', returned_quantity: 30, reworked_quantity: 0, recovered_quantity: 0, scrap_quantity: 0, work_hours: 0, status: 'PENDING' } as ReturnRework
  wrapper(<QualityReworkDrawer open rework={rework} shipments={[]} employees={[]} onClose={vi.fn()} />)
  expect(screen.getByLabelText('原件数出货记录')).toBeDisabled()
  fireEvent.change(screen.getByLabelText('退货数量'), { target: { value: '40' } })
  fireEvent.click(screen.getByRole('button', { name: '保 存' }))
  await waitFor(() => expect(api.updateRework).toHaveBeenCalledWith(2, expect.objectContaining({ shipment_id: 807, returned_quantity: 40 })))
})

it('keeps old source employee requirement', async () => {
  api.listOrders.mockResolvedValue([{ ...order, source_system: '翔邦' }])
  wrapper(<QualityShipmentDrawer open mode="PIECES" orders={[]} employees={[]} onClose={vi.fn()} />)
  await choose('采购单 / 项次', /ASL-REMOTE-907/)
  fireEvent.change(screen.getByLabelText(/出货单号/), { target: { value: 'OLD' } })
  fireEvent.change(screen.getByLabelText('本次出货件数'), { target: { value: '20' } })
  fireEvent.click(screen.getByRole('button', { name: '保 存' }))
  expect(await screen.findByText('请选择品检员')).toBeInTheDocument()
  expect(api.createShipment).not.toHaveBeenCalled()
})

it('requires explicit inspection confirmation before deriving inspection and qualified quantities', async () => {
  wrapper(<QualityShipmentDrawer open mode="PIECES" orders={[order]} employees={[]} onClose={vi.fn()} />)
  await choose('采购单 / 项次', /ASL-REMOTE-907/)
  fireEvent.change(screen.getByLabelText('本次出货件数'), { target: { value: '20' } })
  expect(screen.getByRole('checkbox')).not.toBeChecked()
  fireEvent.click(screen.getByRole('button', { name: '保 存' }))
  expect(await screen.findByText('请确认本次出货成品的品检及数量口径')).toBeInTheDocument()
  expect(api.createShipment).not.toHaveBeenCalled()
  confirmQualifiedPieces()
  fireEvent.click(screen.getByRole('button', { name: '保 存' }))
  await waitFor(() => expect(api.createShipment).toHaveBeenCalledWith(expect.objectContaining({ shipped_quantity: 20, inspection_quantity: 20, qualified_quantity: 20, defective_quantity: 0, shipment_no: expect.stringMatching(/^QS-COUNT-/) })))
})

it('reuses the operation identity when shipment save fails and is retried', async () => {
  api.createShipment.mockRejectedValueOnce(new Error('网络中断，结果未知'))
  wrapper(<QualityShipmentDrawer open mode="PIECES" orders={[order]} employees={[]} onClose={vi.fn()} />)
  await choose('采购单 / 项次', /ASL-REMOTE-907/)
  fireEvent.change(screen.getByLabelText('本次出货件数'), { target: { value: '20' } })
  confirmQualifiedPieces()
  const save = screen.getByRole('button', { name: '保 存' })
  fireEvent.click(save)
  await screen.findByText('网络中断，结果未知')
  await waitFor(() => expect(save).not.toHaveClass('ant-btn-loading'))
  fireEvent.click(save)
  await waitFor(() => expect(api.createShipment).toHaveBeenCalledTimes(2))
  const [first, second] = api.createShipment.mock.calls.map(([payload]) => payload)
  expect(first.client_request_id).toMatch(/^[0-9a-f-]{36}$/i)
  expect(second.client_request_id).toBe(first.client_request_id)
})

it('reuses the operation identity when return save fails and is retried', async () => {
  api.createRework.mockRejectedValueOnce(new Error('退货响应中断，结果未知'))
  wrapper(<QualityReworkDrawer open shipments={[]} employees={[]} onClose={vi.fn()} />)
  await choose('原件数出货记录', /ASL-REMOTE-SHIP/)
  fireEvent.change(screen.getByLabelText('退货数量'), { target: { value: '10' } })
  fireEvent.change(screen.getByLabelText('退货 / 返工原因'), { target: { value: '包装退回' } })
  const save = screen.getByRole('button', { name: '保 存' })
  fireEvent.click(save)
  await screen.findByText('退货响应中断，结果未知')
  await waitFor(() => expect(save).not.toHaveClass('ant-btn-loading'))
  fireEvent.click(save)
  await waitFor(() => expect(api.createRework).toHaveBeenCalledTimes(2))
  const [first, second] = api.createRework.mock.calls.map(([payload]) => payload)
  expect(first.client_request_id).toMatch(/^[0-9a-f-]{36}$/i)
  expect(second.client_request_id).toBe(first.client_request_id)
})
