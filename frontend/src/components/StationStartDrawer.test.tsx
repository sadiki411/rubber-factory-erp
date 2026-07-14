import { fireEvent, render, screen } from '@testing-library/react'
import type { ProductionBoardStation } from '../types'
import { StationStartDrawer } from './StationStartDrawer'

const station = {
  id: 2,
  code: '2',
  group: 'A',
  position_no: 2,
  is_active: true,
  machine: { id: 12, code: '2', name: '2号机台', is_active: true },
  reminder_status: 'IDLE',
  mounted_molds: [],
  run: null,
} satisfies ProductionBoardStation

describe('StationStartDrawer', () => {
  it('keeps trial mounting, direct production and a planned order as separate choices', () => {
    const onQuickMount = vi.fn()
    const onCreatePlan = vi.fn()
    render(<StationStartDrawer open station={station} onClose={vi.fn()} onQuickMount={onQuickMount} onCreatePlan={onCreatePlan} />)

    fireEvent.click(screen.getByRole('button', { name: /快速上机 \/ 试模/ }))
    expect(onQuickMount).toHaveBeenCalledWith(false)
    fireEvent.click(screen.getByRole('button', { name: /上机后登记生产/ }))
    expect(onQuickMount).toHaveBeenCalledWith(true)
    fireEvent.click(screen.getByRole('button', { name: /新增待上机计划/ }))
    expect(onCreatePlan).toHaveBeenCalledTimes(1)
  })

  it('lets an idle machine with a plan either open that plan or enter the quick trial flow', () => {
    const onQuickMount = vi.fn()
    const onCreatePlan = vi.fn()
    const plannedStation: ProductionBoardStation = {
      ...station,
      reminder_status: 'PLANNED',
      run: {
        id: 31,
        order_no: 'PLAN-031',
        station_id: station.id,
        station_code: station.code,
        specification: '密封圈',
        material: 'NBR',
        order_quantity: 100,
        planned_mold_count: 25,
        produced_mold_count: 0,
        good_quantity: 0,
        progress_percent: 0,
        remaining_mold_count: 25,
        status: 'PLANNED',
        estimated_hours: 8,
      },
    }
    render(<StationStartDrawer open station={plannedStation} onClose={vi.fn()} onQuickMount={onQuickMount} onCreatePlan={onCreatePlan} />)

    expect(screen.getByRole('button', { name: /查看待上机计划/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /上机后登记生产/ })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /快速上机 \/ 试模/ }))
    expect(onQuickMount).toHaveBeenCalledWith(false)
    fireEvent.click(screen.getByRole('button', { name: /查看待上机计划/ }))
    expect(onCreatePlan).toHaveBeenCalledTimes(1)
  })
})
