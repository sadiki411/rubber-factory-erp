import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { QualityEmployee, QualityReturnableBatch, QualityReworkCase } from '../types'
import {
  QualityReturnReworkAttemptDrawer,
  QualityReturnReworkDrawer,
  QualityReworkCaseDetailDrawer,
} from './QualityReturnReworkDrawer'

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
  listReturnableBatches: vi.fn(),
  createReworkCase: vi.fn(),
  updateReworkCase: vi.fn(),
  createReworkAttempt: vi.fn(),
}))

vi.mock('../api/client', () => ({
  qualityWorkflowApi: apiMocks,
}))

const candidate: QualityReturnableBatch = {
  key: 'weighted:501',
  source_type: 'WEIGHTED',
  shipment_batch_id: 501,
  shipment_line_id: 601,
  shipment_no: 'QS-20260821-0001',
  shipment_date: '2026-08-21',
  order_ids: [101],
  order_no: '04-A001-2607040001',
  item_no: '11',
  product_name: '油封圈',
  specification: 'Φ32×18',
  material: 'NBR-T3',
  single_batch_net_weight_kg: '10.200',
  pieces_per_batch: 1167,
  total_batches: 34,
  available_batches: 32,
  available_batch_numbers: Array.from({ length: 32 }, (_, index) => index + 3),
  returned_batches: 2,
  rework_count: 2,
  next_return_no: 3,
  inspectors: [{ id: 1, employee_no: 'Q001', name: '张三', role: 'INSPECTOR', is_active: true }],
  lines: [],
}

function renderWithQuery(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><App>{ui}</App></QueryClientProvider>)
}

describe('QualityReturnReworkDrawer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.listReturnableBatches.mockResolvedValue([candidate])
    apiMocks.createReworkCase.mockResolvedValue({ id: 9, case_no: 'R9' })
    apiMocks.updateReworkCase.mockResolvedValue({ id: 9, case_no: 'R9', status: 'CANCELLED' })
    apiMocks.createReworkAttempt.mockResolvedValue({ id: 19, attempt_no: 1 })
  })

  it('selects one physical batch and submits only the immutable source plus return metadata', async () => {
    const onSaved = vi.fn().mockResolvedValue(undefined)
    renderWithQuery(<QualityReturnReworkDrawer open onClose={vi.fn()} onSaved={onSaved} onBackfillShipment={vi.fn()} />)

    expect(await screen.findByText(/04-A001-2607040001 \/ 11/)).toBeInTheDocument()
    expect(screen.getByText('油封圈')).toBeInTheDocument()
    expect(screen.getByText('Φ32×18 · NBR-T3')).toBeInTheDocument()
    expect(screen.getByText(/可退 32 \/ 34 批/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '选择一整批退货' }))
    const drawer = screen.getByRole('dialog')
    expect(within(drawer).getByDisplayValue('1,167 件')).toBeDisabled()
    expect(within(drawer).getByDisplayValue('10.2 kg')).toBeDisabled()
    expect(within(drawer).queryByLabelText('流程卡')).not.toBeInTheDocument()
    expect(within(drawer).queryByLabelText(/返工合格/)).not.toBeInTheDocument()

    fireEvent.click(within(drawer).getByRole('button', { name: '确认登记整批退货' }))
    await waitFor(() => expect(apiMocks.createReworkCase).toHaveBeenCalledTimes(1))
    expect(apiMocks.createReworkCase).toHaveBeenCalledWith(expect.objectContaining({
      origin: 'CUSTOMER_RETURN',
      shipment_batch_id: 501,
      shipment_unit_no: 3,
      opened_on: '2026-08-21',
    }))
    const body = apiMocks.createReworkCase.mock.calls[0][0]
    expect(body).not.toHaveProperty('process_card_id')
    expect(body).not.toHaveProperty('affected_quantity')
    expect(body).not.toHaveProperty('affected_weight_kg')
    expect(body).not.toHaveProperty('recovered_quantity')
    expect(onSaved).toHaveBeenCalled()
  })

  it('offers a direct backfill action when no confirmed shipment can be selected', async () => {
    apiMocks.listReturnableBatches.mockResolvedValue([])
    const onBackfillShipment = vi.fn()
    renderWithQuery(<QualityReturnReworkDrawer open onClose={vi.fn()} onSaved={vi.fn()} onBackfillShipment={onBackfillShipment} />)

    expect(await screen.findByText('暂无可退整批的已确认出货记录')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '补录原出货' }))
    expect(onBackfillShipment).toHaveBeenCalledTimes(1)
  })

  it('searches the server by shipment, order, product, specification or material', async () => {
    renderWithQuery(<QualityReturnReworkDrawer open onClose={vi.fn()} onSaved={vi.fn()} onBackfillShipment={vi.fn()} />)
    await screen.findByText(/04-A001-2607040001 \/ 11/)

    fireEvent.change(screen.getByPlaceholderText('搜索出货单、订单、项次、产品、规格或材质'), { target: { value: 'NBR-T3' } })
    await waitFor(() => expect(apiMocks.listReturnableBatches).toHaveBeenCalledWith({ q: 'NBR-T3', page_size: 50 }), { timeout: 1500 })
  })
})

