import { fireEvent, render, screen } from '@testing-library/react'
import { vi } from 'vitest'
import type { ProductionBoard } from '../types'
import { ProductionPlanBoard } from './ProductionPlanBoard'

const board: ProductionBoard = {
  generated_at: '2026-07-14T08:00:00+08:00',
  reminder_window_minutes: 60,
  counts: { total: 2, idle: 1, occupied: 0, mounted: 0, planned: 1, running: 0, normal: 0, due_soon: 0, overdue: 0 },
  groups: [{
    group: 'A',
    stations: [{
      id: 1,
      group: 'A',
      position_no: 1,
      code: '1',
      is_active: true,
      mounted_molds: [],
      reminder_status: 'PLANNED',
      run: {
        id: 12,
        order_no: 'PLAN-012',
        station_id: 1,
        station_code: '1',
        mold_id: 9,
        mold_code: 'MOLD-009',
        mold_model_code: 'MODEL-009',
        mold_product_name: '密封圈',
        specification: '30×4',
        material: 'NBR',
        order_quantity: 500,
        planned_mold_count: 125,
        produced_mold_count: 0,
        good_quantity: 0,
        progress_percent: 0,
        remaining_mold_count: 125,
        status: 'PLANNED',
        loaded_at: null,
        expected_change_at: null,
        estimated_hours: 8,
      },
    }],
  }],
}

describe('ProductionPlanBoard', () => {
  it('shows machine, mold model and order and opens the confirmation detail', () => {
    const onPlanClick = vi.fn()
    render(<ProductionPlanBoard board={board} onPlanClick={onPlanClick} />)

    expect(screen.getByText('待上机计划')).toBeInTheDocument()
    expect(screen.getByText('一组-1号机台')).toBeInTheDocument()
    expect(screen.getByText('MODEL-009')).toBeInTheDocument()
    expect(screen.getByText('PLAN-012')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '查看并确认上机' }))
    expect(onPlanClick).toHaveBeenCalledWith(board.groups[0].stations[0])
  })
})
