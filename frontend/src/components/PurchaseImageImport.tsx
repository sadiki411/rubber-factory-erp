import { Alert, App, Button, Card, Checkbox, Col, Input, Row, Select, Space, Tag } from 'antd'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { businessImportApi, purchaseImageImportApi } from '../api/client'
import type { BusinessImportCounts, BusinessImportPreview, PurchaseImageDocument, PurchaseImageRecognition, PurchaseImageRow } from '../types'

interface Props {
  disabled?: boolean
  onBusyChange?: (busy: boolean) => void
}

interface LocalImage { file: File; url: string }

const MB = 1024 * 1024
const ROW_FIELDS: { key: keyof PurchaseImageRow; label: string; type?: 'date' | 'number'; required?: boolean }[] = [
  { key: 'item_no', label: '项次', required: true },
  { key: 'product_name', label: '产品名称' },
  { key: 'specification', label: '规格', required: true },
  { key: 'material', label: '材质', required: true },
  { key: 'order_quantity', label: '数量', type: 'number', required: true },
  { key: 'unit_price', label: '单价', type: 'number' },
  { key: 'amount', label: '金额', type: 'number' },
  { key: 'due_date', label: '交期', type: 'date', required: true },
]
const CHANGE_LABELS: Record<string, string> = {
  order_no: '采购单号', order_date: '采购日期', product_specification_id: '关联产品档案 ID',
  ...Object.fromEntries(ROW_FIELDS.map(({ key, label }) => [key, label])),
}

function text(value: unknown): string {
  return value === null || value === undefined || value === '' ? '（空）' : typeof value === 'object' ? JSON.stringify(value) : String(value)
}

