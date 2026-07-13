import { fireEvent, render, screen, within } from '@testing-library/react'
import dayjs from 'dayjs'
import { vi } from 'vitest'
import type { ProductionBoard as ProductionBoardData, ProductionBoardStation, ProductionStationGroup, ProductionStationPosition } from '../types'
import { ProductionBoard } from './ProductionBoard'

function idleStation(id: number, group: ProductionStationGroup, position: ProductionStationPosition): ProductionBoardStation {
  return {
    id,
    code: String(position),
    group,
    position_no: position,
    is_active: true,
    reminder_status: 'IDLE',
    minutes_to_change: null,
    run: null,
  }
}

const board: ProductionBoardData = {
  generated_at: dayjs().toISOString(),
  reminder_window_minutes: 60,
  counts: { total: 6, idle: 5, planned: 0, running: 1, due_soon: 0, overdue: 1 },
  groups: [
    {
      group: 'A',
      stations: [
        {
          id: 1,
          code: '1',
          group: 'A',
          position_no: 1,
          is_active: true,
          reminder_status: 'OVERDUE',
          minutes_to_change: -10,
          run: {
            id: 9,
            order_no: 'ORD-009',
            station_id: 1,
            station_code: '1',
            specification: '20×2',
            material: 'N7200',
            order_quantity: 600,
            planned_mold_count: 100,
            produced_mold_count: 80,
            good_quantity: 470,
            progress_percent: '80.00',
            remaining_mold_count: 20,
            status: 'RUNNING',
            loaded_at: dayjs().subtract(2, 'hour').toISOString(),
            expected_change_at: dayjs().subtract(10, 'minute').toISOString(),
            estimated_hours: '2.00',
          },
        },
        idleStation(2, 'A', 2),
      ],
    },
    { group: 'B', stations: [idleStation(3, 'B', 3), idleStation(4, 'B', 4)] },
    { group: 'C', stations: [idleStation(5, 'C', 5), idleStation(6, 'C', 6)] },
  ],
}

describe('ProductionBoard', () => {
  it('shows three connected two-machine groups and lets the user select an idle machine', () => {
    const onStationClick = vi.fn()
    render(<ProductionBoard board={board} onStationClick={onStationClick} />)
    expect(screen.getByText('三组双联机台实时看板')).toBeInTheDocument()
    expect(screen.getByText('每组2台，共6台；点击机台登记上模、补录日报或下机')).toBeInTheDocument()
    expect(screen.getByText('一组机台')).toBeInTheDocument()
    expect(screen.getByText('二组机台')).toBeInTheDocument()
    expect(screen.getByText('三组机台')).toBeInTheDocument()
    expect(screen.getAllByRole('group', { name: /双联设备/ })).toHaveLength(3)
    const firstGroup = screen.getByRole('group', { name: '一组机台双联设备：1号机台与2号机台' })
    expect(within(firstGroup).getAllByRole('button')).toHaveLength(2)
    expect(screen.getByRole('group', { name: '二组机台双联设备：3号机台与4号机台' })).toBeInTheDocument()
    expect(screen.getByRole('group', { name: '三组机台双联设备：5号机台与6号机台' })).toBeInTheDocument()
    expect(screen.getByText('3号机台与4号机台相连')).toBeInTheDocument()
    expect(screen.getByText('5号机台与6号机台相连')).toBeInTheDocument()
    expect(screen.getAllByText('运行 0 / 2 台')).toHaveLength(2)
    expect(screen.getByText('运行 1 / 2 台')).toBeInTheDocument()
    expect(screen.getByText('ORD-009')).toBeInTheDocument()
    expect(screen.getByText('已超时')).toBeInTheDocument()
    expect(screen.getByText(/超时 \d+分钟/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '一组机台 2号机台，空闲，登记上模' }))
    expect(onStationClick).toHaveBeenCalledWith(board.groups[0].stations[1])
  })

  it('shows a safe placeholder when a planned run has no production timestamps', () => {
    const plannedBoard: ProductionBoardData = {
      ...board,
      groups: [{
        group: 'A',
        stations: [{
          ...board.groups[0].stations[0],
          reminder_status: 'PLANNED',
          minutes_to_change: null,
          run: {
            ...board.groups[0].stations[0].run!,
            status: 'PLANNED',
            loaded_at: null,
            expected_change_at: null,
          },
        }],
      }],
    }
    render(<ProductionBoard board={plannedBoard} onStationClick={vi.fn()} />)
    expect(screen.getByText('待上模')).toBeInTheDocument()
    expect(screen.queryByText(/Invalid Date/)).not.toBeInTheDocument()
    expect(screen.getAllByText('-').length).toBeGreaterThan(0)
  })
})
