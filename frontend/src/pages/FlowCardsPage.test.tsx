/// <reference types="node" />
import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { FlowCardsPage } from './FlowCardsPage'
import { flowApi, type FlowContext } from '../flowcards'
import { ReadOnlyContext } from '../access'
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'

vi.mock('../flowcards', async (original) => ({ ...await original<typeof import('../flowcards')>(), flowApi: { context: vi.fn(), preview: vi.fn(), create: vi.fn(), groups: vi.fn(), templates: vi.fn() } }))
Object.defineProperty(window, 'matchMedia', { writable: true, value: vi.fn().mockImplementation((query: string) => ({ matches: false, media: query, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() })) })

const context: FlowContext = { write_enabled: true, product: { id: 1, name: '隔离示例', specification: '215', material: 'N7200' }, run_id: 10, mold_id: null, order_ids: [1, 2], orders: [{ id: 1, order_no: 'DEMO-ORDER-01', item_no: '3' }, { id: 2, order_no: 'DEMO-ORDER-02', item_no: '4' }], suggested_molds: 2050, standards: [], existing_groups: [], appearance: [{ id: 1, name: '毛刺' }, { id: 2, name: '杂质' }, { id: 3, name: '包风' }, { id: 4, name: '缺料' }], criteria: [], diagrams: [] }

function mount(readOnly = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><ReadOnlyContext.Provider value={readOnly}><MemoryRouter initialEntries={['/flow-cards?run_id=10']}><App><FlowCardsPage /></App></MemoryRouter></ReadOnlyContext.Provider></QueryClientProvider>)
}

describe('Flow-card preparation page', () => {
  beforeEach(() => {
    vi.clearAllMocks(); vi.mocked(flowApi.context).mockResolvedValue(context)
    vi.mocked(flowApi.groups).mockResolvedValue({ count: 0, results: [] })
    vi.mocked(flowApi.templates).mockResolvedValue({ renderer: 'review-v2', version: '草稿', published: false, write_enabled: true, history: [] })
  })
  it('shows the actual source and preview without creating identities', async () => {
    vi.mocked(flowApi.preview).mockResolvedValue({ count: 21, tail_molds: 50, planned_molds: 2050 })
    mount()
    expect(await screen.findByText('215 / N7200')).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText(/沿用任务确认计划/), { target: { value: '明确本次0至2050模' } })
    fireEvent.click(screen.getByRole('button', { name: /预览分卡/ }))
    await waitFor(() => expect(screen.getByText(/21张；尾卡 50模/)).toBeInTheDocument())
    expect(flowApi.create).not.toHaveBeenCalled()
    expect(vi.mocked(flowApi.preview).mock.calls[0][0]).toMatchObject({ product_id: 1, run_id: 10, order_ids: [1, 2], planned_molds: 2050 })
    // Unit tests must work in a clean checkout before the production build.
    // Review exports are opt-in and must not rewrite tracked evidence on every run.
    if (process.env.FLOW_CARDS_REVIEW_HTML) {
      const target = resolve(process.env.FLOW_CARDS_REVIEW_HTML)
      mkdirSync(dirname(target), { recursive: true })
      const css = readFileSync(resolve('src/pages/FlowCardsPage.css'), 'utf8')
      const notice = '<p style="background:#fff5d6;padding:12px">隔离数据 / 实际React组件的静态DOM预览（按钮无效，不代表浏览器或实机验收）。缩窄窗口可查看单列排版。</p>'
      writeFileSync(target, `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>工序流转卡隔离界面预览</title><style>${css}</style>${document.head.innerHTML}</head><body>${notice}${document.body.innerHTML}</body></html>`)
    }
  }, 30000)
  it('keeps loading failures distinct from zeros and never displays a successful empty result', async () => {
    vi.mocked(flowApi.context).mockRejectedValue(new Error('隔离读取失败'))
    mount()
    expect(await screen.findByText('来源读取失败，不猜配产品')).toBeInTheDocument()
    expect(screen.queryByText(/0张/)).not.toBeInTheDocument()
  })
  it('does not allow a read-only account to generate or confirm standards', async () => {
    mount(true)
    expect(await screen.findByText('215 / N7200')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '确认生成本次独立卡' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '维护产品确认标准' })).toBeDisabled()
  })
})
