import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { QualityShipmentCardSource } from '../types'
import { ShipmentCardSources, ShipmentCardTraceDrawer } from './QualityShipmentTrace'

const mocks = vi.hoisted(() => ({ getCardShipmentTrace: vi.fn() }))
vi.mock('../api/client', () => ({ qualityWorkflowApi: mocks }))

Object.defineProperty(window, 'matchMedia', { writable: true, value: vi.fn().mockImplementation((query: string) => ({
  matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
})) })
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }

const sources: QualityShipmentCardSource[] = Array.from({ length: 7 }, (_, index) => ({
  card_id: index + 1, card_no: `04-M003-260918002${index}`, shipment_unit_no: index + 1,
  quantity_scope: 'PHYSICAL_UNIT', piece_quantity: 9419, net_weight_kg: '10.600',
  evidence: [{ code: 'UNIT_BINDING', label: '系统物理包绑定' }],
}))

describe('shipment identity evidence', () => {
  beforeEach(() => mocks.getCardShipmentTrace.mockReset())

  it('shows seven cards with compact expansion and keeps missing evidence distinct from no scan', () => {
    const { rerender } = render(<ShipmentCardSources sources={sources} />)
    expect(screen.getByText(/已找到 7 张关联卡/)).toBeInTheDocument()
    expect(screen.getByText('展开其余 5 条关联')).toBeInTheDocument()
    expect(screen.getAllByText(/04-M003-260918002/)).toHaveLength(7)
    expect(screen.getAllByText('系统物理包绑定')).toHaveLength(7)
    rerender(<ShipmentCardSources sources={[]} />)
    expect(screen.getByText('未记录明确关联的流程卡；不能据此判断当时未扫码。')).toBeInTheDocument()
  })

  it('distinguishes loading, error/retry and first shipment without returns', async () => {
    mocks.getCardShipmentTrace.mockRejectedValueOnce(new Error('网络断开')).mockResolvedValueOnce({
      card_id: 1, card_no: 'CARD', cards: [{ id: 1, card_no: 'CARD', status: 'SHIPPED' }],
      shipment_count: 1, return_count: 0, limitations: [], events: [{
        key: 'shipment:1', id: 1, type: 'shipment', document_no: 'QS-FIRST', business_date: '2026-11-02',
        occurred_at: '2026-11-02T10:00:00+08:00', status: 'CONFIRMED', batch_quantity: 65933,
        batch_weight_kg: '74.200', quantity_evidence: '已保存件数', url: '/quality?shipment_batch_id=1',
      }],
    })
    render(<ShipmentCardTraceDrawer cardId={1} cardNo="CARD" onClose={vi.fn()} />)
    expect(screen.getByLabelText('正在读取出货追溯')).toBeInTheDocument()
    expect(await screen.findByText('出货追溯读取失败')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /重\s*试/ }))
    expect(await screen.findByText('QS-FIRST')).toBeInTheDocument()
    expect(screen.getByText('1 张出货单 / 0 条退回记录（含取消历史）')).toBeInTheDocument()
    expect(screen.getByText('整张出货单：65,933 件 / 74.2 kg')).toBeInTheDocument()
    expect(screen.getByText('本页不受出货列表日期筛选')).toBeInTheDocument()
    await waitFor(() => expect(mocks.getCardShipmentTrace).toHaveBeenCalledTimes(2))
  })

  it('never displays missing historical pieces as zero', async () => {
    mocks.getCardShipmentTrace.mockResolvedValue({ card_id: 1, card_no: 'CARD', cards: [], shipment_count: 1, return_count: 1, limitations: [], events: [
      { key: 'shipment:1', id: 1, type: 'shipment', document_no: 'QS-UNKNOWN', status: 'CONFIRMED', batch_quantity: null, quantity_evidence: '件数及折算依据缺失', url: '/quality' },
      { key: 'return:1', id: 1, type: 'return', document_no: 'RT-UNKNOWN', status: 'PENDING', piece_quantity: null, url: '/quality' },
    ] })
    render(<ShipmentCardTraceDrawer cardId={1} cardNo="CARD" onClose={vi.fn()} />)
    expect(await screen.findByText('件数及折算依据缺失（不显示为0）')).toBeInTheDocument()
    expect(screen.getByText(/退回 件数未记录/)).toBeInTheDocument()
    expect(screen.queryByText(/整张出货单：0/)).not.toBeInTheDocument()
  })
})
