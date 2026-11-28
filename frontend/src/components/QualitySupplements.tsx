import { useState } from 'react'
import { Alert, Button, Checkbox, Drawer, Form, Input, InputNumber, Pagination, Select, Space, Spin, Tag, Typography } from 'antd'
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'
import { apiFetch, productSpecificationApi, toList } from '../api/client'
import { finishFlowRequest, flowRequestKey } from '../flowcards'
import type { ApiList, QualityEmployee } from '../types'
import { QualityQrScanner } from './QualityQrScanner'

const base = '/api/quality/supplements/'
const today = () => dayjs().format('YYYY-MM-DD')
type Kind = 'REQUEST' | 'REPLACEMENT'
export type SupplementStart = { batchId?: number; issueId?: number; code?: string; mode?: 'new' | 'list' }
type Origin = { batch_id: number; unit_no: number; product_id: number; shipment_no: string; card_no: string; specification: string; material: string; date: string; customer_code: string; project_no: string; return_id?: number; is_supplement: boolean }
type Claim = { id: number; order_no: string; item_no: string; quantity: number }
type Shipment = { id: number; date: string; recorded_at: string; recorded_by: number; shipment_no: string; package_no: string; material: string; specification: string; kind: Kind; quantity: number | null; weight_kg: string | null; unit_weight_g?: string | null; customer_card_no: string; reused_card_no?: string; inspector: string | null; state: string; claimed_quantity: number; claimable: number | null; return_id?: number; returned_quantity: number | null; delivery_info?: string; backfill_reason?: string; claims: Claim[] }
type Issue = { id: number; reference: string; kind: Kind; reason: string; specification: string; material: string; customer_code: string; project_no: string; product_id: number; package_count: number; requested_quantity: number | null; remaining: number | null; shipped_quantity: number; unknown_packages: number; retest_note: string; disputed_quantity: number | null; dispute_note: string; moved_out: boolean; origins: Array<{ batch_id: number; unit_no: number; card_no_snapshot: string; batch__shipment_no: string; batch__supplement_kind: string }> }
type Detail = Shipment & { issue_id: number; issue: Issue; history?: Array<{ action: string; created_at: string; actor_id: number; details: { note?: string } }> }
type Orders = Array<{ id: number; order_no: string; item_no: string; remaining: number }>
type ReleaseOption = { origin_id: number; allocation_id: number; order_no: string; item_no: string; max_quantity: number; expected_released: number; shipment_no: string; unit_no: number }
type IssueDetail = Issue & { shipments: Shipment[]; release_options: ReleaseOption[]; delivery_releases: {id: number; shipment_no: string; unit_no: number; order_no: string; item_no: string; quantity: number; note: string; active: boolean}[];
  delivery_release_history?: {id: number; actor_id: number; created_at: string; details: {order_no: string; item_no: string; quantity: number; net_before: number; net_after: number; note: string}}[] }
type Lookup = { origins: Origin[]; replacements: Shipment[]; related_issues?: Issue[]; unverified_shipment_ids: number[]; truncated_shipments?: string[] }
type Candidates = ApiList<{ id: number; shipment_no: string; quantity: number | null; delivery_info: string; kind?: Kind; legacy?: boolean }>
const errorMessage = (error: unknown) => error instanceof Error ? error.message : String(error)
const pieces = (quantity: number | null) => quantity === null ? '件数待核' : quantity.toLocaleString() + ' 件'
export const supplementLabel = (kind: Kind) => kind === 'REPLACEMENT' ? '质量换新' : '临时无单'
const eventLabels: Record<string, string> = {
  SHIP: '登记实发', PENDING: '建立待补事项', CLAIM: '认领订单项次', UNCLAIM: '更正认领',
  NO_CLAIM: '无需挂单', REOPEN: '恢复待挂', VERIFY_QUANTITY: '核实实点件数', RETURN: '整包实退',
  ISSUE_REVIEW: '核对补货事项', LINK_ISSUE: '已发包关联事项',
}
export const getSupplementConfig = () => apiFetch<{ enabled: boolean; can_reconcile: boolean }>(base + 'config/')
const link = (query: Record<string, string>) => '?' + new URLSearchParams(query).toString()
const whole = { width: '100%' }

