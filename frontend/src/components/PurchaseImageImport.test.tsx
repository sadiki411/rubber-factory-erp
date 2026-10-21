import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { BusinessImportPreview, PurchaseImageRecognition } from '../types'
import { BusinessImportDrawer } from './BusinessImportDrawer'
import { PurchaseImageImport } from './PurchaseImageImport'

const api = vi.hoisted(() => ({ recognize: vi.fn(), preview: vi.fn(), commit: vi.fn() }))
vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
    businessImportApi: { ...actual.businessImportApi, commit: api.commit },
    purchaseImageImportApi: { recognize: api.recognize, preview: api.preview },
  }
})

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({ matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })),
})
class ResizeObserverMock { observe() {} unobserve() {} disconnect() {} }
globalThis.ResizeObserver = ResizeObserverMock

const MB = 1024 * 1024
function image(name = 'purchase.png', size = 100, type = name.endsWith('.png') ? 'image/png' : 'image/jpeg') {
  const file = new File(['image'], name, { type })
  Object.defineProperty(file, 'size', { value: size })
  return file
}

function recognition(): PurchaseImageRecognition {
  return {
    token: 'recognition-token', customer: '艾斯霖', requires_human_confirmation: true,
    documents: [{
      document_id: 'document-1', file_name: 'purchase.png', archive_name: '01.png', sha256: 'server-image-sha256', order_no: 'TEST-PO-001', order_date: '2026-10-20',
      rows: [{ item_no: '1', product_name: '测试品名', specification: 'TEST-SPEC', material: 'TEST-MAT', order_quantity: 100, unit_price: '1.25', amount: '125.00', due_date: '2026-11-01' }],
      warnings: ['交期需核对原图'], raw_text: '测试识别原文',
    }],
  }
}

function preview(overrides: Partial<BusinessImportPreview> = {}): BusinessImportPreview {
  return {
    token: 'preview-token', total_rows: 1, error_count: 0, warning_count: 0,
    counts: { product_specifications: 0, orders: 1, material_receipts: 0, inspection_criteria: 0 },
    rows: [{ row_key: 'row-1', record_type: 'ORDER', action: 'UPDATE', valid: true, order_no: 'TEST-PO-001', item_no: '1', changes: { order_quantity: { from: 50, to: 100 }, due_date: { from: '2026-10-25', to: '2026-11-01' } } }],
    issues: [], ...overrides,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

function setup(context?: 'orders' | 'product-specifications') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  const busy = vi.fn()
  const result = render(<QueryClientProvider client={client}><App>{context
    ? <BusinessImportDrawer open context={context} onClose={vi.fn()} />
    : <PurchaseImageImport onBusyChange={busy} />}</App></QueryClientProvider>)
  return { ...result, invalidate, busy, user: userEvent.setup({ applyAccept: false }) }
}

async function recognizeDraft(user: ReturnType<typeof userEvent.setup>, files = [image()]) {
  await user.upload(screen.getByLabelText('采购单图片'), files)
  await user.click(screen.getByRole('button', { name: '识别采购单图片' }))
  await screen.findByDisplayValue('TEST-PO-001')
}

async function precheck(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('checkbox', { name: '我已对照原图核对所有采购单字段' }))
  await user.click(screen.getByRole('button', { name: '确认核对并预检' }))
  return screen.findByRole('region', { name: '采购单预检结果' })
}

beforeEach(() => {
  api.recognize.mockReset().mockResolvedValue(recognition())
  api.preview.mockReset().mockResolvedValue(preview())
  api.commit.mockReset().mockResolvedValue({ created: { orders: 1 }, updated: { orders: 2 }, skipped: { orders: 3 } })
  let urlId = 0
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, writable: true, value: vi.fn(() => `blob:purchase-${++urlId}`) })
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, writable: true, value: vi.fn() })
})

