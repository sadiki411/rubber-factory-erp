import { App, Grid } from 'antd'
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
  beforeEach(() => api.list.mockResolvedValue([{ id: 1, product_name: '测试圈', specification: '240', material: 'N7200', is_active: true, actual_material_length: '321mm', actual_cut_weight: '21g', secondary_curing: '180°C / 4h', large_strip_weight_g: '21.00', large_strip_count: 5, small_strip_weight_g: '11.00', small_strip_count: 2, forming_material_weight_g: '127.00', strip_count: '5/2', mold_in_stock: '现场确认记录', notes: '生产需留意合模' }]))
  it.each([[false, false], [false, true], [true, false], [true, true]])('shows saved parameters before editing: readOnly=%s, desktop=%s', async (readOnly, desktop) => {
    vi.spyOn(Grid, 'useBreakpoint').mockReturnValue({ md: desktop })
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><ReadOnlyContext value={readOnly}><App><ProductSpecificationsPage /></App></ReadOnlyContext></QueryClientProvider>)
    const product = await screen.findByText('测试圈')
    const listLayout = within(screen.getByLabelText('一模排料参数'))
    for (const value of ['大条条重', '21 g', '大条数量', '5 条', '小条条重', '11 g', '小条数量', '2 条', '一模排料总重', '127 g']) {
      expect(listLayout.getByText(value)).toBeInTheDocument()
    }
    expect(screen.queryByText('产品编辑表单')).not.toBeInTheDocument()
    await user.click(product)
    expect(await screen.findByText('测试圈 · 产品资料')).toBeInTheDocument()
    expect(screen.queryByText('产品编辑表单')).not.toBeInTheDocument()
    expect(screen.getByText('二烤参考（不代表本批已执行）')).toBeInTheDocument()
    const dialog = within(screen.getByRole('dialog'))
    expect(dialog.getByText('321mm / 21g')).toBeInTheDocument()
    expect(dialog.getByText('127 g')).toBeInTheDocument()
    expect(dialog.getByText('5/2')).toBeInTheDocument()
    expect(dialog.getByText('现场确认记录')).toBeInTheDocument()
    expect(dialog.getByText('生产需留意合模')).toBeInTheDocument()
    const edit = dialog.getByRole('button', { name: /编辑资料/ })
    if (readOnly) expect(edit).toBeDisabled()
    else { await user.click(edit); expect(screen.getByText('产品编辑表单')).toBeInTheDocument() }
  })
})
