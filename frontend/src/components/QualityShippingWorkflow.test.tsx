import { App } from 'antd'
import { render, screen, within } from '@testing-library/react'
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