describe('PurchaseImageImport', () => {
  it('recognizes multiple images, requires confirmation, sends edited date strings and commits the preview token', async () => {
    const { user, invalidate } = setup()
    const files = [image(), image('second.JPG', 100, 'image/jpeg')]
    await recognizeDraft(user, files)
    expect(api.recognize).toHaveBeenCalledWith(files)
    expect(screen.getByRole('link', { name: '查看原图：purchase.png' })).toHaveAttribute('href', 'blob:purchase-1')
    expect(screen.getByRole('link', { name: '查看原图：second.JPG' })).toHaveAttribute('href', 'blob:purchase-2')
    expect(screen.getByText('交期需核对原图')).toBeInTheDocument()
    await user.click(screen.getByText('查看识别原文'))
    expect(screen.getByText('测试识别原文')).toBeVisible()
    expect(screen.getByRole('button', { name: '确认核对并预检' })).toBeDisabled()
    expect(api.preview).not.toHaveBeenCalled()
    expect(api.commit).not.toHaveBeenCalled()

    const date = screen.getByLabelText('采购单 1 明细 1 交期')
    expect(date).toHaveAttribute('type', 'date')
    expect(screen.getByLabelText('采购单 1 采购日期')).toHaveAttribute('type', 'date')
    fireEvent.change(date, { target: { value: '2026-11-03' } })
    fireEvent.change(screen.getByLabelText('采购单 1 明细 1 数量'), { target: { value: '120' } })
    await precheck(user)
    expect(api.preview).toHaveBeenCalledWith('recognition-token', [expect.objectContaining({
      document_id: 'document-1', archive_name: '01.png', sha256: 'server-image-sha256', order_date: '2026-10-20', rows: [expect.objectContaining({ order_quantity: '120', due_date: '2026-11-03' })],
    })], true)
    expect(screen.getByText('更新')).toBeInTheDocument()
    expect(screen.getByText('50 → 100')).toBeInTheDocument()
    expect(screen.getByText('2026-10-25 → 2026-11-01')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '确认整批导入采购单' }))
    await waitFor(() => expect(api.commit).toHaveBeenCalledWith('preview-token'))
    expect(await screen.findByText('业务数据导入完成：新增 1 条，更新 2 条，跳过 3 条')).toBeInTheDocument()
    expect(invalidate.mock.calls.some((args) => args.length === 0)).toBe(true)
    expect(screen.queryByDisplayValue('TEST-PO-001')).not.toBeInTheDocument()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:purchase-1')
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:purchase-2')
  })

  it.each([
    ['最多上传 10 张图片', () => Array.from({ length: 11 }, (_, index) => image(`${index}.png`))],
    ['单张图片不能超过 10MB', () => [image('large.png', 10 * MB + 1)]],
    ['图片总大小不能超过 20MB', () => [image('one.png', 10 * MB), image('two.jpg', 10 * MB), image('three.png', 1)]],
    ['仅支持 PNG / JPG 图片', () => [image('document.pdf', 10, 'application/pdf')]],
    ['仅支持 PNG / JPG 图片', () => [image('fake.png', 10, 'image/gif')]],
    ['不能上传空图片', () => [image('empty.png', 0)]],
  ])('rejects invalid selections: %s', async (error, files) => {
    const { user } = setup()
    await user.upload(screen.getByLabelText('采购单图片'), files())
    expect(screen.getByText(error)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '识别采购单图片' })).toBeDisabled()
    expect(api.recognize).not.toHaveBeenCalled()
    expect(URL.createObjectURL).not.toHaveBeenCalled()
  })

  it('accepts exactly ten images totaling 20MB and enforces totals across selections', async () => {
    const { user } = setup()
    const files = Array.from({ length: 10 }, (_, index) => image(`${index}.png`, 2 * MB))
    await user.upload(screen.getByLabelText('采购单图片'), files.slice(0, 5))
    await user.upload(screen.getByLabelText('采购单图片'), files.slice(5))
    expect(screen.getAllByRole('link', { name: /查看原图/ })).toHaveLength(10)
    await user.upload(screen.getByLabelText('采购单图片'), image('extra.png'))
    expect(screen.getByText('最多上传 10 张图片')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '识别采购单图片' }))
    await waitFor(() => expect(api.recognize).toHaveBeenCalledWith(files))
  })

  it('accepts the single-image 10MB boundary', async () => {
    const { user } = setup()
    await recognizeDraft(user, [image('purchase.png', 10 * MB)])
    expect(api.recognize).toHaveBeenCalledTimes(1)
  })

  it('edits each recognized document independently and retains every document identity', async () => {
    const result = recognition()
    result.documents.push({ ...recognition().documents[0], document_id: 'document-2', file_name: 'second.jpg', order_no: 'TEST-PO-002' })
    api.recognize.mockResolvedValue(result)
    const { user } = setup()
    await recognizeDraft(user, [image(), image('second.jpg')])
    fireEvent.change(screen.getByLabelText('采购单 2 明细 1 材质'), { target: { value: 'SECOND-MATERIAL' } })
    fireEvent.change(screen.getByLabelText('采购单 2 采购日期'), { target: { value: '2026-10-21' } })
    expect(screen.getByLabelText('采购单 1 明细 1 材质')).toHaveValue('TEST-MAT')
    await precheck(user)
    expect(api.preview).toHaveBeenCalledWith('recognition-token', [
      expect.objectContaining({ document_id: 'document-1', order_date: '2026-10-20' }),
      expect.objectContaining({ document_id: 'document-2', order_date: '2026-10-21', rows: [expect.objectContaining({ material: 'SECOND-MATERIAL' })] }),
    ], true)
    await user.click(screen.getByRole('checkbox'))
    expect(screen.queryByRole('region', { name: '采购单预检结果' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '确认核对并预检' })).toBeDisabled()
  })

  it('validates required fields and numeric values without inventing missing data', async () => {
    const result = recognition()
    result.documents[0].order_no = ''
    result.documents[0].rows[0] = { ...result.documents[0].rows[0], order_quantity: 0, due_date: null, unit_price: -1 }
    api.recognize.mockResolvedValue(result)
    const { user } = setup()
    await user.upload(screen.getByLabelText('采购单图片'), image())
    await user.click(screen.getByRole('button', { name: '识别采购单图片' }))
    expect(await screen.findByText('采购单 1：采购单号必填')).toBeInTheDocument()
    expect(screen.getByText('采购单 1 / 明细 1：数量必须为正整数')).toBeInTheDocument()
    expect(screen.getByText('采购单 1 / 明细 1：交期必填')).toBeInTheDocument()
    expect(screen.getByText('采购单 1 / 明细 1：单价必须为非负数')).toBeInTheDocument()
    expect(screen.getByLabelText('采购单 1 明细 1 交期')).toHaveValue('')
    await user.click(screen.getByRole('checkbox'))
    expect(screen.getByRole('button', { name: '确认核对并预检' })).toBeDisabled()
    expect(api.preview).not.toHaveBeenCalled()
  })

  it('shows conflict details and changes, and invalidates preview and confirmation on any edit', async () => {
    api.preview.mockResolvedValue(preview({
      error_count: 1, issues: [{ level: 'error', field: 'order_no', message: '采购单号与现有订单冲突' }],
      rows: [{ ...preview().rows[0], valid: false }],
    }))
    const { user } = setup()
    await recognizeDraft(user)
    const region = await precheck(user)
    expect(within(region).getByText(/采购单号：TEST-PO-001/)).toBeInTheDocument()
    expect(within(region).getByText('数量：100 · 交期：2026-11-01')).toBeInTheDocument()
    expect(within(region).getByText(/采购单号与现有订单冲突/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '确认整批导入采购单' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('采购单 1 明细 1 产品名称'), { target: { value: '修正名称' } })
    expect(screen.queryByRole('region', { name: '采购单预检结果' })).not.toBeInTheDocument()
    expect(screen.getByRole('checkbox')).not.toBeChecked()
    expect(api.commit).not.toHaveBeenCalled()
  })

  it('also blocks commit on invalid rows or error issues even if error_count is zero', async () => {
    api.preview.mockResolvedValue(preview({ rows: [{ ...preview().rows[0], valid: false }] }))
    const { user } = setup()
    await recognizeDraft(user)
    await precheck(user)
    expect(screen.getByRole('button', { name: '确认整批导入采购单' })).toBeDisabled()
  })

  it('requires an explicit candidate choice and binds every matching draft row across documents', async () => {
    const result = recognition()
    const matching = { ...result.documents[0].rows[0], specification: 'X115-002', material: 'EP7521' }
    result.documents[0].rows = [matching, { ...matching, item_no: '2', material: 'OTHER-MATERIAL' }]
    result.documents.push({ ...recognition().documents[0], document_id: 'document-2', file_name: 'second.jpg', order_no: 'TEST-PO-002', rows: [{ ...matching, specification: ' x115-002 ', material: 'ep7521' }] })
    api.recognize.mockResolvedValue(result)
    api.preview.mockResolvedValueOnce(preview({
      error_count: 1, issues: [{ level: 'error', message: '匹配到多个产品档案，请人工选择' }],
      rows: [{
        row_key: 'product-conflict', record_type: 'PRODUCT_SPECIFICATION', specification: 'x115-002', material: 'ep7521', valid: false,
        product_candidates: [
          { id: 67, product_name: '三角垫', specification: 'x115-002', material: 'ep7521', mold_no: 'X115-002' },
          { id: 68, product_name: 'x115-002', specification: 'x115-002', material: 'ep7521', mold_no: '' },
        ],
      }],
    }))
    const { user } = setup()
    await recognizeDraft(user, [image(), image('second.jpg')])
    await precheck(user)
    const select = screen.getByRole('combobox', { name: '关联产品档案 x115-002 / ep7521' })
    expect(select).toHaveValue('')
    expect(screen.queryByText(/已关联产品档案 ID/)).not.toBeInTheDocument()
    expect(api.preview.mock.calls[0][1].flatMap((document: PurchaseImageRecognition['documents'][number]) => document.rows)
      .every((row: PurchaseImageRecognition['documents'][number]['rows'][number]) => row.product_specification_id == null)).toBe(true)
    await user.click(select)
    expect(await screen.findByRole('option', { name: 'ID 67 · 三角垫 · x115-002 / ep7521 · 模具：X115-002' })).toBeInTheDocument()
    await user.click(screen.getByRole('option', { name: 'ID 68 · x115-002 · x115-002 / ep7521 · 模具：未关联' }))
    expect(screen.getAllByText('已关联产品档案 ID：68')).toHaveLength(2)
    expect(screen.queryByRole('region', { name: '采购单预检结果' })).not.toBeInTheDocument()
    expect(screen.getByRole('checkbox')).not.toBeChecked()
    expect(screen.getByRole('button', { name: '确认核对并预检' })).toBeDisabled()
    expect(api.preview).toHaveBeenCalledTimes(1)
    expect(api.commit).not.toHaveBeenCalled()
    await precheck(user)
    const documents = api.preview.mock.calls[1][1] as PurchaseImageRecognition['documents']
    expect(documents[0]).toMatchObject({ document_id: 'document-1', archive_name: '01.png', sha256: 'server-image-sha256' })
    expect(documents[0].rows[0].product_specification_id).toBe(68)
    expect(documents[0].rows[1].product_specification_id).toBeUndefined()
    expect(documents[1].rows[0].product_specification_id).toBe(68)
  })

  it('allows an existing explicit binding to be cleared before another confirmed preview', async () => {
    const result = recognition()
    result.documents[0].rows[0].product_specification_id = 67
    api.recognize.mockResolvedValue(result)
    const { user } = setup()
    await recognizeDraft(user)
    expect(screen.getByText('已关联产品档案 ID：67')).toBeInTheDocument()
    await precheck(user)
    await user.click(screen.getByRole('button', { name: '采购单 1 明细 1 清除关联产品' }))
    expect(screen.queryByText(/已关联产品档案 ID/)).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '采购单预检结果' })).not.toBeInTheDocument()
    expect(screen.getByRole('checkbox')).not.toBeChecked()
    await precheck(user)
    expect(api.preview.mock.calls[1][1][0].rows[0].product_specification_id).toBeNull()
  })

  it.each(['规格', '材质'])('clears stale product binding when %s is edited', async (label) => {
    const result = recognition()
    result.documents[0].rows[0].product_specification_id = 67
    api.recognize.mockResolvedValue(result)
    const { user } = setup()
    await recognizeDraft(user)
    await precheck(user)
    fireEvent.change(screen.getByLabelText(`采购单 1 明细 1 ${label}`), { target: { value: 'CHANGED' } })
    expect(screen.queryByText(/已关联产品档案 ID/)).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '采购单预检结果' })).not.toBeInTheDocument()
    expect(screen.getByRole('checkbox')).not.toBeChecked()
    await precheck(user)
    expect(api.preview.mock.calls[1][1][0].rows[0].product_specification_id).toBeNull()
  })

  it('keeps the draft editable when preview fails, and permits a corrected retry', async () => {
    api.preview.mockRejectedValueOnce(new Error('交期冲突，请修正'))
    const { user } = setup()
    await recognizeDraft(user)
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: '确认核对并预检' }))
    expect(await screen.findByText('交期冲突，请修正')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('采购单 1 明细 1 交期'), { target: { value: '2026-11-04' } })
    await precheck(user)
    expect(api.preview).toHaveBeenCalledTimes(2)
  })

  it('supports repairing a server-owned draft returned with an OCR error', async () => {
    const result = recognition()
    result.documents[0].rows = []
    api.recognize.mockRejectedValue(Object.assign(new Error('部分内容无法识别'), { data: result }))
    const { user } = setup()
    await recognizeDraft(user)
    expect(screen.getByText(/部分内容无法识别/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '添加空白明细（手工核对填写）' }))
    expect(screen.getByLabelText('采购单 1 明细 1 规格')).toHaveValue('')
    for (const [label, value] of [['项次', '2'], ['规格', 'MANUAL-SPEC'], ['材质', 'MANUAL-MAT'], ['数量', '20'], ['交期', '2026-11-05']]) {
      fireEvent.change(screen.getByLabelText(`采购单 1 明细 1 ${label}`), { target: { value } })
    }
    await precheck(user)
    expect(api.preview).toHaveBeenCalledWith('recognition-token', [expect.objectContaining({ rows: [expect.objectContaining({ item_no: '2', specification: 'MANUAL-SPEC', due_date: '2026-11-05', unit_price: null, amount: null })] })], true)
    expect(screen.getByRole('button', { name: '采购单 1 删除明细 1' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '添加空白明细（手工核对填写）' }))
    expect(screen.queryByRole('region', { name: '采购单预检结果' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '采购单 1 删除明细 2' }))
    expect(screen.getByLabelText('采购单 1 明细 1 规格')).toHaveValue('MANUAL-SPEC')
    expect(screen.getByRole('button', { name: '采购单 1 删除明细 1' })).toBeDisabled()
  })

  it('limits each image to 100 rows while allowing an erroneous row to be removed', async () => {
    const result = recognition()
    result.documents[0].rows = Array.from({ length: 100 }, (_, index) => ({ ...recognition().documents[0].rows[0], item_no: String(index + 1) }))
    api.recognize.mockResolvedValue(result)
    const { user } = setup()
    await recognizeDraft(user)
    expect(screen.getByText('明细 100 / 100 条；每张图片至少保留 1 条明细。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '添加空白明细（手工核对填写）' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '采购单 1 删除明细 100' }))
    expect(screen.getByText('明细 99 / 100 条；每张图片至少保留 1 条明细。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '添加空白明细（手工核对填写）' })).toBeEnabled()
    expect(screen.getByLabelText('采购单 1 明细 99 项次')).toHaveValue('99')
  }, 30_000)

  it('does not fabricate a draft or token when recognition fails completely', async () => {
    api.recognize.mockRejectedValue(new Error('识别服务暂不可用'))
    const { user } = setup()
    await user.upload(screen.getByLabelText('采购单图片'), image())
    await user.click(screen.getByRole('button', { name: '识别采购单图片' }))
    expect(await screen.findByText(/识别服务暂不可用/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '查看原图：purchase.png' })).toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(api.preview).not.toHaveBeenCalled()
  })

  it('serializes server previews and ignores older results after an edit', async () => {
    const old = deferred<BusinessImportPreview>()
    api.preview.mockReturnValueOnce(old.promise).mockResolvedValueOnce(preview({ token: 'new-preview' }))
    const { user } = setup()
    await recognizeDraft(user)
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: '确认核对并预检' }))
    fireEvent.change(screen.getByLabelText('采购单 1 明细 1 金额'), { target: { value: '126' } })
    expect(screen.getByRole('checkbox')).not.toBeChecked()
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: '确认核对并预检' }))
    expect(api.preview).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('region', { name: '采购单预检结果' })).not.toBeInTheDocument()
    await act(async () => old.resolve(preview({ token: 'stale-preview', error_count: 9 })))
    await screen.findByRole('region', { name: '采购单预检结果' })
    expect(api.preview).toHaveBeenCalledTimes(2)
    expect(screen.queryByText(/错误 9/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '确认整批导入采购单' }))
    await waitFor(() => expect(api.commit).toHaveBeenCalledWith('new-preview'))
  })

  it('ignores an older recognition after a new file selection, and releases removed URLs', async () => {
    const old = deferred<PurchaseImageRecognition>()
    api.recognize.mockReturnValueOnce(old.promise).mockResolvedValueOnce(recognition())
    const { user } = setup()
    await user.upload(screen.getByLabelText('采购单图片'), image('old.png'))
    await user.click(screen.getByRole('button', { name: '识别采购单图片' }))
    await user.click(screen.getByRole('button', { name: '移除图片 1' }))
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:purchase-1')
    await recognizeDraft(user)
    const stale = recognition()
    stale.documents[0].order_no = 'STALE-ORDER'
    await act(async () => old.resolve(stale))
    expect(screen.queryByDisplayValue('STALE-ORDER')).not.toBeInTheDocument()
    expect(screen.getByDisplayValue('TEST-PO-001')).toBeInTheDocument()
  })

  it('clears the previous draft and preview immediately when starting recognition again', async () => {
    const pending = deferred<PurchaseImageRecognition>()
    const { user } = setup()
    await recognizeDraft(user)
    await precheck(user)
    api.recognize.mockReturnValueOnce(pending.promise)
    await user.click(screen.getByRole('button', { name: '识别采购单图片' }))
    expect(screen.queryByDisplayValue('TEST-PO-001')).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '采购单预检结果' })).not.toBeInTheDocument()
    await act(async () => pending.reject(new Error('重新识别失败')))
    expect(await screen.findByText(/重新识别失败/)).toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  })

  it('shows the one-minute wait guidance and prevents repeat recognition while pending', async () => {
    const pending = deferred<PurchaseImageRecognition>()
    api.recognize.mockReturnValue(pending.promise)
    const { user } = setup()
    expect(screen.getByText(/识别最多约需 1 分钟，请耐心等待，识别中不要重复点击或重试/)).toBeInTheDocument()
    await user.upload(screen.getByLabelText('采购单图片'), image())
    const button = screen.getByRole('button', { name: '识别采购单图片' })
    await user.click(button)
    expect(screen.getByText('正在识别采购单图片，最多约需 1 分钟')).toBeInTheDocument()
    expect(screen.getByText(/完成后会显示可编辑草稿；数量、材质等未识别字段需对照原图手工补全/)).toBeInTheDocument()
    expect(button).toBeDisabled()
    fireEvent.click(button)
    expect(api.recognize).toHaveBeenCalledTimes(1)
    await act(async () => pending.resolve(recognition()))
    expect(screen.queryByText('正在识别采购单图片，最多约需 1 分钟')).not.toBeInTheDocument()
  })

  it('releases URLs and ignores recognition after unmount', async () => {
    const pending = deferred<PurchaseImageRecognition>()
    api.recognize.mockReturnValue(pending.promise)
    const { user, unmount, busy } = setup()
    await user.upload(screen.getByLabelText('采购单图片'), image())
    await user.click(screen.getByRole('button', { name: '识别采购单图片' }))
    unmount()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:purchase-1')
    expect(busy).toHaveBeenLastCalledWith(false)
    await act(async () => pending.resolve(recognition()))
    expect(api.preview).not.toHaveBeenCalled()
  })

  it('prevents duplicate commits and edits while committing', async () => {
    const pending = deferred<{ created_count: number }>()
    api.commit.mockReturnValue(pending.promise)
    const { user } = setup()
    await recognizeDraft(user)
    await precheck(user)
    const button = screen.getByRole('button', { name: '确认整批导入采购单' })
    fireEvent.click(button)
    fireEvent.click(button)
    expect(api.commit).toHaveBeenCalledTimes(1)
    expect(screen.getByLabelText('采购单 1 明细 1 数量')).toBeDisabled()
    expect(screen.getByLabelText('采购单图片')).toBeDisabled()
    await act(async () => pending.resolve({ created_count: 1 }))
  })

  it('preserves the corrected draft and allows retry after commit fails', async () => {
    api.commit.mockRejectedValueOnce(new Error('批次提交暂时失败'))
    const { user, invalidate } = setup()
    await recognizeDraft(user)
    await precheck(user)
    await user.click(screen.getByRole('button', { name: '确认整批导入采购单' }))
    expect(await screen.findByText('批次提交暂时失败')).toBeInTheDocument()
    expect(screen.getByLabelText('采购单 1 明细 1 数量')).toBeEnabled()
    expect(screen.getByDisplayValue('TEST-PO-001')).toBeInTheDocument()
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['business-import-history'] })
    await user.click(screen.getByRole('button', { name: '确认整批导入采购单' }))
    await waitFor(() => expect(api.commit).toHaveBeenCalledTimes(2))
  })
})

