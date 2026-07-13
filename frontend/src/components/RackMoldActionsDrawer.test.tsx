import { App } from 'antd'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { MoldAsset } from '../types'
import { RackMoldActionsDrawer } from './RackMoldActionsDrawer'

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

const mold = {
  id: 9,
  asset_code: 'MJ-009',
  mold_model: { id: 3, code: 'ABC-100', product_name: '密封圈' },
  status: 'IN_STOCK',
  slot: { id: 1, display_code: 'J01-L01-A-P01' },
} satisfies MoldAsset

describe('RackMoldActionsDrawer', () => {
  it('offers correction, movement, release and deletion actions for an occupied slot', async () => {
    const user = userEvent.setup()
    const actions = {
      onClose: vi.fn(),
      onEdit: vi.fn(),
      onMove: vi.fn(),
      onLoadMachine: vi.fn(),
      onRelease: vi.fn(),
      onDelete: vi.fn(),
      onViewDetails: vi.fn(),
    }
    render(<App><RackMoldActionsDrawer open mold={mold} {...actions} /></App>)

    expect(document.querySelector('.rack-mold-actions-drawer')).toBeInTheDocument()
    expect(screen.getByText('J01-L01-A-P01')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /编辑编号和模具资料/ }))
    await user.click(screen.getByRole('button', { name: /移到其他库位/ }))
    await user.click(screen.getByRole('button', { name: /安排上机/ }))
    await user.click(screen.getByRole('button', { name: /客户收回并释放库位/ }))
    await user.click(screen.getByRole('button', { name: /删除误录记录并清空库位/ }))
    await user.click(screen.getByRole('button', { name: /查看完整资料和操作历史/ }))

    expect(actions.onEdit).toHaveBeenCalledOnce()
    expect(actions.onMove).toHaveBeenCalledOnce()
    expect(actions.onLoadMachine).toHaveBeenCalledOnce()
    expect(actions.onRelease).toHaveBeenCalledOnce()
    expect(actions.onDelete).toHaveBeenCalledOnce()
    expect(actions.onViewDetails).toHaveBeenCalledOnce()
  })

  it('labels the machine action as changing machine for a mold already on a machine', () => {
    const onMachine = {
      ...mold,
      status: 'ON_MACHINE' as const,
      slot: null,
      machine: { id: 2, code: '2', name: '2号机台' },
    }
    render(
      <App>
        <RackMoldActionsDrawer
          open
          mold={onMachine}
          onClose={vi.fn()}
          onEdit={vi.fn()}
          onMove={vi.fn()}
          onLoadMachine={vi.fn()}
          onRelease={vi.fn()}
          onDelete={vi.fn()}
          onViewDetails={vi.fn()}
        />
      </App>,
    )

    expect(screen.getByRole('button', { name: /更换机台/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /安排上机/ })).not.toBeInTheDocument()
  })
})
