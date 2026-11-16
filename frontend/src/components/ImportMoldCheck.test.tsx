import { App, Button } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { ImportMoldCheck } from './ImportMoldCheck'
import type { ImportMoldCheck as MoldCheck } from '../types'

Object.defineProperty(window, 'matchMedia', { writable: true, value: vi.fn().mockImplementation((query: string) => ({ matches: query.includes('max-width'), media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })) })
class ResizeObserverMock { observe() {} unobserve() {} disconnect() {} }
globalThis.ResizeObserver = ResizeObserverMock

const api = vi.hoisted(() => ({ checkMolds: vi.fn() }))
vi.mock('../api/client', () => ({ businessImportApi: api }))

const check: MoldCheck = { version: 1, checked_at: '2026-11-16T08:00:00+08:00', alert_count: 1,
  model_options: [{ id: 5, code: 'MOLD-240', product_name: '测试圈模具', is_active: true, in_stock: 0, on_machine: 1, returned: 0 }],
  rows: [{ binding_key: 'source:product-1', product_id: null, specification: '240', material: 'N7200', customer_mold_no: 'CUSTOMER-240',
    can_bind: true, orders: [{ order_no: 'ORDER-1', item_no: '1', sheet: 'sheet1', row: 2 }], mold_model_id: null, mold_model_code: '', selection_source: 'UNLINKED', remembered_model_id: null,
    status: 'UNLINKED', message: '尚未关联模具型号，可搜索后选择，也可暂不关联继续导入。', notices: [], needs_attention: true, assets: [] }],
}
const selected: MoldCheck = { ...check, alert_count: 0, rows: [{ ...check.rows[0], mold_model_id: 5, mold_model_code: 'MOLD-240', selection_source: 'MANUAL',
  status: 'ON_MACHINE', message: '模具在厂，正在上机占用1副，不算缺模。', needs_attention: false,
  assets: [{ id: 9, asset_code: 'MOLD-240-01', status: 'ON_MACHINE', status_display: '上机', location: '3号机台' }] }] }

function Harness({ initial = check }: { initial?: MoldCheck }) {
  const [current, setCurrent] = useState<MoldCheck | null | undefined>(initial)
  const [busy, setBusy] = useState(false)
  return <><ImportMoldCheck check={current} token="token-1" disabled={busy} onChange={setCurrent} onBusyChange={setBusy} /><Button disabled={busy}>确认整批导入</Button></>
}
function show(node = <Harness />) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}><App>{node}</App></QueryClientProvider>)
}

describe('import mold checks', () => {
  beforeEach(() => { api.checkMolds.mockReset() })

  it('allows import with an unlinked or unregistered mold without another acknowledgement gate', () => {
    show()
    expect(screen.getByText(/仍可继续导入/)).toBeVisible()
    expect(screen.getByRole('button', { name: '确认整批导入' })).toBeEnabled()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(api.checkMolds).not.toHaveBeenCalled()
  })

  it('stores a draft choice and shows the real mounted asset and machine on mobile', async () => {
    const user = userEvent.setup()
    api.checkMolds.mockResolvedValue({ mold_check: selected })
    show()
    await user.click(screen.getByRole('combobox', { name: '关联模具型号 240 N7200' }))
    await user.click(await screen.findByRole('option', { name: /MOLD-240 · 测试圈模具/ }))
    await waitFor(() => expect(api.checkMolds).toHaveBeenCalledWith('token-1', { binding_key: 'source:product-1', mold_model_id: 5 }))
    expect(await screen.findByText('MOLD-240-01 · 上机 · 3号机台')).toBeVisible()
    expect(screen.getByText('在厂 · 上机占用')).toBeVisible()
    expect(screen.getByRole('button', { name: '确认整批导入' })).toBeEnabled()
  }, 20_000)

  it('does not race import against a pending selection save', async () => {
    const user = userEvent.setup()
    let complete!: (value: { mold_check: MoldCheck }) => void
    api.checkMolds.mockReturnValue(new Promise((resolve) => { complete = resolve }))
    show()
    await user.click(screen.getByRole('button', { name: '重新核对模具状态' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '确认整批导入' })).toBeDisabled())
    complete({ mold_check: selected })
    await waitFor(() => expect(screen.getByRole('button', { name: '确认整批导入' })).toBeEnabled())
  })

  it('retains the last result and permits import after refresh fails', async () => {
    const user = userEvent.setup()
    api.checkMolds.mockRejectedValue(new Error('网络中断'))
    show(<Harness initial={selected} />)
    await user.click(screen.getByRole('button', { name: '重新核对模具状态' }))
    expect(await screen.findByText(/网络中断；原预检与已保存关联保留/)).toBeVisible()
    expect(screen.getByText('MOLD-240-01 · 上机 · 3号机台')).toBeVisible()
    expect(screen.getByRole('button', { name: '确认整批导入' })).toBeEnabled()
  })

  it('renders saved history without editable controls or fetching live state', () => {
    show(<ImportMoldCheck check={selected} token="old-token" />)
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '重新核对模具状态' })).not.toBeInTheDocument()
    expect(screen.getByText(/导入时的核对快照/)).toBeVisible()
    expect(api.checkMolds).not.toHaveBeenCalled()
  })
})