export function QualitySupplements({ start, employees, canReconcile, readOnly, onClose, onSaved, onReturn, onOriginal }: {
  start: SupplementStart; employees: QualityEmployee[]; canReconcile: boolean; readOnly: boolean
  onClose: () => void; onSaved: () => void; onReturn: (id: number) => void; onOriginal: (id: number) => void
}) {
  const [mode, setMode] = useState(start.batchId ? 'detail' : start.issueId ? 'issue' : start.mode || 'new')
  const [batchId, setBatchId] = useState(start.batchId)
  const [issueId, setIssueId] = useState(start.issueId)
  const [release, setRelease] = useState<ReleaseOption>()
  const [lookupText, setLookupText] = useState(start.code || '')
  const [lookupCode, setLookupCode] = useState(start.code || '')
  const [scan, setScan] = useState<'old' | 'new'>()
  const [kind, setKind] = useState<Kind>('REQUEST')
  const [basisIssue, setBasisIssue] = useState<Issue>()
  const [origins, setOrigins] = useState<Origin[]>([])
  const [method, setMethod] = useState<'COUNT' | 'WEIGHT'>('COUNT')
  const [newCard, setNewCard] = useState(false)
  const [state, setState] = useState('')
  const [listPage, setListPage] = useState(1)
  const [searchTerm, setSearchTerm] = useState('')
  const [searchDraft, setSearchDraft] = useState('')
  const [productTerm, setProductTerm] = useState('')
  const [orderText, setOrderText] = useState('')
  const [orderTerm, setOrderTerm] = useState('')
  const [action, setAction] = useState('')
  const [pending, setPending] = useState<Record<string, unknown>>()
  const [candidates, setCandidates] = useState<Candidates>()
  const [checked, setChecked] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [form] = Form.useForm()
  const [actionForm] = Form.useForm()
  const productId = Form.useWatch('product_id', form)
  const [selectedProduct, setSelectedProduct] = useState<{ value: number; label: string }>()
  const businessDate = Form.useWatch('shipment_date', form)
  const lookup = useQuery({ queryKey: ['quality', 'supplement-origins', lookupCode], enabled: !!lookupCode, queryFn: ({signal}) => apiFetch<Lookup>(base + 'origins/' + link({ code: lookupCode }), {signal}) })
  const products = useQuery({ queryKey: ['quality', 'supplement-products', productTerm], enabled: mode === 'new' && !!productTerm.trim(), queryFn: async () => toList(await productSpecificationApi.list({ q: productTerm, active: true, page_size: 35 })) })
  const list = useQuery({ queryKey: ['quality', 'supplement-list', state, listPage, searchTerm], enabled: mode === 'list', queryFn: ({signal}) => apiFetch<ApiList<Shipment>>(base + link({state, q: searchTerm, page: String(listPage)}), {signal}) })
  const [issuePage, setIssuePage] = useState(1)
  const issues = useQuery({ queryKey: ['quality', 'supplement-issues', issuePage], enabled: mode === 'issues' || action === 'link-issue', queryFn: ({signal}) => apiFetch<ApiList<Issue>>(base + 'issues/' + link({page: String(issuePage)}), {signal}) })
  const activeIssue = useQuery({ queryKey: ['quality', 'supplement-issue', issueId], enabled: !!issueId && mode === 'issue',
    queryFn: ({signal}) => apiFetch<IssueDetail>(base + 'issues/' + issueId + '/', {signal}) })
  const detail = useQuery({ queryKey: ['quality', 'supplement-detail', batchId], enabled: mode === 'detail' && !!batchId, queryFn: ({signal}) => apiFetch<Detail>(base + batchId + '/', {signal}) })
  const orders = useQuery({ queryKey: ['quality', 'supplement-orders', batchId, orderTerm], enabled: mode === 'detail' && action === 'claim', queryFn: ({signal}) => apiFetch<Orders>(base + batchId + '/orders/' + link({q: orderTerm}), {signal}) })
  const showDetail = (id: number) => { setMode('detail'); setBatchId(id); setAction(''); setError(''); setSuccess('') }
  const showIssue = (id: number) => { setMode('issue'); setIssueId(id); setRelease(undefined); setError(''); setSuccess('') }
  const clearCandidate = () => { setPending(undefined); setCandidates(undefined); setChecked(false) }
  const report = (err: unknown) => { setError(errorMessage(err)); setSuccess('') }
  function resetNew(issue?: Issue) {
    clearCandidate(); setError(''); setSuccess(''); setBasisIssue(issue); setOrigins([])
    setSelectedProduct(undefined); setNewCard(false); setMethod('COUNT'); setKind(issue?.kind || 'REQUEST')
    form.resetFields(); form.setFieldValue('shipment_date', today()); setMode('new')
  }
  function selectOrigin(row: Origin) {
    const existing = lookup.data?.related_issues?.filter((i) => i.origins.some((o) => o.batch_id === row.batch_id && o.unit_no === row.unit_no)) || []
    if (existing.length === 1 && !basisIssue) { showIssue(existing[0].id); return }
    if (existing.length > 1 && !basisIssue) { setError('本原包有多条补货事项，请核对后打开对应事项，不另建重复记录。'); return }
    if (basisIssue && row.product_id !== basisIssue.product_id) {
      setError('原包不是当前事项的产品档案。'); return
    }
    setKind('REPLACEMENT')
    if (origins.some((o) => o.product_id !== row.product_id ||
      o.customer_code.trim().toLowerCase() !== row.customer_code.trim().toLowerCase() ||
      o.project_no.trim().toLowerCase() !== row.project_no.trim().toLowerCase())) {
      setError('产品、客户代号或项目不同，不能放入同一补货事项。'); return
    }
    setOrigins((prior) => prior.some((x) => x.batch_id === row.batch_id && x.unit_no === row.unit_no) ? prior : [...prior, row])
    setSelectedProduct({ value: row.product_id, label: row.specification + ' / ' + row.material })
    form.setFieldsValue({ product_id: row.product_id, customer_code: row.customer_code, project_no: row.project_no })
    setMode('new'); clearCandidate()
  }
  async function submit(path: string, body: Record<string, unknown>) {
    setBusy(true); setError(''); setSuccess('')
    try {
      const slot = 'supplement:' + path
      const result = await apiFetch<{batch_id: number | null; issue_id: number}>(base + path, {
        method: 'POST', body: JSON.stringify({ ...body, request_key: flowRequestKey(slot, body) }),
      })
      finishFlowRequest(slot)
      onSaved()
      if (result.batch_id && result.batch_id !== batchId) showDetail(result.batch_id)
      else if (!result.batch_id && mode === 'new') { clearCandidate(); showIssue(result.issue_id) }
      else if (mode === 'issue') { setRelease(undefined); await activeIssue.refetch() }
      else { await detail.refetch(); if (path.endsWith('/claim/')) await orders.refetch() }
      setAction(''); actionForm.resetFields(); clearCandidate()
      setSuccess(path.endsWith('/delivery-release/') ? '有效交付已更新。原出货保留，本次没有登记退货。' :
        result.batch_id ? '已保存；认领和更正不会新增出货。' : '事项已保存。')
    } catch (err) { report(err) }
    finally { setBusy(false) }
  }
  async function prepare() {
    try {
      const values = await form.validateFields()
      const { internal_card_id, customer_card_no, inspector_id, unit_weight_g, weight_kg, quantity, requested_quantity,
        product_id, ...fields } = values
      const sourceCards = basisIssue?.origins ?? origins.map((o) => ({card_no_snapshot:o.card_no}))
      const body = {
        ...fields, ...(basisIssue ? { issue_id: basisIssue.id, expected_packages: basisIssue.package_count } : {
          kind, product_id, requested_quantity: requested_quantity ?? null,
        }),
        origins: kind === 'REPLACEMENT' ? origins.map((x) => ({batch_id: x.batch_id, unit_no: x.unit_no})) : [],
        method, ...(method === 'COUNT' ? { quantity } : { weight_kg, ...(unit_weight_g ? {unit_weight_g} : {}) }),
        ...(internal_card_id?.trim() ? { internal_card_id: internal_card_id.trim().replace(/^ERPFC:/i, '') } : {}),
        ...(newCard ? {customer_card_no} : sourceCards.length === 1 && sourceCards[0].card_no_snapshot ?
          {reused_card_no:sourceCards[0].card_no_snapshot} : {}), ...(inspector_id ? {inspector_id} : {}),
      }
      setBusy(true); setError('')
      const rows = await apiFetch<Candidates>(base + 'preflight/' + link({
        product_id: String(basisIssue?.product_id || productId), date: String(values.shipment_date),
        customer_code: String(basisIssue?.customer_code ?? values.customer_code ?? ''),
        project_no: String(basisIssue?.project_no ?? values.project_no ?? ''),
      }))
      setCandidates(rows); setPending(body); setChecked(false)
    } catch (err) { if (!(err && typeof err === 'object' && 'errorFields' in err)) report(err) }
    finally { setBusy(false) }
  }
  const savePending = async () => {
    try {
      const fields = await form.validateFields(['product_id', 'reason', 'customer_code', 'project_no', 'requested_quantity'])
      await submit('issues/', { ...fields, kind, origins: kind === 'REPLACEMENT' ? origins.map(({batch_id, unit_no}) => ({batch_id, unit_no})) : [],
        ...(kind === 'REPLACEMENT' && origins.length === 1 && origins[0].card_no ? {reused_card_no:origins[0].card_no} : {}) })
    } catch (err) { if (!(err && typeof err === 'object' && 'errorFields' in err)) report(err) }
  }
  const row = detail.data
  const sourceCards = basisIssue?.origins ?? origins.map((o) => ({card_no_snapshot:o.card_no}))
  const reusedCard = sourceCards.length === 1 ? sourceCards[0].card_no_snapshot : ''
  const shipLink = (item: Shipment) => <Button key={item.id} block style={{ height: 'auto', whiteSpace: 'normal', textAlign: 'left' }} onClick={() => showDetail(item.id)}>
    {item.date} · {item.shipment_no} · {item.specification} / {item.material} · {pieces(item.quantity)}
  </Button>
  const issueBox = (issue: Issue) => <Space direction="vertical" style={whole} size="small">
    <Typography.Text strong>{issue.reference} · {supplementLabel(issue.kind)} · {issue.specification} / {issue.material}</Typography.Text>
    <Typography.Text>{issue.reason} · 客户代号 {issue.customer_code || '未登记'} · 项目 {issue.project_no || '未登记'}</Typography.Text>
    <Typography.Text>要求 {pieces(issue.requested_quantity)} · 已发 {pieces(issue.shipped_quantity)}{issue.unknown_packages ? '（另有 ' + issue.unknown_packages + ' 包件数待核）' : ''} · 待补 {pieces(issue.remaining)}</Typography.Text>
    <Typography.Text>客户复验：{issue.retest_note || '未回'}</Typography.Text>
    {issue.disputed_quantity != null && <Alert type="warning" message={'交付待核：' + pieces(issue.disputed_quantity)} description={issue.dispute_note + '（不自动回冲）'} />}
    {issue.kind === 'REPLACEMENT' && !issue.origins.length && <Tag>原出货待核</Tag>}
    {issue.moved_out && <Tag>实物包已更正关联，不再作为待补事项</Tag>}
    {issue.origins.map((o) => <Button key={o.batch_id + '/' + o.unit_no} type="link" style={{padding: 0, whiteSpace: 'normal', height: 'auto', textAlign: 'left'}} onClick={() => o.batch__supplement_kind ? showDetail(o.batch_id) : onOriginal(o.batch_id)}>原包 {o.batch__shipment_no} / {o.unit_no} {o.card_no_snapshot}</Button>)}
  </Space>
  return <Drawer open title="翔邦 · 补货与交付核对" width="min(620px, 100vw)" onClose={onClose} maskClosable={false} keyboard={!busy} closeIcon={busy ? false : undefined}>
    <Space direction="vertical" size="middle" style={whole}>
      <Space wrap>
        {!readOnly && <Button disabled={busy} type={mode === 'new' ? 'primary' : 'default'} onClick={() => resetNew()}>登记无单实发</Button>}
        <Button disabled={busy} type={mode === 'list' ? 'primary' : 'default'} onClick={() => setMode('list')}>补货台账</Button>
        <Button disabled={busy} type={mode === 'issues' ? 'primary' : 'default'} onClick={() => setMode('issues')}>补货事项</Button>
      </Space>
      {error && <Alert type="error" showIcon message={error} />}
      {success && <Alert type="success" showIcon message={success} />}
      <Space.Compact style={whole}>
        <Input aria-label="完整旧卡号或出货单号" placeholder="旧卡号、出货单号、ERP 包号或送货单号" value={lookupText} onChange={(e) => setLookupText(e.target.value)} onPressEnter={() => setLookupCode(lookupText.trim())} />
        <Button disabled={busy || !lookupText.trim()} loading={lookup.isFetching} onClick={() => { setError(''); if (lookupCode === lookupText.trim()) void lookup.refetch(); else setLookupCode(lookupText.trim()) }}>查原货</Button>
        <Button disabled={busy} onClick={() => setScan('old')}>扫码</Button>
      </Space.Compact>
      {lookup.isError && <Alert type="error" message="原货查询失败，不代表无记录" description={errorMessage(lookup.error)} />}
      {lookup.data && <div>
        <Typography.Text strong>原包出货历史（不自动选最新）</Typography.Text>
        {!lookup.data.origins.length && <div>未查到可核实的原包，可先登记来源待核。</div>}
        {lookup.data.origins.map((item) => <div key={item.batch_id + '/' + item.unit_no} style={{ margin: '8px 0' }}>
          <div>{item.date} · {item.shipment_no} / {item.unit_no} · {item.specification} / {item.material} {item.card_no && ' · ' + item.card_no}</div>
          <Space wrap>
            <Button size="small" onClick={() => item.is_supplement ? showDetail(item.batch_id) : onOriginal(item.batch_id)}>查原出货</Button>
            {!readOnly && <Button size="small" disabled={busy} onClick={() => selectOrigin(item)}>{item.card_no ? '沿原卡补发新货' : '补发新货'}</Button>}
            {!!item.return_id && <Button size="small" onClick={() => onReturn(item.return_id!)}>{item.is_supplement ? '打开原退回记录' : '沿退货单再次出货'}</Button>}
          </Space>
        </div>)}
        {!!lookup.data.unverified_shipment_ids.length && <Alert type="warning" message="另有出货关联，但不能核实实物包" action={<Space wrap>{lookup.data.unverified_shipment_ids.map((id) => <Button size="small" key={id} onClick={() => onOriginal(id)}>查出货 #{id}</Button>)}</Space>} />}
        {!!lookup.data.truncated_shipments?.length && <Alert type="info" title="部分出货批数较多" description="按完整流程卡，或在出货单号后输入 /物理批号 查询其余实物包。" />}
        {!!lookup.data.replacements.length && <Space direction="vertical" style={whole}><Typography.Text strong>因此补发的新包</Typography.Text>{lookup.data.replacements.map(shipLink)}</Space>}
        {lookup.data.related_issues?.map((item) => <Button key={item.id} onClick={() => showIssue(item.id)}>打开已有补货事项 {item.reference}</Button>)}
      </div>}
      {mode === 'new' && !readOnly && <>
        <Alert type="info" message="本次是一只新实物包。原包退回后原包再发，请打开原退货记录。" />
        <Form form={form} preserve={false} layout="vertical" initialValues={{shipment_date: today()}} onValuesChange={clearCandidate} disabled={busy}>
          {!basisIssue ? <>
            <Form.Item label="为什么补？">
              <Select value={kind} onChange={(value) => {setKind(value); setOrigins([]); clearCandidate()}} options={[{value:'REQUEST',label:'客户临时要货'},{value:'REPLACEMENT',label:'质量问题换新货'}]} />
            </Form.Item>
            <Form.Item name="product_id" label="具体产品档案" rules={[{required:true}]}>
              <Select showSearch filterOption={false} disabled={!!origins.length} onSearch={setProductTerm} placeholder="输入规格查档案" loading={products.isFetching} notFoundContent={products.isError ? '产品查询失败，请重试' : '输入规格搜索'}
                options={[...(selectedProduct ? [selectedProduct] : []), ...(products.data || []).filter((p) => p.id !== selectedProduct?.value).map((p) => ({ value: p.id, label: p.specification + ' / ' + p.material + ' · ' + (p.product_name || '') + ' #' + p.id }))]} />
            </Form.Item>
            <Form.Item name="customer_code" label="实际客户代号（已知才填）"><Input maxLength={150} /></Form.Item>
            <Form.Item name="project_no" label="项目号（已知才填）"><Input maxLength={150} /></Form.Item>
            <Form.Item name="reason" label="补货原因" rules={[{required:true, whitespace:true}]}><Input placeholder={kind === 'REPLACEMENT' ? '例如：硬度超标' : '例如：客户先要货'} maxLength={300} /></Form.Item>
            <Form.Item name="requested_quantity" label="客户要求量（件，未知留空）"><InputNumber min={1} precision={0} style={whole} /></Form.Item>
          </> : issueBox(basisIssue)}
          {kind === 'REPLACEMENT' && <Form.Item label="原出货（选原包才能建立溯源）">
            {origins.length ? origins.map((o) => <Tag closable key={o.batch_id + '/' + o.unit_no} onClose={() => { setOrigins((prev) => prev.filter((x) => x !== o)); clearCandidate() }}>{o.shipment_no}/{o.unit_no}</Tag>) : <Tag>尚未增加核实的原包</Tag>}
          </Form.Item>}
          {!basisIssue && <Form.Item><Button disabled={busy} onClick={() => void savePending()}>尚未发出 · 只建待补事项</Button></Form.Item>}
          <Form.Item name="shipment_date" label="实发日期" rules={[{required:true}]}><Input type="date" max={today()} /></Form.Item>
          {businessDate && businessDate < today() && <Form.Item name="backfill_reason" label="补录已发原因" rules={[{required:true, whitespace:true}]}><Input maxLength={300} /></Form.Item>}
          <Form.Item label="客户新卡">
            <Select value={newCard ? 'NEW' : 'NONE'} onChange={(value) => {setNewCard(value === 'NEW'); clearCandidate()}} options={[{value:'NONE',label:reusedCard ? `沿用原卡号 ${reusedCard}（新包独立编号）` : '客户未提供新卡（使用独立 ERP 包号）'},{value:'NEW',label:'登记本次客户新卡'}]} />
          </Form.Item>
          {newCard && <Space.Compact style={whole}><Form.Item style={{flex:1}} name="customer_card_no" rules={[{required:true, whitespace:true}]}><Input aria-label="本次客户新卡" maxLength={150} /></Form.Item><Button onClick={() => setScan('new')}>扫新卡</Button></Space.Compact>}
          <Form.Item name="internal_card_id" label="已有内部工序卡（可选，填卡 ID）"><Input /></Form.Item>
          <Form.Item label="计量方式"><Select value={method} onChange={(value) => {setMethod(value); clearCandidate()}} options={[{value:'COUNT',label:'可靠实点件数'},{value:'WEIGHT',label:'实称净重'}]} /></Form.Item>
          {method === 'COUNT' ? <Form.Item name="quantity" label="实发件数" rules={[{required:true}]}><InputNumber min={1} precision={0} style={whole} /></Form.Item> : <>
            <Form.Item name="weight_kg" label="实称净重 (kg)" rules={[{required:true}]}><InputNumber stringMode min="0.001" precision={3} style={whole} /></Form.Item>
            <Form.Item name="unit_weight_g" label="成品单重 (g/件)，没有则件数待核"><InputNumber stringMode min="0.00001" precision={5} style={whole} /></Form.Item>
          </>}
          <Form.Item name="inspector_id" label="本次品检员（可选）"><Select showSearch allowClear optionFilterProp="label" options={employees.filter((x) => x.is_active && ['INSPECTOR','BOTH'].includes(x.role)).map((x) => ({value:x.id,label:x.name}))} /></Form.Item>
          <Form.Item name="delivery_info" label="送货单号（可选）"><Input maxLength={300} /></Form.Item>
          <Form.Item name="source_note" label="来源、客户旧卡引用或超补依据（可选）"><Input.TextArea maxLength={500} /></Form.Item>
          {canReconcile && <Form.Item name="overage_confirmed" valuePropName="checked"><Checkbox>我已核实可能超补（须填写依据）</Checkbox></Form.Item>}
        </Form>
        {candidates && <Space direction="vertical" style={whole}>
          <Alert type={candidates.count ? 'warning' : 'info'} message={candidates.count ? '发现同日同产品记录，请核对是否已登记' : '本次查重未发现同日同产品候选'} description="同日、同规格或同重量不等于同包。已登记请打开原记录，不要新建；可在补货事项中核对、补关联原出货。" />
          {candidates.results.map((r) => <Button style={{ height: 'auto', whiteSpace: 'normal' }} key={(r.legacy ? 'legacy:' : 'batch:') + r.id} disabled={r.legacy} onClick={() => r.kind ? showDetail(r.id) : onOriginal(r.id)}>{r.shipment_no} · {pieces(r.quantity)} {r.delivery_info}{r.legacy && '（请在每日出货查看）'}</Button>)}
          {(candidates.count || 0) > candidates.results.length && <Typography.Text>另有更多候选，请在每日出货核对。</Typography.Text>}
          <Checkbox checked={checked} onChange={(e) => setChecked(e.target.checked)}>已核实，这一包已发且没有登记</Checkbox>
        </Space>}
        {pending ? <Button type="primary" block disabled={!checked} loading={busy} onClick={() => void submit('', pending)}>确认登记实发</Button> :
          <Button type="primary" block loading={busy} onClick={() => void prepare()}>先查重，再登记实发</Button>}
      </>}
      {mode === 'list' && <>
        <Space wrap><Select aria-label="归属状态" value={state} onChange={(value) => { setState(value); setListPage(1) }} options={[{value:'',label:'全部出货'},{value:'PENDING',label:'待挂单／件数待核'},{value:'NO_CLAIM',label:'无需挂单'}]} />
          <Input.Search aria-label="查补货台账" value={searchDraft} onChange={(e) => setSearchDraft(e.target.value)} onSearch={(value) => {setSearchTerm(value);setListPage(1)}} placeholder="规格／出货单／卡号" /></Space>
        {list.isError ? <Alert type="error" message="台账读取失败" action={<Button onClick={() => void list.refetch()}>重试</Button>} /> : list.isFetching ? <Spin /> : <>
          {list.data?.results.map(shipLink)}
          {!list.data?.count && <Typography.Text>暂无匹配记录</Typography.Text>}
          <Pagination current={listPage} total={list.data?.count} pageSize={30} onChange={setListPage} hideOnSinglePage />
        </>}
      </>}
      {mode === 'issue' && (activeIssue.isError ? <Alert type="error" title="补货事项读取失败" action={<Button onClick={() => void activeIssue.refetch()}>重试</Button>} /> : !activeIssue.data ? <Spin /> : <>
        {issueBox(activeIssue.data)}
        <Typography.Text strong>原订单交付核对</Typography.Text>
        <Typography.Text type="secondary">翔邦明确原货不抵原项次才核减。不登记退货，也不改原出货；不足时订单自动重开。</Typography.Text>
        {activeIssue.data.delivery_releases.map((item) => <Alert key={item.id} type="info" title={`${item.order_no} / ${item.item_no || '无项次号'} · ${pieces(item.quantity)}${item.active ? '已核减' : '已由实退接替'}`}
          description={`${item.shipment_no}/${item.unit_no} · ${item.note}`} />)}
        {!!activeIssue.data.delivery_release_history?.length && <details><summary>核减操作记录</summary>
          {activeIssue.data.delivery_release_history.map(({id, actor_id, created_at, details}) => <div key={id}>
            {dayjs(created_at).format('MM-DD HH:mm')} · 账号 #{actor_id} · {details.order_no}/{details.item_no || '—'} · 核减 {pieces(details.quantity)} · 净交付 {details.net_before} → {details.net_after} · {details.note}
          </div>)}
        </details>}
        {!readOnly && canReconcile && activeIssue.data.release_options.map((item) => <Button key={item.origin_id + ':' + item.allocation_id}
          style={{height:'auto', whiteSpace:'normal'}} disabled={busy} onClick={() => setRelease(item)}>
          核减 {item.order_no}/{item.item_no || '—'} · 原包 {item.shipment_no}/{item.unit_no} · 最多 {pieces(item.max_quantity)}
        </Button>)}
        {release && <Form key={'release:' + release.origin_id + ':' + release.allocation_id} layout="vertical" disabled={busy}
          onFinish={(values) => void submit(`issues/${activeIssue.data!.id}/delivery-release/`, {
            ...values, origin_id:release.origin_id, allocation_id:release.allocation_id, expected_released:release.expected_released,
          })}>
          <Typography.Text strong>{release.order_no} / {release.item_no || '无项次号'}</Typography.Text>
          <Form.Item name="quantity" label="本次确认不抵交付的件数" rules={[{required:true}]}><InputNumber min={1} max={release.max_quantity} precision={0} style={whole} /></Form.Item>
          <Form.Item name="note" label="翔邦确认依据（如通知日期、送货单号）" rules={[{required:true, whitespace:true}]}><Input.TextArea maxLength={500}/></Form.Item>
          <Form.Item name="original_delivery_excluded" valuePropName="checked" rules={[{validator: (_, value) => value ? Promise.resolve() : Promise.reject(new Error('请先确认翔邦明确原货不抵交付'))}]}>
            <Checkbox>翔邦已确认该数量不抵本订单，原货未退回</Checkbox>
          </Form.Item>
          <Space><Button type="primary" htmlType="submit" loading={busy}>确认核减有效交付</Button><Button onClick={() => setRelease(undefined)}>取消</Button></Space>
        </Form>}
        {activeIssue.data.shipments.map(shipLink)}
        {!readOnly && !activeIssue.data.moved_out && <Button onClick={() => resetNew(activeIssue.data)}>在该事项登记另一只新包</Button>}
      </>)}
      {mode === 'issues' && <>
        {issues.isError ? <Alert type="error" message="补货事项读取失败" action={<Button onClick={() => void issues.refetch()}>重试</Button>} /> : issues.isFetching ? <Spin /> : <>
          {!issues.data?.count && <Typography.Text>暂无补货事项</Typography.Text>}
          {issues.data?.results.map((issue) => <Space key={issue.id} direction="vertical" style={{...whole, borderBottom:'1px solid #eee', paddingBottom:12}}>
            {issueBox(issue)}<Button onClick={() => showIssue(issue.id)}>查看事项与交付核对</Button>{!readOnly && !issue.moved_out && <Button onClick={() => resetNew(issue)}>在该事项登记另一只新包</Button>}
          </Space>)}
          <Pagination current={issuePage} total={issues.data?.count} pageSize={30} onChange={setIssuePage} hideOnSinglePage />
        </>}
      </>}
      {mode === 'detail' && (detail.isError ? <Alert type="error" message="原补货明细读取失败" action={<Button onClick={() => void detail.refetch()}>重试</Button>} /> : !row ? <Spin /> : <>
        <Typography.Title level={5} style={{margin:0}}>{row.shipment_no} · {supplementLabel(row.kind)}</Typography.Title>
        <div>{row.specification} / {row.material} · 实物包 {row.package_no}</div>
        <div>实际发货 {row.date} · {pieces(row.quantity)} · {row.weight_kg === null ? '未称重' : row.weight_kg + ' kg'}</div>
        <div>登记时间 {dayjs(row.recorded_at).format('YYYY-MM-DD HH:mm')} · 经手账号 #{row.recorded_by} · 品检员 {row.inspector || '未登记'}</div>
        {row.backfill_reason && <div>补录原因：{row.backfill_reason}</div>}
        <div>本次客户卡：{row.reused_card_no ? `沿用原卡号 ${row.reused_card_no}（追溯用）` : row.customer_card_no || '未提供'}{row.delivery_info && ' · 送货单 ' + row.delivery_info}</div>
        <div>已挂 {pieces(row.claimed_quantity)} · {row.return_id ? '已实退 ' + pieces(row.returned_quantity) : row.state === 'NO_CLAIM' ? '无需再挂单' : '还可挂 ' + pieces(row.claimable)}</div>
        {row.return_id && <Button onClick={() => onReturn(row.return_id!)}>查看真实退回记录</Button>}
        {row.claims.map((s) => <Space key={s.id} wrap>
          <Tag>{s.order_no} / {s.item_no || '未填项次'}：{pieces(s.quantity)}</Tag>
          {!readOnly && canReconcile && !row.return_id && <Button size="small" onClick={() => {actionForm.resetFields(); actionForm.setFieldValue('allocation_id', s.id); setAction('unclaim')}}>更正归属</Button>}
        </Space>)}
        {issueBox(row.issue)}
        {row.kind === 'REPLACEMENT' && <Button onClick={() => showIssue(row.issue_id)}>查看事项与交付核对</Button>}
        {!readOnly && <Space wrap>
          {!row.return_id && <Button onClick={() => {actionForm.resetFields();setAction('return')}}>登记整包实退</Button>}
          {canReconcile && !row.return_id && (row.claimable || 0) > 0 && <Button onClick={() => {actionForm.resetFields();setOrderText('');setOrderTerm('');setAction('claim')}}>{row.kind === 'REPLACEMENT' ? '将本次补发抵入订单' : '认领正式订单项次'}</Button>}
          {canReconcile && !row.return_id && row.quantity === null && <Button onClick={() => {actionForm.resetFields();setAction('verify-quantity')}}>核实实点件数</Button>}
          {canReconcile && !row.return_id && <Button onClick={() => {actionForm.resetFields();setAction(row.state === 'NO_CLAIM' ? 'reopen' : 'no-claim')}}>{row.state === 'NO_CLAIM' ? '恢复待挂' : '剩余无需挂单'}</Button>}
          {canReconcile && !row.return_id && row.kind === 'REQUEST' && row.state !== 'NO_CLAIM' && !row.claimed_quantity &&
            <Button onClick={() => {actionForm.resetFields();setIssuePage(1);setAction('link-issue')}}>已发包关联质量事项</Button>}
          {canReconcile && <Button onClick={() => {actionForm.resetFields();actionForm.setFieldsValue({requested_quantity:row.issue.requested_quantity, disputed_quantity:row.issue.disputed_quantity, dispute_note:row.issue.dispute_note, retest_note:row.issue.retest_note});setAction('issue')}}>核对补货事项</Button>}
        </Space>}
        {action && <Form name="supplement-action" form={actionForm} preserve={false} layout="vertical" disabled={busy} onFinish={(values) => void submit(action === 'issue' ? 'issues/' + row.issue.id + '/' : row.id + '/' + action + '/', action === 'issue' && origins.length ?
          {...values, origins: origins.map(({batch_id, unit_no}) => ({batch_id, unit_no}))} :
          action === 'return' ? {...values, expected_claimed: row.claimed_quantity, expected_allocations: row.claims.map((s) => s.id)} : values)}>
          {action === 'claim' && <>
            <Input.Search placeholder="完整正式订单号" value={orderText} onChange={(e) => { setOrderText(e.target.value); actionForm.setFieldValue('order_id', undefined) }} onSearch={(value) => setOrderTerm(value.trim())} enterButton="找项次" />
            {orders.isError && <Alert type="error" message="订单项次读取失败" />}
            {row.kind === 'REPLACEMENT' && !orderTerm && <Typography.Text type="secondary">优先列出本事项已核减、且仍有欠交的原项次；其他已核实项次可按完整订单号查找。</Typography.Text>}
            <Form.Item name="order_id" label="明确订单项次" rules={[{required:true}]}><Select loading={orders.isFetching} options={(orders.data || []).filter((o) => o.remaining > 0).map((o) => ({value:o.id,label:o.order_no + ' / ' + (o.item_no || '未填项次') + ' · 可认领 ' + o.remaining + ' 件'}))} /></Form.Item>
            {row.kind === 'REPLACEMENT' && <Form.Item name="basis" label="认领性质" rules={[{required:true}]}><Select options={[{value:'REPLACEMENT_WITHOUT_RETURN',label:'原货未退，已确认不抵本单'},{value:'REPLACEMENT_AFTER_RETURN',label:'实退已释放份额'},{value:'RECONCILED_SINGLE',label:'原货未挂本项，客户确认只认新包'},{value:'INDEPENDENT',label:'有独立新增采购证据'}]} /></Form.Item>}
          </>}
          {['claim','verify-quantity'].includes(action) && <Form.Item name="quantity" label={action === 'claim' ? '认领件数' : '实点件数'} rules={[{required:true}]}><InputNumber min={1} precision={0} max={action === 'claim' ? row.claimable ?? undefined : undefined} style={whole}/></Form.Item>}
          {action === 'verify-quantity' && <Form.Item valuePropName="checked" name="overage_confirmed"><Checkbox>已核实可能超出要求量</Checkbox></Form.Item>}
          {action === 'link-issue' && <>
            {issues.isError && <Alert type="error" message="相关事项读取失败" />}
            <Form.Item name="issue_id" label="明确的质量补货事项" rules={[{required:true}]}><Select
              loading={issues.isFetching}
              options={(issues.data?.results || []).filter((i) => !i.moved_out && i.kind === 'REPLACEMENT' && i.product_id === row.issue.product_id &&
                i.customer_code === row.issue.customer_code && i.project_no === row.issue.project_no).map((i) => ({ value:i.id, label:i.reference + ' · ' + i.reason }))}
              onChange={(id) => actionForm.setFieldValue('expected_packages', issues.data?.results.find((i) => i.id === id)?.package_count)}
            /></Form.Item>
            <Pagination current={issuePage} total={issues.data?.count} pageSize={30} onChange={(page) => {setIssuePage(page);actionForm.setFieldValue('issue_id', undefined)}} hideOnSinglePage />
            <Form.Item name="expected_packages" hidden><InputNumber /></Form.Item>
            <Form.Item valuePropName="checked" name="overage_confirmed"><Checkbox>已核实可能超出要求量或件数暂不明</Checkbox></Form.Item>
          </>}
          {action === 'return' && <>
            <Alert type="warning" title={`整包实退 ${pieces(row.quantity)}，只回冲已挂订单 ${pieces(row.claimed_quantity)}`}
              description="请核对上方各项次和数量。确认后，剩余未挂部分不能再认领；不自动登记返工或库存。" />
            <Form.Item initialValue={today()} name="action_date" label="实际退回日期" rules={[{required:true}]}><Input type="date" min={row.date} max={today()}/></Form.Item>
          </>}
          {action === 'return' && !row.inspector && <Form.Item name="inspector_id" label="原责任品检员（如可核实）">
            <Select allowClear options={employees.filter((x) => x.is_active && ['INSPECTOR','BOTH'].includes(x.role)).map((x) => ({value:x.id,label:x.name}))} />
          </Form.Item>}
          {action === 'unclaim' && <Form.Item name="allocation_id" hidden><InputNumber /></Form.Item>}
          {action === 'issue' && <>
            <Form.Item name="requested_quantity" label="客户要求量"><InputNumber min={1} precision={0} style={whole}/></Form.Item>
            <Form.Item name="disputed_quantity" label="交付待核件数（不回冲）"><InputNumber min={1} precision={0} style={whole}/></Form.Item>
            <Form.Item name="dispute_note" label="争议依据"><Input maxLength={500}/></Form.Item>
            <Form.Item name="retest_note" label="客户复验结果"><Input maxLength={500}/></Form.Item>
            {row.kind === 'REPLACEMENT' && lookup.data?.origins.filter((o) => o.product_id === row.issue.product_id && o.batch_id !== row.id).map((o) => <Form.Item key={o.batch_id + '/' + o.unit_no}><Checkbox checked={origins.some((x) => x.batch_id === o.batch_id && x.unit_no === o.unit_no)} onChange={(e) => setOrigins((prev) => e.target.checked ? [...prev,o] : prev.filter((x) => x.batch_id !== o.batch_id || x.unit_no !== o.unit_no))}>补关联已核实原包 {o.shipment_no}/{o.unit_no}</Checkbox></Form.Item>)}
          </>}
          <Form.Item name="note" label={action === 'return' ? '实退原因' : '核对／更正依据'} rules={[{required:true, whitespace:true}]}><Input.TextArea maxLength={500} /></Form.Item>
          <Space><Button loading={busy} htmlType="submit" type={action === 'return' ? 'default' : 'primary'} danger={action === 'return'}>{action === 'return' ? '确认整包已实退' : '确认保存'}</Button><Button disabled={busy} onClick={() => setAction('')}>取消</Button></Space>
        </Form>}
        {!!row.history?.length && <details><summary>操作记录</summary>{row.history.map((e, i) => <div key={i}>{dayjs(e.created_at).format('MM-DD HH:mm')} · {eventLabels[e.action] || e.action} · 账号 #{e.actor_id} {e.details.note}</div>)}</details>}
      </>)}
    </Space>
    <QualityQrScanner open={!!scan} title={scan === 'new' ? '扫描本次新客户卡' : '扫描旧客户卡查原货'} onClose={() => setScan(undefined)}
      onScan={(code) => {
        if (scan === 'new') { form.setFieldValue('customer_card_no', code); clearCandidate() }
        else { setLookupText(code); setLookupCode(code) }
        setScan(undefined)
      }} />
  </Drawer>
}
