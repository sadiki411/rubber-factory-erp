import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { InventoryPage } from './InventoryPage'

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({ matches: query.includes('max-width'), media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })),
})
class ResizeObserverMock { observe() {} unobserve() {} disconnect() {} }
globalThis.ResizeObserver = ResizeObserverMock

const apiMocks = vi.hoisted(() => ({
  summary: vi.fn(),
  locations: vi.fn(),
  containers: vi.fn(),
  products: vi.fn(),
  materialRemainders: vi.fn(),
  listEmployees: vi.fn(),
  listOrders: vi.fn(),
}))

vi.mock('../api/client', () => ({
  inventoryApi: {
    summary: apiMocks.summary,
    locations: apiMocks.locations,
    containers: apiMocks.containers,
    products: apiMocks.products,
    materialRemainders: apiMocks.materialRemainders,
    bootstrap: vi.fn(),
    receipt: vi.fn(),
    outbound: vi.fn(),
    setQuality: vi.fn(),
    createMaterialRemainder: vi.fn(),
    useMaterialRemainder: vi.fn(),
    moveContainer: vi.fn(),
  },
  qualityApi: { listEmployees: apiMocks.listEmployees },
  orderApi: { list: apiMocks.listOrders },
  toList: (payload: any) => Array.isArray(payload) ? payload : payload.results || [],
}))

const lastLocation = {
  id: 150,
  code: 'K09-L05-P02',
  rack_code: 'K09',
  rack_type: 'SMALL',
  level_no: 5,
  position_no: 2,
  label: 'K09 第5层 第2位',
  allows_basket: false,
  allows_bag: true,
  is_active: true,
  container: null,
}

describe('InventoryPage mobile inventory forms', () => {
  beforeEach(() => {
    apiMocks.summary.mockResolvedValue({ total_quantity: 0, available_quantity: 0, waiting_inspection_quantity: 0, active_containers: 0, occupied_locations: 0, location_count: 150 })
    apiMocks.locations.mockResolvedValue([lastLocation])
    apiMocks.containers.mockResolvedValue([])
    apiMocks.products.mockResolvedValue([])
    apiMocks.materialRemainders.mockResolvedValue([])
    apiMocks.listEmployees.mockResolvedValue([])
    apiMocks.listOrders.mockResolvedValue([])
  })

  it('loads complete selector datasets and exposes a mobile-safe location popup', async () => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><App><InventoryPage /></App></QueryClientProvider>)

    expect((await screen.findAllByText('K09-L05-P02')).length).toBeGreaterThan(0)
    expect(apiMocks.locations).toHaveBeenCalledWith({ active: true })
    expect(apiMocks.containers).toHaveBeenCalledWith({ active: true, page_size: 1000 })
    expect(apiMocks.materialRemainders).toHaveBeenCalledWith({ page_size: 1000 })

    await user.click(screen.getByRole('button', { name: /直接入库/ }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveClass('inventory-modal')
    expect(dialog.querySelectorAll('.ant-col-xs-24').length).toBeGreaterThan(0)

    const locationItem = within(dialog).getByText('库位').closest('.ant-form-item')
    const locationSelect = locationItem?.querySelector<HTMLElement>('[role="combobox"]')
    expect(locationSelect).toBeTruthy()
    await user.click(locationSelect!)

    await waitFor(() => expect(document.querySelector('.inventory-select-popup')).toBeInTheDocument())
    expect(await screen.findByRole('option', { name: /K09-L05-P02/ })).toBeInTheDocument()
  })

  it('fills immutable product identity and the latest quality unit weight when an existing product is selected', async () => {
    apiMocks.products.mockResolvedValue([{
      id: 362,
      product_code: 'P-362',
      product_name: '产品362',
      specification: '362',
      material: 'NBR',
      unit_weight_g: '1.80000',
      latest_quality_unit_weight_g: '2.25000',
      effective_unit_weight_g: '2.25000',
      product_specification: 9,
      is_active: true,
    }])
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><App><InventoryPage /></App></QueryClientProvider>)

    await user.click(await screen.findByRole('button', { name: /直接入库/ }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('combobox', { name: '已有产品' }))
    await user.click(await screen.findByText('P-362 · 362 · NBR'))

    expect(within(dialog).getByRole('textbox', { name: '产品编号' })).toHaveValue('P-362')
    expect(within(dialog).getByRole('textbox', { name: '规格' })).toHaveValue('362')
    expect(within(dialog).getByRole('textbox', { name: '规格' })).toHaveAttribute('readonly')
    expect(Number((within(dialog).getByRole('spinbutton', { name: '成品单重(g)' }) as HTMLInputElement).value)).toBe(2.25)
    const inspectorColumn = within(dialog).getByText('品检员（已检时必填）').closest('.ant-form-item')?.parentElement
    expect(inspectorColumn).toHaveClass('ant-col-sm-12')
  }, 30_000)
})
