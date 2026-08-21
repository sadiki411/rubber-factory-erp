import { App } from 'antd'
import { fireEvent, render, screen, within } from '@testing-library/react'
import type {
  ProductSpecification,
  QualityEmployee,
  QualityOrder,
  QualityShipmentBatch,
  QualityUnitWeight,
} from '../types'
import { QualityWorkflowManagement } from './QualityWorkflowManagement'

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
  assignShipmentBatchInspectors: vi.fn(),
  updateShipmentBatch: vi.fn(),
  confirmShipmentBatch: vi.fn(),
  voidShipmentBatch: vi.fn(),
  createUnitWeight: vi.fn(),
  updateUnitWeight: vi.fn(),
  createReworkCase: vi.fn(),
  updateReworkCase: vi.fn(),
  createReworkAttempt: vi.fn(),
}))

vi.mock('../api/client', () => ({
  qualityWorkflowApi: apiMocks,
}))

const specification: ProductSpecification = {
  id: 29,
  product_name: '减震密封件',
  specification: 'Φ28 × 12',
  material: 'NBR-T3',
  is_active: true,
}

const order: QualityOrder = {
  id: 101,
  order_no: 'XB-202608-001',
  item_no: '30',
  batch_no: 'B-0801',
  product_code: 'CP-001',
  product_name: specification.product_name,
  specification: specification.specification || '',
  material: specification.material || '',
  product_specification: specification,
  product_specification_id: specification.id,
  order_quantity: 300,
  order_date: '2026-08-01',
  due_date: '2026-08-25',
  status: 'OPEN',
}

const employees: QualityEmployee[] = [
  { id: 1, employee_no: 'Q001', name: '张三', role: 'INSPECTOR', is_active: true },
]

const batch: QualityShipmentBatch = {
  id: 501,
  shipment_no: 'QS-20260820-TEST0001',
  shipment_date: '2026-08-20',
  status: 'CONFIRMED',
  order_id: order.id,
  order,
  product_name_snapshot: order.product_name,
  specification_snapshot: order.specification,
  material_snapshot: order.material,
  unit_weight_g: '25.00000',
  single_batch_net_weight_kg: '2.500',
  process_card_shipment_quantity: 100,
  product_batch_count: 3,
  pieces_per_batch: 100,
  shipped_quantity: 300,
  total_net_weight_kg: '7.500',
  net_weight_kg: '7.500',
  line_count: 1,
  notes: '上午出货，客户自提',
  inspectors: [],
  inspector_ids: [],
  lines: [{
    id: 601,
    order_id: order.id,
    order,
    card_no: 'PC-202608-01',
    unit_weight_g_snapshot: '25.00000',
    single_batch_net_weight_kg: '2.500',
    product_batch_count: 3,
    pieces_per_batch: 100,
    process_card_shipment_quantity: 100,
    piece_quantity: 300,
    specification_snapshot: order.specification,
    material_snapshot: order.material,
    net_weight_kg: '7.500',
  }],
}

function renderManagement({
  unitWeights = [],
  batches = [],
}: {
  unitWeights?: QualityUnitWeight[]
  batches?: QualityShipmentBatch[]
} = {}) {
  render(
    <App>
      <QualityWorkflowManagement
        orders={[order]}
        employees={employees}
        cards={[]}
        unitWeights={unitWeights}
        batches={batches}
        reworkCases={[]}
        onRefresh={vi.fn().mockResolvedValue(undefined)}
      />
    </App>,
  )
}

