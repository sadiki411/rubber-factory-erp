import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RackDiagram } from './RackDiagram'
import type { RackLayout } from '../types'

const layout: RackLayout = {
  rack: { id: 1, code: 'J01', name: '1号模具架', locked: true },
  levels: [{
    id: 1,
    level_no: 1,
    zones: [{
      id: 1,
      code: 'A',
      name: '整层',
      current_capacity: 2,
      allowed_capacities: [2],
      stack_levels: 1,
      supports_stacking: false,
      stacking_enabled: false,
      is_active: true,
      slots: [
        { id: 1, display_code: 'J01-L01-A-P01', position_no: 1, stack_level: 1, active: true, mold: { id: 9, asset_code: 'MJ-009', status: 'IN_STOCK', model_code: 'ABC-100' } },
        { id: 2, display_code: 'J01-L01-A-P02', position_no: 2, stack_level: 1, active: true },
      ],
    }],
  }],
}

describe('RackDiagram', () => {
  it('renders rack position, occupied mold and empty slot', () => {
    render(<RackDiagram layout={layout} />)
    expect(screen.getByText('J01 · 1号模具架')).toBeInTheDocument()
    expect(screen.getByText('MJ-009')).toBeInTheDocument()
    expect(screen.getByText('ABC-100')).toBeInTheDocument()
    expect(screen.getByText('空位 · 点击放入')).toBeInTheDocument()
  })

  it('opens quick creation for an empty slot and still opens occupied mold details', async () => {
    const user = userEvent.setup()
    const onEmptySlotClick = vi.fn()
    const onMoldClick = vi.fn()
    render(<RackDiagram layout={layout} onEmptySlotClick={onEmptySlotClick} onMoldClick={onMoldClick} />)

    await user.click(screen.getByRole('button', { name: 'J01-L01-A-P02 空位' }))
    expect(onEmptySlotClick).toHaveBeenCalledWith(expect.objectContaining({ id: 2 }))

    await user.click(screen.getByRole('button', { name: 'J01-L01-A-P01 MJ-009' }))
    expect(onMoldClick).toHaveBeenCalledWith(9)
  })

  it('shows both S2 and S1 and invokes the stacking switch', async () => {
    const user = userEvent.setup()
    const onStackingChange = vi.fn()
    const zone = layout.levels[0].zones[0]
    const stackedLayout: RackLayout = {
      ...layout,
      levels: [{
        ...layout.levels[0],
        zones: [{
          ...zone,
          supports_stacking: true,
          stacking_enabled: true,
          stack_levels: 2,
          slots: [
            { id: 11, display_code: 'J01-L01-A-P01-S2', position_no: 1, stack_level: 2, active: true },
            { id: 12, display_code: 'J01-L01-A-P01-S1', position_no: 1, stack_level: 1, active: true },
            { id: 13, display_code: 'J01-L01-A-P02-S2', position_no: 2, stack_level: 2, active: true },
            { id: 14, display_code: 'J01-L01-A-P02-S1', position_no: 2, stack_level: 1, active: true },
          ],
        }],
      }],
    }

    render(<RackDiagram layout={stackedLayout} onStackingChange={onStackingChange} />)
    expect(screen.getByText('J01-L01-A-P01-S2')).toBeInTheDocument()
    expect(screen.getByText('J01-L01-A-P01-S1')).toBeInTheDocument()
    await user.click(screen.getByRole('switch', { name: '切换整层叠放' }))
    expect(onStackingChange).toHaveBeenCalledWith(expect.objectContaining({ id: 1 }), false)
  })

  it('marks inactive zones and shows a disabled slot reason', () => {
    const zone = layout.levels[0].zones[0]
    const specialLayout: RackLayout = {
      ...layout,
      levels: [{
        ...layout.levels[0],
        zones: [
          {
            ...zone,
            current_capacity: 1,
            allowed_capacities: [1],
            slots: [{ id: 21, display_code: 'J06-L07-B-P01-S1', position_no: 1, stack_level: 1, active: false, blocking_reason: '立柱旁禁止放模具' }],
          },
          {
            id: 22,
            code: 'C',
            name: '右侧杂物区',
            current_capacity: 2,
            allowed_capacities: [2, 3],
            stack_levels: 1,
            supports_stacking: true,
            stacking_enabled: false,
            is_active: false,
            blocking_reason: '上方用于堆放杂物',
            slots: [],
          },
        ],
      }],
    }

    render(<RackDiagram layout={specialLayout} onCapacityChange={vi.fn()} onStackingChange={vi.fn()} />)
    expect(screen.getByText('立柱旁禁止放模具')).toBeInTheDocument()
    expect(screen.getByText('禁放 / 杂物区')).toBeInTheDocument()
    expect(screen.getByText('上方用于堆放杂物')).toBeInTheDocument()
    expect(screen.queryByLabelText('切换右侧杂物区容量')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('切换右侧杂物区叠放')).not.toBeInTheDocument()
  })
})
