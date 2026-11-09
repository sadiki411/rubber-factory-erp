import { render, screen, within } from '@testing-library/react'
import { MaterialLayoutSummary } from './MaterialLayoutSummary'
import { ProductionRunParameters } from './ProductionRunParameters'
import type { ProductionRun } from '../types'

it('displays missing weights separately from an explicitly saved zero total', () => {
  render(<MaterialLayoutSummary record={{ large_strip_weight_g: '0.00', large_strip_count: 5 }} />)
  const layout = within(screen.getByLabelText('一模排料参数'))
  expect(layout.getAllByText('0 g')).toHaveLength(2)
  expect(layout.getAllByText('未填写')).toHaveLength(2)
})

it('clearly labels legacy kg data and never invents large or small-strip fields', () => {
  render(<MaterialLayoutSummary record={{ strip_weight_kg: '0.021', strips_per_batch: 5 }} />)
  expect(screen.getByText('历史单条条重')).toBeInTheDocument()
  expect(screen.getByText('21 g')).toBeInTheDocument()
  expect(screen.getByText('105 g')).toBeInTheDocument()
  expect(screen.getAllByText('未填写')).toHaveLength(4)
})

it('does not display a partial total as a complete per-mold weight', () => {
  render(<MaterialLayoutSummary record={{ large_strip_weight_g: '21', large_strip_count: 5, small_strip_count: 2 }} />)
  expect(screen.getByText('参数不完整，暂不计算')).toBeInTheDocument()
  expect(screen.queryByText('105 g')).not.toBeInTheDocument()
})

it('preserves quantity-mode planning and all linked order items in the read-only view', () => {
  render(<ProductionRunParameters run={{ order_quantity: 700, cavities: 16, estimated_defect_mode: 'QUANTITY', estimated_defect_quantity: 20, estimated_defect_rate: '9', order_allocations: [
    { id: 1, order_id: 1, order_no: 'TEST-A', item_no: '01', due_date: '2026-11-10', planned_quantity: 400, allocated_quantity: 400, remaining_quantity: 0 },
    { id: 2, order_id: 2, order_no: 'TEST-B', item_no: '02', planned_quantity: 300, allocated_quantity: 200, remaining_quantity: 100 },
  ] } as ProductionRun} />)
  expect(screen.getByText('预估不良件数')).toBeInTheDocument()
  expect(screen.getByText('20 件')).toBeInTheDocument()
  expect(screen.queryByText('9 %')).not.toBeInTheDocument()
  expect(screen.getByText('TEST-A / 01')).toBeVisible()
  expect(screen.getByText('TEST-B / 02')).toBeVisible()
  expect(screen.getByText(/交期 2026-11-10 · 本次计划 400 件 · 已分配 400 件/)).toBeVisible()
})
