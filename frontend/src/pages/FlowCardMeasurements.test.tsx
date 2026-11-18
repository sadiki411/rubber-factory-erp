import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { FlowCardDetailPage } from './FlowCardsPage'
import { flowApi, flowLimitText, type Criterion, type FlowCardDetail, type FlowEvent } from '../flowcards'
import { qualityApi } from '../api/client'

vi.mock('../api/client', () => ({ qualityApi: { listEmployees: vi.fn() }, toList: (v: unknown) => v }))
vi.mock('../flowcards', async (original) => ({ ...await original<typeof import('../flowcards')>(), flowApi: { card: vi.fn(), events: vi.fn(), record: vi.fn(), bakeSamples: vi.fn(), scan: vi.fn() } }))
Object.defineProperty(window, 'matchMedia', { writable: true, value: vi.fn().mockImplementation((query: string) => ({ matches: false, media: query, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() })) })
vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
const criterion: Criterion = { id: 1, name: '线径', stage: 'DIMENSION', lower: '', upper: '3.05', unit: 'mm', method: '批准示例方法', limit_mode: 'UPPER', upper_inclusive: true }
const original: FlowEvent = { id: 'reading-1', card_ids: ['card-1'], kind: 'DIMENSION', employee_name: '示例员', business_at: '2026-11-18T10:00:00Z', created_at: '2026-11-18T10:01:00Z', scope: '本次抽测', evidence: '隔离', replaces_id: null,
  values: { actual: '3.06', unit: 'mm', criterion_id: 1, standard_snapshot: criterion, judgment: 'OUTSIDE', limit_side: 'UPPER', difference: '0.01', measurement_message: '超上限 0.01 mm' } }
const detail = { id: 'card-1', card_no: 'FC-20261118-ISOLATED', write_enabled: true, planned_molds: 100, sequence: 1, denominator: 1, source_status: 'PLAN_ONLY', whole_mold_status: 'NOT_REQUIRED', unresolved_anomalies: 0,
  snapshot: { product: { id: 1, specification: '100x3', material: 'DEMO' }, orders: [], template: { version: '草稿' }, whole_mold: 'NOT_REQUIRED', standard: { id: 1, version: 1, source: '隔离', snapshot: { criteria: [criterion] } } }, measurement_alerts: [original], measurement_status: [] } as unknown as FlowCardDetail
function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/flow-cards/cards/card-1']}><App><Routes><Route path="/flow-cards/cards/:id" element={<FlowCardDetailPage />} /></Routes></App></MemoryRouter></QueryClientProvider>)
}
describe('Measurement review UI', () => {
  beforeEach(() => { vi.clearAllMocks(); vi.mocked(flowApi.card).mockResolvedValue(detail); vi.mocked(flowApi.events).mockResolvedValue({ count: 1, results: [original] }); vi.mocked(qualityApi.listEmployees).mockResolvedValue([]); vi.mocked(flowApi.bakeSamples).mockResolvedValue({ count: 0, results: [] }) })
  it('shows upper-only, two-sided, strict and pending text without an invented zero', () => {
    expect(flowLimitText(criterion)).toBe('≤ 3.05 mm')
    expect(flowLimitText({ ...criterion, upper_inclusive: false })).toBe('< 3.05 mm')
    expect(flowLimitText({ ...criterion, lower: '2.95', limit_mode: 'RANGE' })).toBe('2.95～3.05 mm')
    expect(flowLimitText({ ...criterion, limit_mode: 'PENDING' })).toBe('界限待确认')
  })
  it('shows the actual exceedance before the ordinary description and opens a linked retest', async () => {
    mount(); expect(await screen.findByText('线径超上限，待复核')).toBeInTheDocument()
    expect(screen.getByText(/仅为本次检查，不判整批合格/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /^复测 \/ 更正$/ }))
    expect(await screen.findByLabelText('更正/复测关联原记录ID（可空，原值不会被删）')).toHaveValue('reading-1')
    expect(screen.getByLabelText('实际值')).toHaveValue('')
    fireEvent.change(screen.getByLabelText('实际值'), { target: { value: '3.04' } })
    expect(await screen.findByText('实测（允许0）')).toBeInTheDocument()
    fireEvent.mouseDown(screen.getByRole('combobox', { name: '值的状态' }))
    fireEvent.click((await screen.findAllByText('未测')).at(-1)!)
    await waitFor(() => expect(screen.getByLabelText('实际值')).toHaveValue(''))
    expect(flowApi.record).not.toHaveBeenCalled()
  }, 30000)
  it('selects a concrete bake coupon and its shared coverage without copying IDs', async () => {
    const bake = { ...original, id: 'bake-1', kind: 'BAKE', values: { temperature_c: '150', minutes: '120', coupon_samples: [{ sample_ref: '胶片A', material_ref: '当批胶料A', card_ids: ['card-1', 'card-2'], trays: [1, 3], accompanied: true }] } }
    vi.mocked(flowApi.events).mockResolvedValue({ count: 1, results: [bake] })
    mount(); fireEvent.click(await screen.findByRole('button', { name: '记烤后硬度' }))
    expect(await screen.findByLabelText('本厂自制胶片原始标识')).toHaveValue('胶片A')
    expect(screen.getByLabelText('当批胶料原始标识')).toHaveValue('当批胶料A')
    expect((screen.getByLabelText('本次检测来源') as HTMLInputElement).value).toContain('代表2卡')
    expect(screen.queryByLabelText(/实际随烤记录ID/)).not.toBeInTheDocument()
  }, 30000)
})
