import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PublicLocationHistory } from './PublicLocationHistory'

const apiMocks = vi.hoisted(() => ({ moldHistory: vi.fn(), inventoryHistory: vi.fn() }))
vi.mock('../api/client', () => ({
  slotApi: { publicHistory: apiMocks.moldHistory },
  inventoryApi: { publicLocationHistory: apiMocks.inventoryHistory },
}))

const entry = {
  id: 1, created_at: '2026-10-09T08:00:00+08:00', operation_label: '出库',
  item_code: 'P-240', item_name: '密封圈', specification: '240', material: 'N7200',
  quantity_change: -100, from_location: 'K01-L01-P01', to_location: null,
}

function renderHistory(kind: 'inventory' | 'mold' = 'inventory', locationKey = 'K01-L01-P01') {
  const client = new QueryClient({ defaultOptions: { queries: { retryDelay: 0 } } })
  return render(<QueryClientProvider client={client}><PublicLocationHistory kind={kind} locationKey={locationKey} /></QueryClientProvider>)
}

describe('PublicLocationHistory', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    apiMocks.inventoryHistory.mockResolvedValue({ count: 0, next: null, results: [] })
    apiMocks.moldHistory.mockResolvedValue({ count: 0, next: null, results: [] })
  })

  it('loads location-specific history in mobile cards, paginates and deduplicates page boundaries', async () => {
    const user = userEvent.setup()
    apiMocks.inventoryHistory.mockResolvedValueOnce({ count: 2, next: '?page=2', results: [entry] })
      .mockResolvedValueOnce({ count: 2, next: null, results: [entry, { ...entry, id: 2, item_code: 'OLD-P', operation_label: '入库', quantity_change: 4000 }] })
    const { container } = renderHistory()
    expect(await screen.findByText('P-240')).toBeInTheDocument()
    expect(screen.getByText('240 / N7200')).toBeInTheDocument()
    expect(screen.getByText('-100 件')).toBeInTheDocument()
    expect(container.querySelector('table')).toBeNull()
    await user.click(screen.getByRole('button', { name: '加载更多历史记录' }))
    expect(await screen.findByText('OLD-P')).toBeInTheDocument()
    expect(screen.getAllByText('P-240')).toHaveLength(1)
    expect(apiMocks.inventoryHistory).toHaveBeenNthCalledWith(1, 'K01-L01-P01', 1)
    expect(apiMocks.inventoryHistory).toHaveBeenNthCalledWith(2, 'K01-L01-P01', 2)
    expect(screen.getByText('已显示全部已登记记录')).toBeInTheDocument()
  })

  it('shows mold moves with source and target instead of loading the current mold history', async () => {
    apiMocks.moldHistory.mockResolvedValue({ count: 1, next: null, results: [{ ...entry, item_code: 'MOLD-14', specification: '14x2.5', material: '', operation_label: '上机', quantity_change: undefined, from_location: 'J01-06-A-02-01', to_machine: 'MC-3' }] })
    renderHistory('mold', '42')
    expect(await screen.findByText('MOLD-14')).toBeInTheDocument()
    expect(screen.getByText('库位 J01-06-A-02-01 → 机台 MC-3')).toBeInTheDocument()
    expect(apiMocks.moldHistory).toHaveBeenCalledWith('42', 1)
    expect(apiMocks.inventoryHistory).not.toHaveBeenCalled()
  })

  it('shows empty history', async () => {
    renderHistory()
    expect(await screen.findByText('该库位暂无已登记的历史记录')).toBeInTheDocument()
  })

  it('can retry failed history without blocking the current details', async () => {
    apiMocks.inventoryHistory.mockRejectedValue(new Error('network failure'))
    renderHistory()
    expect(await screen.findByText('历史记录读取失败')).toBeInTheDocument()
    apiMocks.inventoryHistory.mockResolvedValue({ count: 1, next: null, results: [entry] })
    await userEvent.click(screen.getByRole('button', { name: /重\s*试/ }))
    expect(await screen.findByText('P-240')).toBeInTheDocument()
  })

  it('keeps the first page visible after a failed next page and retries that page', async () => {
    apiMocks.inventoryHistory.mockResolvedValueOnce({ count: 2, next: '?page=2', results: [entry] }).mockRejectedValue(new Error('offline'))
    renderHistory()
    await screen.findByText('P-240')
    await userEvent.click(screen.getByRole('button', { name: '加载更多历史记录' }))
    await screen.findByText('历史记录读取失败')
    expect(screen.getByText('P-240')).toBeInTheDocument()
    apiMocks.inventoryHistory.mockResolvedValue({ count: 2, next: null, results: [{ ...entry, id: 2, item_code: 'OLD-P' }] })
    await userEvent.click(screen.getByRole('button', { name: /重\s*试/ }))
    await screen.findByText('OLD-P')
    await waitFor(() => expect(apiMocks.inventoryHistory).toHaveBeenLastCalledWith('K01-L01-P01', 2))
  })
})
