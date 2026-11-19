import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { flowApi } from '../flowcards'
import { ReadOnlyContext } from '../access'
import { InternalShipmentTrace } from './InternalShipmentTrace'

vi.mock('../flowcards', async (original) => ({ ...await original<typeof import('../flowcards')>(), flowApi: { sources: vi.fn(), reviewSource: vi.fn() } }))
Object.defineProperty(window, 'matchMedia', { writable: true, value: vi.fn().mockImplementation((query: string) => ({ matches: false, media: query, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() })) })

describe('internal source history', () => {
  beforeEach(() => vi.clearAllMocks())
  it('loads only when opened, paginates all rows, and preserves revoked-state meaning', async () => {
    vi.mocked(flowApi.sources).mockImplementation(async (_id, page = 1) => ({ count: 21, results: [{ id: page, card_id: `CARD-${page}`, card_no: `FC-DEMO-${page}`, state: page === 1 ? 'CONFIRMED' : 'REVOKED', scope: 'SET_UNKNOWN' }] }))
    render(<ReadOnlyContext.Provider value={true}><InternalShipmentTrace batchId={100} /></ReadOnlyContext.Provider>)
    expect(flowApi.sources).not.toHaveBeenCalled()
    fireEvent.click(screen.getByText(/工序流转卡来源/))
    expect(await screen.findByText('FC-DEMO-1')).toBeInTheDocument()
    fireEvent.click(screen.getByTitle('2'))
    expect(await screen.findByText('FC-DEMO-2')).toBeInTheDocument()
    await waitFor(() => expect(flowApi.sources).toHaveBeenLastCalledWith(100, 2))
    expect(screen.getByText('原错误关联已撤销（保留历史）')).toBeInTheDocument()
    expect(screen.queryByText(/人工核对 \/ 撤销错误关系/)).not.toBeInTheDocument()
  })
  it('does not turn read errors into no-source results', async () => {
    vi.mocked(flowApi.sources).mockRejectedValue(new Error('隔离网络失败'))
    render(<InternalShipmentTrace batchId={101} />)
    fireEvent.click(screen.getByText(/工序流转卡来源/))
    expect(await screen.findByText('内部来源读取失败，不视为无来源')).toBeInTheDocument()
    expect(screen.queryByText('未建立内部包追溯；不猜填历史')).not.toBeInTheDocument()
  })
})
