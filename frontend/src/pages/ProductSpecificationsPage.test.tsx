import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ReadOnlyContext } from '../access'
import { ProductSpecificationsPage } from './ProductSpecificationsPage'

const api = vi.hoisted(() => ({ list: vi.fn() }))
vi.mock('../api/client', () => ({ productSpecificationApi: api, toList: (value: unknown) => value }))
vi.mock('../components/ProductSpecificationDrawer', () => ({ ProductSpecificationDrawer: ({ open }: { open: boolean }) => open ? <div>产品编辑表单</div> : null }))
vi.mock('../components/BusinessImportDrawer', () => ({ BusinessImportDrawer: () => null }))
Object.defineProperty(window, 'matchMedia', { writable: true, value: vi.fn().mockImplementation((media: string) => ({ matches: false, media, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() })) })
class ResizeObserverMock { observe() {} unobserve() {} disconnect() {} }
globalThis.ResizeObserver = ResizeObserverMock

describe('product reference before editing', () => {
  beforeEach(() => api.list.mockResolvedValue([{ id: 1, product_name: '测试圈', specification: '240', material: 'N7200', is_active: true, actual_material_length: '321mm', actual_cut_weight: '21g', secondary_curing: '180°C / 4h' }]))
  it.each([false, true])('opens read-only reference first, preserves explicit edit access: readOnly=%s', async (readOnly) => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><ReadOnlyContext value={readOnly}><App><ProductSpecificationsPage /></App></ReadOnlyContext></QueryClientProvider>)
    await user.click(await screen.findByText('测试圈'))
    expect(await screen.findByText('测试圈 · 产品资料')).toBeInTheDocument()
    expect(screen.queryByText('产品编辑表单')).not.toBeInTheDocument()
    expect(screen.getByText('二烤参考（不代表本批已执行）')).toBeInTheDocument()
    expect(screen.getByText('321mm / 21g')).toBeInTheDocument()
    const edit = within(screen.getByRole('dialog')).getByRole('button', { name: /编辑资料/ })
    if (readOnly) expect(edit).toBeDisabled()
    else { await user.click(edit); expect(screen.getByText('产品编辑表单')).toBeInTheDocument() }
  })
})
