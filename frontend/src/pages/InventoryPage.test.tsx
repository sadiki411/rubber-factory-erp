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
  receipt: vi.fn(),
  correctContainerProduct: vi.fn(),
  materialRemainders: vi.fn(),
  listEmployees: vi.fn(),
  listOrders: vi.fn(),
  setQuality: vi.fn(),
  outbound: vi.fn(),
  moveContainer: vi.fn(),
}))

vi.mock('../api/client', () => ({
  inventoryApi: {
    summary: apiMocks.summary,
    locations: apiMocks.locations,
    containers: apiMocks.containers,
    products: apiMocks.products,
    correctContainerProduct: apiMocks.correctContainerProduct,
    materialRemainders: apiMocks.materialRemainders,
    bootstrap: vi.fn(),
    receipt: apiMocks.receipt,
    outbound: apiMocks.outbound,
    setQuality: apiMocks.setQuality,
    createMaterialRemainder: vi.fn(),
    useMaterialRemainder: vi.fn(),
    moveContainer: apiMocks.moveContainer,
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
    apiMocks.receipt.mockReset().mockResolvedValue({ id: 83 })
    apiMocks.correctContainerProduct.mockResolvedValue({})
    apiMocks.materialRemainders.mockResolvedValue([])
    apiMocks.listEmployees.mockResolvedValue([])
    apiMocks.listOrders.mockResolvedValue([])
    apiMocks.setQuality.mockReset().mockResolvedValue({})
    apiMocks.outbound.mockReset().mockResolvedValue({})
    apiMocks.moveContainer.mockReset().mockResolvedValue({})
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

  it('allows received inventory product details to be corrected after putaway', async () => {
    apiMocks.locations.mockResolvedValue([{
      ...lastLocation,
      container: {
        id: 81,
        container_code: 'CT-081',
        container_type: 'BAG',
        batch_no: 'INV-081',
        product_id: 362,
        product_code: 'P-362',
        product_name: '旧名称',
        specification: '362',
        material: 'NBR',
        quantity: 1000,
        quality_status: 'PASSED',
        bag_count: 1,
        unit_weight_g: '2.25000',
      },
    }])
    apiMocks.products.mockResolvedValue([{
      id: 362,
      product_code: 'P-362',
      product_name: '旧名称',
      specification: '362',
      material: 'NBR',
      unit_weight_g: '2.25000',
      effective_unit_weight_g: '2.25000',
      is_active: true,
    }])
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><App><InventoryPage /></App></QueryClientProvider>)

    await user.click(await screen.findByRole('button', { name: '改产品资料' }))
    const title = await screen.findByText('更正库存产品资料 · CT-081')
    const dialog = title.closest('[role="dialog"]') as HTMLElement
    expect(dialog).toBeInTheDocument()
    const nameInput = within(dialog).getByRole('textbox', { name: '产品名称' })
    expect(nameInput).toHaveValue('旧名称')
    await user.clear(nameInput)
    await user.type(nameInput, '正确名称')
    await user.type(within(dialog).getByRole('textbox', { name: '更正说明（可选）' }), '入库时漏填')
    await user.click(within(dialog).getByRole('button', { name: '保存更正' }))

    await waitFor(() => expect(apiMocks.correctContainerProduct).toHaveBeenCalledWith(81, expect.objectContaining({
      product_name: '正确名称',
      specification: '362',
      material: 'NBR',
      reason: '入库时漏填',
    })))
  }, 30_000)

  it('warns when manual identity already exists and offers its recorded product data and weight', async () => {
    apiMocks.products.mockResolvedValue([{
      id: 28, product_code: '350', product_name: '350', specification: '350', material: 'N7006A',
      unit_weight_g: '10.85500', effective_unit_weight_g: '10.85500', is_active: true,
    }])
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><App><InventoryPage /></App></QueryClientProvider>)
    await user.click(await screen.findByRole('button', { name: /直接入库/ }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByRole('textbox', { name: '产品编号' }), '350')
    await user.type(within(dialog).getByRole('textbox', { name: '规格' }), '350')
    const material = within(dialog).getByRole('textbox', { name: '材质' })
    await user.type(material, 'N7200')
    expect(within(dialog).queryByText(/已找到已有产品/)).not.toBeInTheDocument()
    await user.clear(material)
    await user.type(material, 'N7006A')
    expect(await within(dialog).findByText('已找到已有产品：350 · N7006A')).toBeInTheDocument()
    expect(within(dialog).getByText(/本次入库会复用已有产品资料/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: '带入已有产品资料和单重' }))
    expect(within(dialog).queryByText(/已找到已有产品/)).not.toBeInTheDocument()
    expect(within(dialog).getByRole('textbox', { name: '产品名称' })).toHaveValue('350')
    expect(within(dialog).getByRole('textbox', { name: '规格' })).toHaveAttribute('readonly')
    expect(Number((within(dialog).getByRole('spinbutton', { name: '成品单重(g)' }) as HTMLInputElement).value)).toBe(10.855)
  }, 30_000)

  it('still permits manual repeat receipt without requiring the operator to select the existing product', async () => {
    apiMocks.products.mockResolvedValue([{
      id: 28, product_code: '350', product_name: '350', specification: '350', material: 'N7006A',
      unit_weight_g: '10.85500', effective_unit_weight_g: '10.85500', is_active: true,
    }])
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><App><InventoryPage /></App></QueryClientProvider>)
    await user.click(await screen.findByRole('button', { name: '查看' }))
    await user.click(await screen.findByRole('button', { name: /在此库位入库/ }))
    const dialog = await screen.findByRole('dialog')
    for (const label of ['产品编号', '产品名称', '规格']) {
      await user.click(within(dialog).getByRole('textbox', { name: label }))
      await user.paste('350')
    }
    await user.type(within(dialog).getByRole('textbox', { name: '材质' }), 'N7006A')
    await user.type(within(dialog).getByRole('spinbutton', { name: '成品单重(g)' }), '10.855')
    await user.type(within(dialog).getByRole('spinbutton', { name: '总数量（件）' }), '78')
    await user.click(within(dialog).getByRole('button', { name: '确认入库' }))
    await waitFor(() => expect(apiMocks.receipt).toHaveBeenCalled())
    expect(apiMocks.receipt.mock.calls[0][0]).toMatchObject({
      product_id: undefined, product: expect.objectContaining({ product_code: '350', specification: '350', material: 'N7006A' }),
      unit_weight_g: 10.855, quantity: 78, location_id: 150,
    })
  }, 30_000)

  it('requires weight-change confirmation for a matching manually entered product too', async () => {
    apiMocks.products.mockResolvedValue([{
      id: 28, product_code: '350', product_name: '350', specification: '350', material: 'N7006A',
      unit_weight_g: '10.85500', effective_unit_weight_g: '10.85500', is_active: true,
    }])
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><App><InventoryPage /></App></QueryClientProvider>)
    await user.click(await screen.findByRole('button', { name: '查看' }))
    await user.click(await screen.findByRole('button', { name: /在此库位入库/ }))
    const dialog = await screen.findByRole('dialog')
    for (const [label, value] of [['产品编号', '350'], ['规格', '350'], ['材质', 'N7006A']]) {
      await user.click(within(dialog).getByRole('textbox', { name: label }))
      await user.paste(value)
    }
    await user.type(within(dialog).getByRole('spinbutton', { name: '成品单重(g)' }), '11')
    await user.type(within(dialog).getByRole('spinbutton', { name: '总数量（件）' }), '78')
    expect(await within(dialog).findByText('已找到已有产品：350 · N7006A')).toBeInTheDocument()
    expect(Number((within(dialog).getByRole('spinbutton', { name: '成品单重(g)' }) as HTMLInputElement).value)).toBe(11)
    await user.click(within(dialog).getByRole('button', { name: '确认入库' }))
    expect((await screen.findAllByText('确认更新产品默认单重')).length).toBeGreaterThan(0)
    expect(apiMocks.receipt).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '返回检查' }))
    expect(apiMocks.receipt).not.toHaveBeenCalled()
  }, 30_000)

  function seedStock(qualityStatus = 'WAITING', inspectorId: number | null = null) {
    const container = {
      id: 81, container_code: 'CT-081', container_type: 'BAG', batch_no: 'INV-081',
      product_id: 362, product_code: 'P-362', product_name: '库存产品', specification: '362',
      material: 'NBR', quantity: 1000, quality_status: qualityStatus, bag_count: 1,
      inspector_id: inspectorId, inspector_name: inspectorId ? '品检员甲' : '',
      inspected_on: inspectorId ? '2026-10-21' : null,
    }
    apiMocks.locations.mockResolvedValue([{ ...lastLocation, container }])
    apiMocks.containers.mockResolvedValue([{
      id: 81, container_code: 'CT-081', quantity: 1000, status: 'ACTIVE',
      batch: { quality_status: qualityStatus, product: { specification: '362' } },
    }])
    return container
  }

  async function findLocationDrawer() {
    const title = await screen.findByText('库位详情 · K09-L05-P02')
    const drawer = title.closest('[role="dialog"]') as HTMLElement
    await waitFor(() => expect(drawer).toBeVisible())
    return drawer
  }

  async function findModal(title: string) {
    const heading = await screen.findByText(title, { selector: '.ant-modal-title' })
    const dialog = heading.closest('[role="dialog"]') as HTMLElement
    await waitFor(() => expect(dialog).toBeVisible())
    return dialog
  }

  it.each([
    { title: '库存直接入库', openName: /直接入库/, saveName: '确认入库' },
    { title: '库存出库', openName: /库存出库/, saveName: '确认出库' },
  ])('keeps an incomplete $title form open without sending a request or an unhandled rejection', async ({ title, openName, saveName }) => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><App><InventoryPage /></App></QueryClientProvider>)
    await user.click(await screen.findByRole('button', { name: openName }))
    const dialog = await findModal(title)
    await user.click(within(dialog).getByRole('button', { name: saveName }))
    await waitFor(() => expect(dialog.querySelector('.ant-form-item-explain-error')).toBeVisible())
    expect(dialog).toBeVisible()
    expect(apiMocks.receipt).not.toHaveBeenCalled()
    expect(apiMocks.outbound).not.toHaveBeenCalled()
  }, 30_000)

  it('offers quality correction and movement directly in the location drawer and allows HOLD without an inspector or date', async () => {
    const stock = seedStock()
    apiMocks.setQuality.mockImplementation(async () => {
      apiMocks.locations.mockResolvedValue([{ ...lastLocation, container: { ...stock, quality_status: 'HOLD' } }])
      return {}
    })
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><App><InventoryPage initialLocationCode="K09-L05-P02" /></App></QueryClientProvider>)
    const drawer = await findLocationDrawer()
    expect(within(drawer).getByRole('button', { name: /移库$/ })).toBeVisible()
    expect(within(drawer).queryByRole('button', { name: '办理出库' })).not.toBeInTheDocument()
    await user.click(within(drawer).getByRole('button', { name: '更新质量状态' }))
    const form = await findModal('更新库存质量状态 · CT-081')
    await user.click(within(form).getByRole('combobox', { name: '质量状态' }))
    await user.click(await screen.findByText('冻结', { selector: '.ant-select-item-option-content' }))
    await user.click(within(form).getByRole('button', { name: '保存状态' }))
    await waitFor(() => expect(apiMocks.setQuality).toHaveBeenCalledWith(81, { quality_status: 'HOLD', inspector_id: null, inspected_on: null }))
    await waitFor(() => expect(form).not.toBeVisible())
    expect(within(drawer).getByText('冻结')).toBeVisible()
    expect(within(drawer).getByText('1000 件')).toBeVisible()
  }, 30_000)

  it('requires an inspector for PASSED and refreshes the open drawer before offering outbound with the selected container', async () => {
    const stock = seedStock()
    apiMocks.listEmployees.mockResolvedValue([{ id: 9, employee_no: 'QC-009', name: '品检员甲', is_active: true }])
    apiMocks.setQuality.mockImplementation(async () => {
      apiMocks.locations.mockResolvedValue([{ ...lastLocation, container: { ...stock, quality_status: 'PASSED', inspector_id: 9, inspector_name: '品检员甲' } }])
      apiMocks.containers.mockResolvedValue([{ id: 81, container_code: 'CT-081', quantity: 1000, status: 'ACTIVE', batch: { quality_status: 'PASSED', product: { specification: '362' } } }])
      return {}
    })
    apiMocks.outbound.mockImplementation(async () => {
      apiMocks.locations.mockResolvedValue([lastLocation])
      apiMocks.containers.mockResolvedValue([])
      return {}
    })
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><App><InventoryPage initialLocationCode="K09-L05-P02" /></App></QueryClientProvider>)
    const drawer = await findLocationDrawer()
    await user.click(within(drawer).getByRole('button', { name: '更新质量状态' }))
    const form = await findModal('更新库存质量状态 · CT-081')
    await user.click(within(form).getByRole('combobox', { name: '质量状态' }))
    await user.click(await screen.findByText('已检合格', { selector: '.ant-select-item-option-content' }))
    await user.click(within(form).getByRole('button', { name: '保存状态' }))
    expect(await within(form).findByText('已检合格必须选择品检员')).toBeVisible()
    expect(apiMocks.setQuality).not.toHaveBeenCalled()
    await user.click(within(form).getByRole('combobox', { name: '品检员' }))
    await user.click(await screen.findByText('QC-009 · 品检员甲', { selector: '.ant-select-item-option-content' }))
    await user.click(within(form).getByRole('button', { name: '保存状态' }))
    await waitFor(() => expect(apiMocks.setQuality).toHaveBeenCalledWith(81, expect.objectContaining({ quality_status: 'PASSED', inspector_id: 9 })))
    await user.click(await within(drawer).findByRole('button', { name: '办理出库' }))
    const outbound = await findModal('库存出库')
    expect(within(outbound).getByText('CT-081 · 362 · 可出 1000')).toBeVisible()
    await user.type(within(outbound).getByRole('spinbutton', { name: '出库数量（件）' }), '1000')
    await user.click(within(outbound).getByRole('button', { name: '确认出库' }))
    await waitFor(() => expect(apiMocks.outbound).toHaveBeenCalled())
    expect(apiMocks.outbound.mock.calls[0][0]).toMatchObject({ lines: [{ container_id: 81, quantity: 1000 }] })
    expect(await within(drawer).findByText('当前为空库位')).toBeVisible()
  }, 45_000)

  it('retains the recorded inspector and inspection date rather than forcing a second selection', async () => {
    seedStock('PASSED', 9)
    apiMocks.listEmployees.mockResolvedValue([{ id: 9, employee_no: 'QC-009', name: '品检员甲', is_active: true }])
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><App><InventoryPage initialLocationCode="K09-L05-P02" /></App></QueryClientProvider>)
    const drawer = await findLocationDrawer()
    await user.click(within(drawer).getByRole('button', { name: '更新质量状态' }))
    const form = await findModal('更新库存质量状态 · CT-081')
    expect(await within(form).findByText('QC-009 · 品检员甲')).toBeVisible()
    expect(within(form).getByLabelText('检验日期（可选）')).toHaveValue('2026-10-21')
    await user.click(within(form).getByRole('button', { name: '保存状态' }))
    await waitFor(() => expect(apiMocks.setQuality).toHaveBeenCalledWith(81, { quality_status: 'PASSED', inspector_id: 9, inspected_on: '2026-10-21' }))
  }, 30_000)

  it('retains the quality form and original displayed status if saving fails', async () => {
    seedStock()
    apiMocks.setQuality.mockRejectedValue(new Error('网络中断，请重试'))
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><App><InventoryPage initialLocationCode="K09-L05-P02" /></App></QueryClientProvider>)
    const drawer = await findLocationDrawer()
    await user.click(within(drawer).getByRole('button', { name: '更新质量状态' }))
    const form = await findModal('更新库存质量状态 · CT-081')
    await user.click(within(form).getByRole('combobox', { name: '质量状态' }))
    await user.click(await screen.findByText('冻结', { selector: '.ant-select-item-option-content' }))
    await user.click(within(form).getByRole('button', { name: '保存状态' }))
    expect(await screen.findByText('网络中断，请重试')).toBeVisible()
    expect(form).toBeVisible()
    expect(within(form).getByText('冻结')).toBeVisible()
    expect(within(drawer).getByText('待检')).toBeVisible()
    expect(within(drawer).queryByRole('button', { name: '办理出库' })).not.toBeInTheDocument()
  }, 30_000)

  it('searches container codes in both the shelf map and the actionable table', async () => {
    const stock = seedStock()
    apiMocks.locations.mockResolvedValue([{ ...lastLocation, container: stock }, { ...lastLocation, id: 149, code: 'K09-L05-P01' }])
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><App><InventoryPage /></App></QueryClientProvider>)
    await user.type(await screen.findByPlaceholderText('搜索位置、产品、规格、材质或容器编号'), 'CT-081')
    expect(screen.getAllByText('K09-L05-P02').length).toBeGreaterThan(0)
    expect(screen.queryByText('K09-L05-P01')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '改质量状态' })).toBeVisible()
  }, 30_000)

  it('does not mistake a failed location request for an empty warehouse requiring initialization', async () => {
    apiMocks.locations.mockRejectedValueOnce(new Error('读取失败'))
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><App><InventoryPage /></App></QueryClientProvider>)
    expect(await screen.findByText('库位读取失败，不能据此判断为空库位')).toBeVisible()
    expect(screen.queryByRole('button', { name: '初始化 K01-K09 库位' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '重新读取' }))
    expect((await screen.findAllByText('K09-L05-P02')).length).toBeGreaterThan(0)
  })
})
