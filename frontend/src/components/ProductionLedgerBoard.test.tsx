import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ProductionLedgerBoard } from './ProductionLedgerBoard'

const api = vi.hoisted(() => ({ listRuns: vi.fn() }))
vi.mock('../api/client', () => ({ productionApi: api, toList: (value: unknown) => value }))
vi.mock('./ProductionLedgerTaskDrawer', () => ({ ProductionLedgerTaskDrawer: ({ open }: { open: boolean }) => open ? <div>编辑生产任务表单</div> : null }))
vi.mock('./ProductionCounterDrawer', () => ({ ProductionCounterDrawer: () => null }))
vi.mock('./ProductionFinalYieldDrawer', () => ({ ProductionFinalYieldDrawer: () => null }))
Object.defineProperty(window, 'matchMedia', { writable: true, value: vi.fn().mockImplementation((media: string) => ({ matches: false, media, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() })) })

it('shows all five layout values directly in the hand ledger, with other saved parameters available read-only', async () => {
  api.listRuns.mockResolvedValue([{ id: 1, order_no: 'TEST-01', specification: '240', material: 'N7200', status: 'PLANNED', order_quantity: 1000, cavities: 16, planned_mold_count: 63, large_strip_weight_g: '21.00', large_strip_count: 5, small_strip_weight_g: '11.00', small_strip_count: 2, compound_size: '321×4mm', curing_seconds: 60, notes: '不可省略的任务备注' }])
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><App><ProductionLedgerBoard /></App></QueryClientProvider>)
  expect(await screen.findByText('TEST-01')).toBeInTheDocument()
  for (const value of ['21 g', '5 条', '11 g', '2 条', '127 g']) expect(screen.getByText(value)).toBeVisible()
  expect(screen.queryByText('编辑生产任务表单')).not.toBeInTheDocument()
  const user = userEvent.setup()
  await user.click(screen.getByText('其他生产参数'))
  for (const value of ['321×4mm', '16 孔', '60 秒', '不可省略的任务备注']) expect(screen.getByText(value)).toBeVisible()
  expect(screen.queryByText('编辑生产任务表单')).not.toBeInTheDocument()
  expect(api.listRuns).toHaveBeenCalledTimes(1)
})