function validDate(value: string | null) {
  return !!value && /^\d{4}-\d{2}-\d{2}$/.test(value)
    && !Number.isNaN(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value
}

function validationErrors(documents: PurchaseImageDocument[]) {
  if (!documents.length) return ['没有可预检的采购单，请重新识别。']
  return documents.flatMap((document, documentIndex) => {
    const prefix = `采购单 ${documentIndex + 1}`
    const errors: string[] = []
    if (!document.order_no?.trim()) errors.push(`${prefix}：采购单号必填`)
    if (!validDate(document.order_date)) errors.push(`${prefix}：采购日期必填，格式为 YYYY-MM-DD`)
    if (!document.rows.length) errors.push(`${prefix}：至少需要一条明细`)
    if (document.rows.length > 100) errors.push(`${prefix}：每张图片最多 100 条明细，请删除多余行`)
    document.rows.forEach((row, rowIndex) => {
      ROW_FIELDS.forEach(({ key, label, type, required }) => {
        const value = String(row[key] ?? '').trim()
        let error = ''
        if (required && !value) error = `${label}必填`
        else if (value && type === 'date' && !validDate(value)) error = `${label}必须为 YYYY-MM-DD`
        else if (value && type === 'number' && (!Number.isFinite(Number(value)) || Number(value) < 0
          || (key === 'order_quantity' && (!Number.isInteger(Number(value)) || Number(value) === 0)))) {
          error = key === 'order_quantity' ? '数量必须为正整数' : `${label}必须为非负数`
        }
        if (error) errors.push(`${prefix} / 明细 ${rowIndex + 1}：${error}`)
      })
    })
    return errors
  })
}

function count(counts?: Partial<BusinessImportCounts>) {
  return Object.values(counts || {}).reduce((sum, value) => sum + Number(value || 0), 0)
}

function sameProduct(row: PurchaseImageRow, specification?: string, material?: string) {
  const normalize = (value?: string | null) => value?.trim().toLowerCase() || ''
  return !!normalize(specification) && !!normalize(material)
    && normalize(row.specification) === normalize(specification) && normalize(row.material) === normalize(material)
}

// A failed OCR response may still carry a server-owned token and repairable draft.
// Never invent a token or fill unrecognized business values.
function recoveryDraft(error: unknown): PurchaseImageRecognition | undefined {
  if (!error || typeof error !== 'object' || !('data' in error)) return undefined
  const data = error.data as Partial<PurchaseImageRecognition> | undefined
  if (typeof data?.token !== 'string' || !data.token || !Array.isArray(data.documents)) return undefined
  if (!data.documents.every((document) => typeof document.document_id === 'string' && typeof document.file_name === 'string' && Array.isArray(document.rows))) return undefined
  return { ...data, customer: '艾斯霖', requires_human_confirmation: true } as PurchaseImageRecognition
}

export function PurchaseImageImport({ disabled = false, onBusyChange }: Props) {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [images, setImages] = useState<LocalImage[]>([])
  const [draft, setDraft] = useState<PurchaseImageRecognition>()
  const [preview, setPreview] = useState<BusinessImportPreview>()
  const [confirmed, setConfirmed] = useState(false)
  const [recognizing, setRecognizing] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  const [committing, setCommitting] = useState(false)
  const [error, setError] = useState('')
  const revision = useRef(0)
  const previewQueue = useRef<Promise<unknown>>(Promise.resolve())
  const commitLock = useRef(false)
  const urls = useRef(new Set<string>())
  const busy = recognizing || previewing || committing

  useEffect(() => { onBusyChange?.(busy) }, [busy, onBusyChange])
  useEffect(() => () => { onBusyChange?.(false) }, [onBusyChange])
  useEffect(() => () => {
    revision.current += 1
    urls.current.forEach((url) => URL.revokeObjectURL(url))
    urls.current.clear()
  }, [])

  function invalidate() {
    revision.current += 1
    setPreview(undefined)
    setConfirmed(false)
    setPreviewing(false)
    setRecognizing(false)
    setError('')
  }

  function selectImages(files: File[]) {
    if (disabled || commitLock.current || !files.length) return
    const all = [...images.map(({ file }) => file), ...files]
    let failure = ''
    if (all.length > 10) failure = '最多上传 10 张图片'
    else if (files.some((file) => !/\.(png|jpe?g)$/i.test(file.name) || (file.type && !['image/png', 'image/jpeg'].includes(file.type)))) failure = '仅支持 PNG / JPG 图片'
    else if (files.some((file) => file.size > 10 * MB)) failure = '单张图片不能超过 10MB'
    else if (all.reduce((sum, file) => sum + file.size, 0) > 20 * MB) failure = '图片总大小不能超过 20MB'
    else if (files.some((file) => file.size === 0)) failure = '不能上传空图片'
    if (failure) { setError(failure); return }
    const added = files.map((file) => {
      const url = URL.createObjectURL(file)
      urls.current.add(url)
      return { file, url }
    })
    invalidate()
    setDraft(undefined)
    setImages([...images, ...added])
  }

  function removeImage(index: number) {
    if (disabled || commitLock.current) return
    const removed = images[index]
    URL.revokeObjectURL(removed.url)
    urls.current.delete(removed.url)
    invalidate()
    setDraft(undefined)
    setImages(images.filter((_, imageIndex) => imageIndex !== index))
  }

  async function recognize() {
    if (disabled || commitLock.current || !images.length || recognizing) return
    invalidate()
    const current = revision.current
    setDraft(undefined)
    setRecognizing(true)
    try {
      const result = await purchaseImageImportApi.recognize(images.map(({ file }) => file))
      if (current !== revision.current) return
      setDraft(result)
    } catch (failure) {
      if (current !== revision.current) return
      setDraft(recoveryDraft(failure))
      setError(`${failure instanceof Error ? failure.message : '图片识别失败'}。如已返回草稿，可对照原图修正；未返回草稿时请重新识别。`)
    } finally {
      void queryClient.invalidateQueries({ queryKey: ['business-import-history'] })
      if (current === revision.current) setRecognizing(false)
    }
  }

  function editDocument(index: number, update: (document: PurchaseImageDocument) => PurchaseImageDocument) {
    if (disabled || commitLock.current || !draft) return
    invalidate()
    setDraft({ ...draft, documents: draft.documents.map((document, documentIndex) => documentIndex === index ? update(document) : document) })
  }

  function bindProduct(specification: string | undefined, material: string | undefined, id?: number) {
    if (disabled || commitLock.current || !draft) return
    invalidate()
    setDraft({ ...draft, documents: draft.documents.map((document) => ({
      ...document,
      rows: document.rows.map((row) => sameProduct(row, specification, material) ? { ...row, product_specification_id: id ?? null } : row),
    })) })
  }

  const errors = draft ? validationErrors(draft.documents) : []
  async function runPreview() {
    if (disabled || commitLock.current || !draft || !confirmed || errors.length || previewing) return
    const current = ++revision.current
    setPreview(undefined)
    setError('')
    setPreviewing(true)
    try {
      const documents = draft.documents.map((document) => ({
        ...document,
        rows: document.rows.map((row) => ({ ...row, unit_price: row.unit_price === '' ? null : row.unit_price, amount: row.amount === '' ? null : row.amount })),
      }))
      // Preview updates the same server batch. Serialize requests as well as
      // ignoring stale responses so an older write cannot win on the server.
      const request = previewQueue.current.catch(() => undefined).then(() => current === revision.current
        ? purchaseImageImportApi.preview(draft.token, documents, true) : undefined)
      previewQueue.current = request
      const result = await request
      if (current !== revision.current || !result) return
      setPreview(result)
    } catch (failure) {
      if (current === revision.current) setError(failure instanceof Error ? failure.message : '采购单预检失败，请修正草稿后重试')
    } finally {
      void queryClient.invalidateQueries({ queryKey: ['business-import-history'] })
      if (current === revision.current) setPreviewing(false)
    }
  }

  const canCommit = !!preview && confirmed && preview.error_count === 0 && preview.rows.length > 0
    && preview.rows.every((row) => row.valid !== false) && !preview.issues.some((issue) => issue.level === 'error')

  async function commit() {
    if (disabled || commitLock.current || busy || !canCommit || !preview) return
    commitLock.current = true
    setCommitting(true)
    const current = revision.current
    try {
      const result = await businessImportApi.commit(preview.token)
      // A completed write must refresh every consumer, including inventory caches.
      await queryClient.invalidateQueries()
      if (current !== revision.current) return
      message.success(`业务数据导入完成：新增 ${result.created_count ?? result.imported_count ?? count(result.created || result.imported || result.counts)} 条，更新 ${result.updated_count ?? count(result.updated)} 条，跳过 ${result.skipped_count ?? count(result.skipped)} 条`)
      invalidate()
      setDraft(undefined)
      setImages([])
      urls.current.forEach((url) => URL.revokeObjectURL(url))
      urls.current.clear()
    } catch (failure) {
      void queryClient.invalidateQueries({ queryKey: ['business-import-history'] })
      if (current === revision.current) setError(failure instanceof Error ? failure.message : '采购单导入失败')
    } finally {
      commitLock.current = false
      setCommitting(false)
    }
  }

  const locked = disabled || committing
  return (
    <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
      <Alert type="info" showIcon title="艾斯霖采购单图片导入" description="支持多张 PNG / JPG，最多 10 张、单张 10MB、总计 20MB。识别最多约需 1 分钟，请耐心等待，识别中不要重复点击或重试。识别结果不会直接写入订单；请对照原图核对后预检。标有 * 的字段必填。" />
      <label>
        选择采购单图片
        <input aria-label="采购单图片" type="file" accept=".png,.jpg,.jpeg,image/png,image/jpeg" multiple disabled={locked} style={{ display: 'block', maxWidth: '100%' }} onChange={(event) => {
          selectImages(Array.from(event.target.files || []))
          event.target.value = ''
        }} />
      </label>
      <Space orientation="vertical" style={{ width: '100%' }}>
        {images.map(({ file, url }, index) => (
          <Space key={url} wrap>
            <a href={url} target="_blank" rel="noreferrer">查看原图：{file.name}</a>
            <span>{(file.size / MB).toFixed(2)} MB</span>
            <Button size="small" disabled={locked} aria-label={`移除图片 ${index + 1}`} onClick={() => removeImage(index)}>移除</Button>
          </Space>
        ))}
      </Space>
      <Button block type="primary" disabled={locked || !images.length || recognizing} loading={recognizing} onClick={() => void recognize()}>识别采购单图片</Button>
      {recognizing && <Alert type="info" showIcon title="正在识别采购单图片，最多约需 1 分钟" description="请耐心等待，不要重复点击或重试。完成后会显示可编辑草稿；数量、材质等未识别字段需对照原图手工补全，系统不会猜填。" />}
      {error && <Alert type="error" showIcon title={error} />}
      {draft && <>
        <Alert type="warning" showIcon title="识别草稿 · 客户：艾斯霖" description="未识别的字段请手工补全，不要猜测。任何编辑都会取消已核对确认并使预检失效；重新识别会清除旧草稿。" />
        {draft.documents.map((document, documentIndex) => (
          <Card key={document.document_id} size="small" title={`采购单 ${documentIndex + 1} · ${document.file_name}`}>
            <Space orientation="vertical" style={{ width: '100%' }}>
              {(document.warnings || []).map((warning, index) => <Alert key={`warning-${index}`} type="warning" showIcon title={warning} />)}
              <details><summary>查看识别原文</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{document.raw_text || '未返回识别原文，请核对原图。'}</pre></details>
              <Row gutter={[12, 12]}>
                <Col xs={24} sm={12}><label>采购单号 *<Input aria-label={`采购单 ${documentIndex + 1} 采购单号`} required value={document.order_no ?? ''} disabled={locked} onChange={(event) => editDocument(documentIndex, (value) => ({ ...value, order_no: event.target.value }))} /></label></Col>
                <Col xs={24} sm={12}><label>采购日期 *<Input aria-label={`采购单 ${documentIndex + 1} 采购日期`} required type="date" value={document.order_date ?? ''} disabled={locked} onChange={(event) => editDocument(documentIndex, (value) => ({ ...value, order_date: event.target.value }))} /></label></Col>
              </Row>
              {document.rows.map((row, rowIndex) => (
                <Card key={`row-${rowIndex}`} size="small" title={`明细 ${rowIndex + 1}`} extra={<Button size="small" danger disabled={locked || document.rows.length <= 1} aria-label={`采购单 ${documentIndex + 1} 删除明细 ${rowIndex + 1}`} onClick={() => editDocument(documentIndex, (value) => ({ ...value, rows: value.rows.length > 1 ? value.rows.filter((_, index) => index !== rowIndex) : value.rows }))}>删除</Button>}>
                  {row.product_specification_id != null && <Space wrap style={{ marginBottom: 12 }}>
                    <Tag color="blue">已关联产品档案 ID：{row.product_specification_id}</Tag>
                    <Button size="small" disabled={locked} aria-label={`采购单 ${documentIndex + 1} 明细 ${rowIndex + 1} 清除关联产品`} onClick={() => editDocument(documentIndex, (value) => ({ ...value, rows: value.rows.map((line, index) => index === rowIndex ? { ...line, product_specification_id: null } : line) }))}>清除关联</Button>
                  </Space>}
                  <Row gutter={[12, 12]}>
                    {ROW_FIELDS.map(({ key, label, type, required }) => (
                      <Col xs={24} sm={12} key={key}>
                        <label>{label}{required ? ' *' : '（选填）'}<Input aria-label={`采购单 ${documentIndex + 1} 明细 ${rowIndex + 1} ${label}`} required={required} type={type || 'text'} min={type === 'number' ? (key === 'order_quantity' ? 1 : 0) : undefined} step={type === 'number' ? (key === 'order_quantity' ? 1 : 'any') : undefined} value={row[key] ?? ''} disabled={locked} onChange={(event) => editDocument(documentIndex, (value) => ({ ...value, rows: value.rows.map((line, index) => index === rowIndex ? { ...line, [key]: event.target.value, ...(key === 'specification' || key === 'material' ? { product_specification_id: null } : {}) } : line) }))} /></label>
                      </Col>
                    ))}
                  </Row>
                </Card>
              ))}
              <span>明细 {document.rows.length} / 100 条；每张图片至少保留 1 条明细。</span>
              <Button disabled={locked || document.rows.length >= 100} onClick={() => editDocument(documentIndex, (value) => ({ ...value, rows: value.rows.length < 100 ? [...value.rows, { item_no: '', product_name: '', specification: '', material: '', order_quantity: '', unit_price: '', amount: '', due_date: '' }] : value.rows }))}>添加空白明细（手工核对填写）</Button>
            </Space>
          </Card>
        ))}
        {errors.length > 0 && <Alert type="warning" showIcon title="请补全或修正必填字段" description={<ul>{errors.map((value) => <li key={value}>{value}</li>)}</ul>} />}
        <Checkbox checked={confirmed} disabled={locked} onChange={(event) => { invalidate(); setConfirmed(event.target.checked) }}>我已对照原图核对所有采购单字段</Checkbox>
        <Button block disabled={locked || !confirmed || errors.length > 0 || previewing} loading={previewing} onClick={() => void runPreview()}>确认核对并预检</Button>
      </>}
      {preview && <section aria-label="采购单预检结果">
        <Space orientation="vertical" style={{ width: '100%' }}>
          <Alert showIcon type={canCommit ? 'success' : 'error'} title={`预检：${preview.total_rows} 条，错误 ${preview.error_count} / 警告 ${preview.warning_count}`} />
          {preview.issues.map((issue, index) => <Alert key={`issue-${index}`} type={issue.level === 'error' ? 'error' : 'warning'} showIcon title={`${issue.sheet || '采购单'}${issue.row == null ? '' : ` / 行 ${issue.row}`}${issue.field ? ` / ${CHANGE_LABELS[issue.field] || issue.field}` : ''}：${issue.message}`} />)}
          {preview.rows.map((row) => {
            const source = draft?.documents.filter((document) => document.order_no === row.order_no).flatMap((document) => document.rows).find((line) => String(line.item_no) === String(row.item_no))
            const matchingRows = draft?.documents.flatMap((document) => document.rows).filter((line) => sameProduct(line, row.specification, row.material)) || []
            const selectedIds = new Set(matchingRows.map((line) => line.product_specification_id))
            const selectedId = selectedIds.size === 1 ? matchingRows[0]?.product_specification_id ?? undefined : undefined
            return <Card key={row.row_key} size="small" title={row.record_type === 'ORDER' ? `采购单号：${row.order_no || '（空）'} / 项次：${row.item_no || '（空）'}` : `产品规格：${row.specification || '（空）'} / 材质：${row.material || '（空）'}`} extra={<Tag color={row.valid === false ? 'error' : 'processing'}>{{ CREATE: '新增', UPDATE: '更新', SKIP: '跳过' }[row.action || ''] || row.action || '待核对'}</Tag>}>
              {row.record_type === 'ORDER' && <p>数量：{text(row.order_quantity ?? row.changes?.order_quantity?.to ?? source?.order_quantity)} · 交期：{text(row.due_date ?? row.changes?.due_date?.to ?? source?.due_date)}</p>}
              {row.valid === false && <Tag color="error">存在冲突或校验错误，禁止整批导入</Tag>}
              {row.summary && <p>{row.summary}</p>}
              {typeof row.skip_reason === 'string' && row.skip_reason && <p>跳过原因：{row.skip_reason}</p>}
              {typeof row.link_reason === 'string' && row.link_reason && <p>关联提示：{row.link_reason}</p>}
              {row.record_type === 'PRODUCT_SPECIFICATION' && !!row.product_candidates?.length && <>
                <p>请人工选择关联产品档案；不会默认选择第一条。选择后需重新核对并预检。</p>
                <Select<number> aria-label={`关联产品档案 ${row.specification} / ${row.material}`} style={{ width: '100%', marginBottom: 12 }} placeholder="请选择档案（不自动选择）" allowClear virtual={false} value={selectedId} disabled={locked || !matchingRows.length}
                  options={row.product_candidates.map((candidate) => ({ value: candidate.id, label: `ID ${candidate.id} · ${candidate.product_name || '未填写品名'} · ${candidate.specification} / ${candidate.material} · 模具：${candidate.mold_no || '未关联'}` }))}
                  optionRender={(option) => <div style={{ whiteSpace: 'normal', overflowWrap: 'anywhere' }}>{option.label}</div>}
                  onChange={(id) => bindProduct(row.specification, row.material, id)} />
              </>}
              {Object.entries(row.changes || {}).map(([field, change]) => <div key={field}><strong>{CHANGE_LABELS[field] || field}：</strong>{text(change?.from)} → {text(change?.to)}</div>)}
            </Card>
          })}
          <Button block type="primary" disabled={locked || busy || !canCommit} loading={committing} onClick={() => void commit()}>确认整批导入采购单</Button>
        </Space>
      </section>}
    </Space>
  )
}