describe('QualityReturnReworkAttemptDrawer', () => {
  const employee: QualityEmployee = { id: 3, employee_no: 'R003', name: '李四', role: 'REWORKER', is_active: true }
  const item: QualityReworkCase = {
    id: 9,
    case_no: 'R9',
    origin: 'CUSTOMER_RETURN',
    shipment_batch_id: 501,
    shipment_unit_no: 3,
    opened_on: '2026-08-21',
    reason_category: 'OTHER',
    status: 'PROCESSING',
    attempt_count: 2,
    source: {
      shipment_batch_id: 501,
      shipment_line_id: 601,
      shipment_unit_no: 3,
      shipment_no: candidate.shipment_no,
      shipment_date: candidate.shipment_date,
      order_ids: candidate.order_ids,
      order_no: candidate.order_no,
      item_no: candidate.item_no,
      product_name: candidate.product_name,
      specification: candidate.specification,
      material: candidate.material,
      single_batch_net_weight_kg: candidate.single_batch_net_weight_kg,
      pieces_per_batch: candidate.pieces_per_batch,
      total_batches: candidate.total_batches,
      inspectors: candidate.inspectors,
      lines: [],
    },
  }

  it('records R3 without asking for recovered or scrap quantities', async () => {
    const onSaved = vi.fn().mockResolvedValue(undefined)
    renderWithQuery(<QualityReturnReworkAttemptDrawer open item={item} employees={[employee]} onClose={vi.fn()} onSaved={onSaved} />)

    const drawer = screen.getByRole('dialog')
    expect(within(drawer).getByText('R9 · 登记 R3')).toBeInTheDocument()
    expect(within(drawer).queryByLabelText(/返工合格|合格件数|报废件数/)).not.toBeInTheDocument()
    fireEvent.click(within(drawer).getByRole('button', { name: '保存本轮返工' }))

    await waitFor(() => expect(apiMocks.createReworkAttempt).toHaveBeenCalledTimes(1))
    expect(apiMocks.createReworkAttempt).toHaveBeenCalledWith(expect.objectContaining({
      case_id: 9,
      attempt_date: '2026-08-21',
      status: 'PROCESSING',
    }))
    const body = apiMocks.createReworkAttempt.mock.calls[0][0]
    expect(body).not.toHaveProperty('input_quantity')
    expect(body).not.toHaveProperty('reworked_quantity')
    expect(body).not.toHaveProperty('recovered_quantity')
    expect(body).not.toHaveProperty('scrap_quantity')
    expect(onSaved).toHaveBeenCalled()
  })

  it('hides further rounds for scrapped cases', () => {
    renderWithQuery(<QualityReworkCaseDetailDrawer open item={{ ...item, status: 'SCRAPPED' }} onClose={vi.fn()} onAddAttempt={vi.fn()} onSaved={vi.fn()} />)
    expect(screen.queryByRole('button', { name: '登记下一轮返工' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '取消误登记' })).not.toBeInTheDocument()
  })

  it('cancels a mistaken return while preserving it as an audited record', async () => {
    const onSaved = vi.fn().mockResolvedValue(undefined)
    const onClose = vi.fn()
    renderWithQuery(<QualityReworkCaseDetailDrawer open item={item} onClose={onClose} onAddAttempt={vi.fn()} onSaved={onSaved} />)

    fireEvent.click(screen.getByRole('button', { name: '取消误登记' }))
    fireEvent.click(await screen.findByRole('button', { name: '确认取消' }))
    await waitFor(() => expect(apiMocks.updateReworkCase).toHaveBeenCalledWith(9, { status: 'CANCELLED' }))
    expect(onSaved).toHaveBeenCalled()
    expect(onClose).toHaveBeenCalled()
  })
})