describe('QualityWorkflowManagement', () => {
  it('shows the linked product and material without looking it up through orders', () => {
    const weight = {
      id: 77,
      product_specification_id: specification.id,
      product_specification: specification,
      unit_weight_g: '9.45740',
      measured_on: '2026-08-20',
      is_active: true,
    } as QualityUnitWeight & { product_specification: ProductSpecification }

    renderManagement({ unitWeights: [weight] })

    expect(screen.getByText('减震密封件 · NBR-T3')).toBeInTheDocument()
    expect(screen.getByText('Φ28 × 12')).toBeInTheDocument()
    expect(screen.queryByText('规格#29')).not.toBeInTheDocument()
  })

  it('opens the complete details from the clickable shipment number', () => {
    renderManagement({ batches: [batch] })

    fireEvent.click(screen.getByRole('tab', { name: '重量出货批次（1）' }))
    const batchPanel = screen.getByRole('tabpanel', { name: '重量出货批次（1）' })
    expect(within(batchPanel).getByText('XB-202608-001 / 30')).toBeInTheDocument()
    expect(within(batchPanel).getByText('减震密封件 · NBR-T3')).toBeInTheDocument()
    expect(within(batchPanel).getByText('Φ28 × 12')).toBeInTheDocument()
    expect(within(batchPanel).getByText('2026-08-25')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: batch.shipment_no }))

    const drawer = screen.getByRole('dialog')
    expect(within(drawer).getByText(`出货批次详情 · ${batch.shipment_no}`)).toBeInTheDocument()
    expect(within(drawer).getAllByText('XB-202608-001 / 30').length).toBeGreaterThan(0)
    expect(within(drawer).getByText('2026-08-25')).toBeInTheDocument()
    expect(within(drawer).getAllByText('减震密封件').length).toBeGreaterThan(0)
    expect(within(drawer).getAllByText('NBR-T3').length).toBeGreaterThan(0)
    expect(within(drawer).getAllByText('3 批').length).toBeGreaterThan(0)
    expect(within(drawer).getAllByText('100 件').length).toBeGreaterThan(0)
    expect(within(drawer).getAllByText('100 件/批').length).toBeGreaterThan(0)
    expect(within(drawer).getAllByText('300 件').length).toBeGreaterThan(0)
    expect(within(drawer).getAllByText('7.500 kg').length).toBeGreaterThan(0)
    expect(within(drawer).getByText('上午出货，客户自提')).toBeInTheDocument()
    expect(within(drawer).getByText('出货明细（1行）')).toBeInTheDocument()
    expect(within(drawer).getByLabelText('品检员（选填，可多选）')).toBeInTheDocument()
  })

  it('deduplicates order and product summaries for multi-line batches', () => {
    const firstLine = batch.lines?.[0]
    const multiLineBatch: QualityShipmentBatch = {
      ...batch,
      line_count: 2,
      lines: firstLine ? [firstLine, { ...firstLine, id: 602 }] : [],
    }
    renderManagement({ batches: [multiLineBatch] })

    fireEvent.click(screen.getByRole('tab', { name: '重量出货批次（1）' }))
    const panel = screen.getByRole('tabpanel', { name: '重量出货批次（1）' })
    expect(within(panel).getAllByText('XB-202608-001 / 30')).toHaveLength(1)
    expect(within(panel).getAllByText('减震密封件 · NBR-T3')).toHaveLength(1)
    expect(within(panel).getAllByText('Φ28 × 12')).toHaveLength(1)
    expect(within(panel).getAllByText('2026-08-25')).toHaveLength(1)
  })

  it('opens the same details from both the line-count and inspector actions', () => {
    const { unmount } = render(
      <App>
        <QualityWorkflowManagement
          orders={[order]}
          employees={employees}
          cards={[]}
          unitWeights={[]}
          batches={[batch]}
          reworkCases={[]}
          onRefresh={vi.fn().mockResolvedValue(undefined)}
        />
      </App>,
    )

    fireEvent.click(screen.getByRole('tab', { name: '重量出货批次（1）' }))
    fireEvent.click(screen.getByRole('button', { name: '明细 1 行' }))
    expect(screen.getByRole('dialog')).toHaveTextContent('流程卡标准')
    unmount()

    renderManagement({ batches: [batch] })
    fireEvent.click(screen.getByRole('tab', { name: '重量出货批次（1）' }))
    fireEvent.click(screen.getByRole('button', { name: '补录品检员' }))
    const drawer = screen.getByRole('dialog')
    expect(drawer).toHaveTextContent('最终总件数')
    expect(within(drawer).getByLabelText('品检员（选填，可多选）')).toBeInTheDocument()
  })

  it('shows the legacy primary inspector when the multi-inspector list is empty', () => {
    renderManagement({ batches: [{ ...batch, inspector_id: employees[0].id, inspector: employees[0], inspectors: [] }] })
    fireEvent.click(screen.getByRole('tab', { name: '重量出货批次（1）' }))

    const panel = screen.getByRole('tabpanel', { name: '重量出货批次（1）' })
    expect(within(panel).getByText('张三')).toBeInTheDocument()
    expect(within(panel).getByRole('button', { name: '修改品检员' })).toBeInTheDocument()
    expect(within(panel).queryByText('待补录')).not.toBeInTheDocument()
  })
})