describe('BusinessImportDrawer purchase image entrance', () => {
  it('mounts image import only when expanded for orders, preserving the original xlsx input', async () => {
    const { user } = setup('orders')
    expect(document.querySelector('input[type="file"]')).toHaveAttribute('accept', '.xlsx')
    expect(screen.queryByLabelText('采购单图片')).not.toBeInTheDocument()
    await user.click(screen.getByText('艾斯霖采购单图片导入（PNG / JPG）'))
    expect(await screen.findByLabelText('采购单图片')).toBeInTheDocument()
  })

  it('does not expose image import in product-specifications context', () => {
    setup('product-specifications')
    expect(screen.queryByText('艾斯霖采购单图片导入（PNG / JPG）')).not.toBeInTheDocument()
    expect(document.querySelector('input[type="file"]')).toHaveAttribute('accept', '.xlsx')
  })
})

describe('purchase image API transport contract', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('uses files[] multipart and confirmed JSON, retaining the existing business commit endpoint', async () => {
    const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
    const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify(recognition()), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetch)
    const files = [image(), image('two.jpg')]
    await actual.purchaseImageImportApi.recognize(files)
    const [path, options] = fetch.mock.calls[0] as unknown as [string, RequestInit]
    expect(path).toBe('/api/orders/imports/purchase-images/recognize/')
    expect(options.method).toBe('POST')
    expect(options.body).toBeInstanceOf(FormData)
    expect((options.body as FormData).getAll('files[]')).toEqual(files)
    expect(new Headers(options.headers).has('Content-Type')).toBe(false)
    expect(options.credentials).toBe('include')

    await actual.purchaseImageImportApi.preview('recognition-token', recognition().documents, true)
    const [previewPath, previewOptions] = fetch.mock.calls[1] as unknown as [string, RequestInit]
    expect(previewPath).toBe('/api/orders/imports/purchase-images/preview/')
    expect(previewOptions.method).toBe('POST')
    expect(JSON.parse(previewOptions.body as string)).toEqual({ token: 'recognition-token', documents: recognition().documents, confirmed: true })
    await actual.businessImportApi.commit('preview-token')
    const [commitPath, commitOptions] = fetch.mock.calls[2] as unknown as [string, RequestInit]
    expect(commitPath).toBe('/api/orders/imports/commit/')
    expect(JSON.parse(commitOptions.body as string)).toEqual({ token: 'preview-token' })
  })
})
