import { fireEvent, render, screen } from '@testing-library/react'
import { vi } from 'vitest'
import type { ProductionBoardStation } from '../types'
import { MountedMoldActionsDrawer } from './MountedMoldActionsDrawer'

const station: ProductionBoardStation = {
  id: 1,
  group: 'A',
  position_no: 1,
  code: '1',
  is_active: true,
  reminder_status: 'MOUNTED',
  mounted_molds: [{ id: 9, asset_code: 'MOLD-009', model_code: 'MODEL-009', product_name: '密封圈', status: 'ON_MACHINE' }],
  run: null,
}

describe('MountedMoldActionsDrawer', () => {
  it('offers both production registration and a clear down-and-putaway action', () => {
    const onCreateProduction = vi.fn()
    const onPutaway = vi.fn()
    const onSendOut = vi.fn()
    render(<MountedMoldActionsDrawer open station={station} onClose={vi.fn()} onCreateProduction={onCreateProduction} onViewPlan={vi.fn()} onPutaway={onPutaway} onSendOut={onSendOut} />)

    expect(screen.getByText('MODEL-009')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /登记生产/ }))
    expect(onCreateProduction).toHaveBeenCalledWith(station.mounted_molds[0])
    fireEvent.click(screen.getByRole('button', { name: /下机并归位/ }))
    expect(onPutaway).toHaveBeenCalledWith(station.mounted_molds[0])
    fireEvent.click(screen.getByRole('button', { name: /客户收回/ }))
    expect(onSendOut).toHaveBeenCalledWith(station.mounted_molds[0])
  })

  it('keeps a mounted planned mold actionable for confirmation or trial putaway', () => {
    const plannedStation: ProductionBoardStation = {
      ...station,
      reminder_status: 'PLANNED',
      run: {
        id: 31,
        station_id: station.id,
        station_code: station.code,
        order_no: 'PLAN-031',
        mold_id: station.mounted_molds[0].id,
        mold_code: station.mounted_molds[0].asset_code,
        mold_model_code: station.mounted_molds[0].model_code,
        specification: '试模计划',
        material: 'NBR',
        order_quantity: 100,
        planned_mold_count: 50,
        produced_mold_count: 0,
        good_quantity: 0,
        progress_percent: 0,
        remaining_mold_count: 50,
        status: 'PLANNED',
        estimated_hours: 8,
      },
    }
    const onViewPlan = vi.fn()
    const onPutaway = vi.fn()
    render(<MountedMoldActionsDrawer open station={plannedStation} onClose={vi.fn()} onCreateProduction={vi.fn()} onViewPlan={onViewPlan} onPutaway={onPutaway} onSendOut={vi.fn()} />)

    expect(screen.queryByRole('button', { name: /登记生产/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /客户收回/ })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /查看并确认计划/ }))
    expect(onViewPlan).toHaveBeenCalledWith(plannedStation.run)
    fireEvent.click(screen.getByRole('button', { name: /试模结束并归位/ }))
    expect(onPutaway).toHaveBeenCalledWith(plannedStation.mounted_molds[0])
  })
})
