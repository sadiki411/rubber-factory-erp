import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { IntegrationPreviewPage } from './IntegrationPreviewPage'

const api = vi.hoisted(() => ({ workbench: vi.fn(), compactHistory: vi.fn(), board: vi.fn() }))
vi.mock('../api/client', () => ({ orderApi: { workbench: api.workbench }, productionApi: api }))
Object.defineProperty(window, 'matchMedia', { writable: true, value: vi.fn().mockImplementation((media: string) => ({ matches: false, media, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() })) })

it('keeps the redesign isolated, loads compact history only on the production view and preserves access to old records', async () => {
  api.workbench.mockResolvedValue({ count: 0, results: [], summary: {}, generated_at: '2026-11-10' })
  api.compactHistory.mockResolvedValue({ count: 1, results: [{ id: 10, order_no: 'ORIGINAL-10', specification: '215', material: 'N7200', status: 'COMPLETED', production_record_count: 2, recorded_mold_count: 12 }] })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/previews/integration']}><IntegrationPreviewPage /></MemoryRouter></QueryClientProvider>)
  expect(await screen.findByText('此筛选无已登记待办；可切换全部历史')).toBeVisible()
  expect(api.compactHistory).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: '查看全部历史与类别' }))
  expect(api.workbench).toHaveBeenLastCalledWith(expect.objectContaining({ due_scope: '', page: 1 }), expect.anything())
  await userEvent.click(screen.getByRole('button', { name: '生产页预览' }))
  expect(await screen.findByText('ORIGINAL-10 / 未登记项次')).toBeVisible()
  expect(api.compactHistory).toHaveBeenCalledWith(expect.objectContaining({ page_size: 10 }), expect.anything())
  expect(screen.getByText('独立预览 · 尚未替换正式首页和生产页')).toBeVisible()
})
