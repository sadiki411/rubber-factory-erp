import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { FlowBakePanel } from './FlowBakePanel'
import { qualityApi } from '../api/client'
import { flowApi, type FlowCardDetail } from '../flowcards'

vi.mock('../api/client', () => ({ qualityApi: { listEmployees: vi.fn() }, toList: (v: unknown) => v }))
vi.mock('../flowcards', () => ({ flowApi: { record: vi.fn(), scan: vi.fn() }, flowRequestKey: () => 'isolated-bake-key', finishFlowRequest: vi.fn() }))
Object.defineProperty(window, 'matchMedia', { writable: true, value: vi.fn().mockImplementation((query: string) => ({ matches: false, media: query, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() })) })
vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><App><FlowBakePanel card={{ id: 'card-1', card_no: 'FC-ISOLATED-1' } as FlowCardDetail} disabled={false} changed={vi.fn()} /></App></QueryClientProvider>)
}
describe('Actual bake UI safety', () => {
  beforeEach(() => { vi.clearAllMocks(); vi.mocked(qualityApi.listEmployees).mockResolvedValue([]) })
  it('keeps actual time empty and rejects missing facts before any write', async () => {
    mount(); fireEvent.click(screen.getByRole('button', { name: '登记实际二烤 / 分盘续记' }))
    expect(screen.getByLabelText('实际作业日期时间')).toHaveValue('')
    fireEvent.click(screen.getByRole('button', { name: 'OK' }))
    await screen.findByText('二烤登记未确认')
    expect(flowApi.record).not.toHaveBeenCalled()
  }, 30000)
  it('clears sample coverage on add, removal or card-code change instead of shifting the represented card', async () => {
    mount(); fireEvent.click(screen.getByRole('button', { name: '登记实际二烤 / 分盘续记' }))
    fireEvent.click(screen.getByRole('checkbox', { name: '本次有本厂当批胶料自制胶片随产品二烤' }))
    fireEvent.click(await screen.findByRole('checkbox', { name: '第1张卡' }))
    expect(screen.getByRole('checkbox', { name: '第1张卡' })).toBeChecked()
    fireEvent.click(screen.getByRole('button', { name: '同次二烤再关联一张卡' }))
    await waitFor(() => expect(screen.getByRole('checkbox', { name: '第1张卡' })).not.toBeChecked())
    fireEvent.click(screen.getByRole('checkbox', { name: '第2张卡' }))
    fireEvent.click(screen.getAllByRole('button', { name: '移除此卡（未保存）' })[0])
    await waitFor(() => expect(screen.queryByRole('checkbox', { name: '第2张卡' })).not.toBeInTheDocument())
    expect(screen.getByRole('checkbox', { name: '第1张卡' })).not.toBeChecked()
    fireEvent.click(screen.getByRole('checkbox', { name: '第1张卡' }))
    fireEvent.change(screen.getByLabelText('本次第1张卡号'), { target: { value: 'FC-ISOLATED-OTHER' } })
    await waitFor(() => expect(screen.getByRole('checkbox', { name: '第1张卡' })).not.toBeChecked())
    expect(flowApi.record).not.toHaveBeenCalled()
  }, 30000)
})
