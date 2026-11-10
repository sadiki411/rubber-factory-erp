import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { ProductionRecordPage } from './ProductionRecordPage'

const api = vi.hoisted(() => ({ detailRun: vi.fn() }))
vi.mock('../api/client', () => ({ productionApi: api, ApiError: class extends Error {} }))
Object.defineProperty(window, 'matchMedia', { writable: true, value: vi.fn().mockImplementation((media: string) => ({ matches: false, media, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() })) })
class ResizeObserverMock { observe() {} unobserve() {} disconnect() {} }
globalThis.ResizeObserver = ResizeObserverMock

it('opens a finished run by real ID independently of active lists, and returns to the original section/page', async () => {
  api.detailRun.mockResolvedValue({ id: 10, order_no: 'SAMPLE-162', specification: '215', material: 'N7200', status: 'COMPLETED', cavities: 100, daily_logs: [] })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/production/records/10?return_to=%2Forder-overview%2F162%3Fsection%3Dproduction%26page%3D2']}><Routes><Route path="/production/records/:id" element={<ProductionRecordPage />} /><Route path="/order-overview/:id" element={<div>订单162原节原页</div>} /></Routes></MemoryRouter></QueryClientProvider>)
  expect(await screen.findByText('SAMPLE-162 / 未登记')).toBeInTheDocument()
  expect(api.detailRun).toHaveBeenCalledWith('10')
  expect(screen.queryByRole('button', { name: /保存|编辑|取消任务/ })).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '返回来源页面' }))
  expect(await screen.findByText('订单162原节原页')).toBeInTheDocument()
})
