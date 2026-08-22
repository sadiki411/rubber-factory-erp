import { App } from 'antd'
import { fireEvent, render, screen } from '@testing-library/react'
import type { QualityReworkCase } from '../types'
import { QualityReworkCaseMobileList } from './QualityReworkCaseMobileList'

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

const item: QualityReworkCase = {
  id: 9,
  case_no: 'RW-20260821-0009',
  origin: 'CUSTOMER_RETURN',
  shipment_batch_id: 501,
  shipment_unit_no: 3,
  opened_on: '2026-08-21',
  reason_category: 'STICKING',
  reason_category_display: '粘皮',
  reason: '产品粘皮',
  status: 'PROCESSING',
  attempt_count: 1,
  source: {
    shipment_batch_id: 501,
    shipment_line_id: 601,
    shipment_unit_no: 3,
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
    inspectors: [],
    lines: [],
  },
}

describe('QualityReworkCaseMobileList', () => {
  it('shows order, product, specification, material and physical batch without a table swipe', () => {
    const onOpen = vi.fn()
    const onAddAttempt = vi.fn()
    render(<App><QualityReworkCaseMobileList items={[item]} onOpen={onOpen} onAddAttempt={onAddAttempt} /></App>)

    expect(screen.getByText('04-A001-2607040001 / 11')).toBeInTheDocument()
    expect(screen.getByText('油封圈')).toBeInTheDocument()
    expect(screen.getByText('Φ32×18')).toBeInTheDocument()
    expect(screen.getByText('NBR-T3')).toBeInTheDocument()
    expect(screen.getByText('第 3 / 34 批')).toBeInTheDocument()
    expect(screen.getByText('粘皮')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /查看\s*\/\s*修改/ }))
    expect(onOpen).toHaveBeenCalledWith(item)
    fireEvent.click(screen.getByRole('button', { name: /登记下一轮/ }))
    expect(onAddAttempt).toHaveBeenCalledWith(item)
  })
})
