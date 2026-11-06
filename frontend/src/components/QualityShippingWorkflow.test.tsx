import { App } from 'antd'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { qualityWorkflowApi } from '../api/client'
import type { QualityOrder } from '../types'
import { QualityShippingWorkflow } from './QualityShippingWorkflow'

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

const order: QualityOrder = {
  id: 1,
  order_no: 'ORDER-FAILURE-1',
  item_no: '1',
  batch_no: 'B1',
  product_code: 'P1',
  product_name: '测试产品',
  specification: '240',
  material: 'N7200',
  order_quantity: 100,
  order_date: '2026-10-13',
  due_date: '2026-10-14',
  status: 'OPEN',
}

const baseProps = {
  orders: [order],
  employees: [],
  processCards: [],
  shipments: [],
  batches: [],
  reworks: [],
  reworkCases: [],
  onOpenRework: vi.fn(),
  onOpenTimeline: vi.fn(),
  onSubmitBatch: vi.fn().mockResolvedValue(undefined),
  onOpenShipment: vi.fn(),
}

describe('QualityShippingWorkflow', () => {
  it('reads the complete tracking history rather than the truncated page prop', async () => {
    const timeline = vi.spyOn(qualityWorkflowApi, 'listReworkTimeline').mockResolvedValue(Array.from({ length: 9 }, (_, index) => ({ id: index + 1, case_no: `R-HISTORY-${index + 1}`, origin: 'CUSTOMER_RETURN', status: 'RESHIPPED', opened_on: '2026-09-01', reason_category: 'OTHER', reason: '', affected_quantity: 10, attempts: [], return_round: index + 1 } as any)))
    try {
      render(<App><QualityShippingWorkflow {...baseProps} processCards={[{ id: 63, order, order_id: 1, card_no: 'READ-CARD', quantity: 100, rework_count: 9, status: 'ACTIVE' } as any]} /></App>)
      await userEvent.setup().click(screen.getByRole('button', { name: /时间线/ }))
      expect(await screen.findByText('R-HISTORY-1')).toBeInTheDocument()
      expect(screen.getByText('R-HISTORY-9')).toBeInTheDocument()
      expect(screen.getAllByText('实际返工作业未登记')).toHaveLength(9)
      expect(timeline).toHaveBeenCalledWith(63)
      expect(screen.queryByText('0轮返工')).not.toBeInTheDocument()
    } finally { timeline.mockRestore() }
  })
  it('preserves a successful empty process-card response without listing unrelated orders', () => {
    render(<App><QualityShippingWorkflow {...baseProps} processCardsTotal={0} /></App>)
    expect(screen.getByText('当前条件下暂无可用流程卡')).toBeInTheDocument()
    expect(screen.queryByText('订单待发需求（未登记物理卡）')).not.toBeInTheDocument()
  })

  it('uses the independent rework request state instead of showing a false zero', () => {
    const { rerender } = render(<App><QualityShippingWorkflow {...baseProps} processCardsTotal={0} reworkCasesLoading /></App>)
    const metric = screen.getByText('待处理返工（当前已加载）').closest('.ant-statistic')!
    expect(metric.querySelector('.ant-skeleton')).not.toBeNull()
    rerender(<App><QualityShippingWorkflow {...baseProps} processCardsTotal={0} reworkCasesError="返工查询超时" /></App>)
    expect(within(metric as HTMLElement).getByText('读取失败')).toBeInTheDocument()
    expect(within(metric as HTMLElement).queryByText('0')).not.toBeInTheDocument()
  })
  it('keeps a pending process-card request in loading state instead of falling back to orders', () => {
    const { rerender } = render(<App><QualityShippingWorkflow {...baseProps} processCardsLoading /></App>)
    expect(screen.getByText('正在读取流程卡，请稍候…')).toBeInTheDocument()
    expect(screen.queryByText('订单待发需求（未登记物理卡）')).not.toBeInTheDocument()

    rerender(<App><QualityShippingWorkflow {...baseProps} processCardsError="流程卡接口超时" onRetryProcessCards={vi.fn()} /></App>)
    expect(screen.getByText('流程卡读取失败')).toBeInTheDocument()
    expect(screen.getByText('流程卡接口超时')).toBeInTheDocument()
    expect(screen.queryByText('订单待发需求（未登记物理卡）')).not.toBeInTheDocument()
  })
})
