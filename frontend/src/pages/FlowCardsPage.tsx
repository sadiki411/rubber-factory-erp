import { Alert, App, Button, Card, Checkbox, DatePicker, Descriptions, Empty, Form, Input, InputNumber, Modal, Pagination, Select, Skeleton, Space, Table, Tag, Typography, Upload } from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { qualityApi, toList } from '../api/client'
import { useReadOnly } from '../access'
import { PageTitle } from '../components/PageTitle'
import { QualityQrScanner } from '../components/QualityQrScanner'
import { FlowTemplatePanel } from '../components/FlowTemplatePanel'
import { FlowBakePanel } from '../components/FlowBakePanel'
import { flowApi, flowRequestKey, finishFlowRequest, isInternalCardCode, type FlowGroup, type PrintJob, type FlowTrace } from '../flowcards'
import { safeBusinessReturn } from '../navigation'
import './FlowCardsPage.css'

const WHOLE: Record<string, string> = { UNKNOWN: '待确认', REQUIRED: '需要整模', NOT_REQUIRED: '不需整模', SYSTEM_UNREGISTERED: '需整模·系统未登记', PARTIAL: '部分处理', COMPLETED: '整包作业完成', EXCEPTION: '异常待处理' }
const SOURCE_STATE: Record<string, string> = { NEEDS_REVIEW: '更正后/身份缺项待核对', CONFIRMED: '有明确关联', REVOKED: '原错误关联已撤销（留史）', SUPERSEDED: '已由重新核对版本替代' }
const STAGES = [ ['UPPER_TEMP', '上模接触实测温度'], ['LOWER_TEMP', '下模接触实测温度'], ['MOLDING_HARDNESS', '成型后硬度'], ['BAKED_HARDNESS', '烤后硬度'], ['DIMENSION', '关键尺寸'], ['SECONDARY_CURE', '二烤建议'] ].map(([value, label]) => ({ value, label }))

function SourceReturnHistory({ cardId, source }: { cardId: string; source: FlowTrace }) {
  const [page, setPage] = useState(1)
  const query = useQuery({ queryKey: ['flow-source-returns', cardId, source.id, page], queryFn: () => flowApi.traceReturns(cardId, source.id, page), enabled: page > 1 })
  const rows = page === 1 ? source.related_returns : query.data?.results
  return <>{query.isError && page > 1 ? <Alert type="error" title="退回历史读取失败，不视为无记录" /> : page > 1 && query.isPending ? <Skeleton active /> : rows?.map((r) => <div key={r.id}>退回 {r.case_no} · 第{r.return_round ?? '未知'}轮 · {r.status}{r.reshipment_batch_id && <Button href={`/quality?shipment_batch_id=${r.reshipment_batch_id}`}>本轮再发</Button>}</div>)}
    {source.related_return_count > 20 && <Pagination current={page} total={source.related_return_count} pageSize={20} showSizeChanger={false} onChange={setPage} />}</>
}

function CardLookup() {
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [code, setCode] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const locked = useRef(false)
  const scan = async (raw: string) => {
    if (locked.current) return false
    locked.current = true; setBusy(true); setError('')
    try {
      const card = await flowApi.scan(raw)
      setOpen(false); navigate(`/flow-cards/cards/${card.id}?return_to=${encodeURIComponent(location.pathname + location.search)}`)
      return true
    } catch (e) { setError((e as Error).message); return false }
    finally { locked.current = false; setBusy(false) }
  }
  return <Card size="small"><Space.Compact style={{ width: '100%' }}><Input aria-label="查找内部工序卡" placeholder="输入 ERPFC:… 或完整 FC-… 卡号" value={code} onChange={(e) => setCode(e.target.value)} onPressEnter={() => void scan(code)} /><Button loading={busy} disabled={!code.trim()} onClick={() => void scan(code)}>查看卡</Button><Button onClick={() => setOpen(true)}>扫码查卡</Button></Space.Compact>
    {error && <Alert type="error" title="内部卡读取失败；未新建客户卡或更改数量" description={error} />}
    <QualityQrScanner open={open} onClose={() => setOpen(false)} onScan={scan} continuous={false} normalizeValue={(v) => v?.trim() || ''} isValidValue={(v) => isInternalCardCode(v || '')} title="查看工序流转卡" description="扫描只打开详情，不登记产量或出退货。" valueNoun="内部卡" manualPlaceholder="ERPFC:… 或 FC-…" invalidMessage="请扫描内部工序卡；客户卡和库位码请使用原入口。" />
  </Card>
}

function PrintResults({ job, changed, disabled }: { job: PrintJob; changed: (job: PrintJob) => void; disabled: boolean }) {
  const { message } = App.useApp()
  const [success, setSuccess] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const save = async (cancel = false) => {
    setBusy(true)
    try {
      const outcomes = Object.fromEntries(job.items.filter((i) => i.active).map((i) => [i.card_id, success.includes(i.card_id) ? 'SUCCESS' : cancel ? 'CANCELLED' : 'UNKNOWN']))
      changed(await flowApi.printResult(job.id, outcomes)); setSuccess([])
      message.success('已保存实际确认结果；未知项仍保留在原任务')
    } catch (e) { message.error((e as Error).message) } finally { setBusy(false) }
  }
  return <Card size="small" title="打印任务（下载不等于打印成功）">
    <Typography.Paragraph copyable>请求号：{job.request_key}</Typography.Paragraph>
    {job.file_prepared ? <Button href={job.pdf_url} target="_blank">打开原PDF · 原尺寸100%打印</Button> : <Button disabled={disabled} loading={busy} onClick={() => { setBusy(true); flowApi.preparePrint(job.id).then(changed).catch((e: Error) => message.error(e.message)).finally(() => setBusy(false)) }}>继续原打印任务生成PDF</Button>}
    <Typography.Paragraph>仅勾选确实打印成功的卡。未勾选默认“结果未知”；失败后可继续下载同一任务，不另增补打次数。</Typography.Paragraph>
    <Checkbox.Group value={success} onChange={(v) => setSuccess(v as string[])} options={job.items.map((i) => ({ label: `${i.card_no} · 原${i.sequence}/${i.denominator}批 · ${i.proposed_reprint ? `补打${i.proposed_reprint}次` : '初印'} · ${i.status}`, value: i.card_id, disabled: disabled || !i.active }))} />
    <Space wrap style={{ marginTop: 12 }}><Button disabled={disabled || !job.items.some((i) => i.active)} loading={busy} onClick={() => void save()}>保存成功范围，其余待核对</Button><Button disabled={disabled || !job.items.some((i) => i.active)} loading={busy} onClick={() => void save(true)}>保存成功范围，其余明确取消</Button></Space>
  </Card>
}

export function FlowCardsPage() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const readOnly = useReadOnly()
  const { message } = App.useApp()
  const client = useQueryClient()
  const [form] = Form.useForm()
  const [standardForm] = Form.useForm()
  const [standardOpen, setStandardOpen] = useState(false)
  const [group, setGroup] = useState<FlowGroup>()
  const [preview, setPreview] = useState<{ count: number; tail_molds: number | null }>()
  const [cardPage, setCardPage] = useState(1)
  const [selected, setSelected] = useState<string[]>([])
  const [job, setJob] = useState<PrintJob>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [diagramImage, setDiagramImage] = useState<string>()
  const [diagramDirection, setDiagramDirection] = useState('')
  const [diagramScope, setDiagramScope] = useState('')
  const [diagramPositions, setDiagramPositions] = useState('')
  const [appearanceName, setAppearanceName] = useState('')
  const [groupListPage, setGroupListPage] = useState(1)
  const [groupSearch, setGroupSearch] = useState('')
  const initialized = useRef('')
  const scope = useMemo(() => new URLSearchParams([...params].filter(([key]) => ['order_id', 'run_id', 'product_id'].includes(key))), [params])
  const context = useQuery({ queryKey: ['flow-context', scope.toString()], queryFn: () => flowApi.context(scope), enabled: Boolean(scope.size) })
  const data = context.data
  const disabled = readOnly || !data?.write_enabled
  const allGroups = useQuery({ queryKey: ['flow-group-search', scope.toString(), groupListPage, groupSearch], queryFn: () => flowApi.groups(scope, groupListPage, groupSearch), enabled: Boolean(data) })
  const selectedStandard = Form.useWatch('standard_id', form)
  const cards = useQuery({ queryKey: ['flow-cards', group?.id, cardPage, group?.status], queryFn: () => flowApi.cards(group!.id, cardPage), enabled: Boolean(group?.status === 'GENERATED') })
  useEffect(() => {
    const key = scope.toString()
    if (data && initialized.current !== key) {
      initialized.current = key
      form.resetFields(); setGroup(undefined); setJob(undefined); setSelected([]); setPreview(undefined)
      const only = data.standards.length === 1 ? data.standards[0] : undefined
      form.setFieldsValue({ planned_molds: data.suggested_molds, molds_per_card: 100, standard_id: only?.id, whole_mold: only?.whole_mold || 'UNKNOWN', appearance_ids: (only?.snapshot.appearance || data.appearance.slice(0, 4)).map((a) => a.id) })
    }
  }, [data, form, scope])
  const payload = async () => ({ ...await form.validateFields(), product_id: data!.product.id, run_id: data!.run_id, order_ids: data!.order_ids })
  const act = async (work: () => Promise<void>) => {
    setBusy(true); setError('')
    try { await work() } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  const save = (generate: boolean) => act(async () => {
    const body = await payload()
    const slot = `group:${scope}`
    let result = await flowApi.create({ ...body, request_key: flowRequestKey(slot, body) })
    setGroup(result); void client.invalidateQueries({ queryKey: ['flow-context'] })
    if (generate) result = await flowApi.generate(result.id)
    setGroup(result); setSelected([]); setCardPage(1); finishFlowRequest(slot)
    void client.invalidateQueries({ queryKey: ['flow-context'] })
  })
  const loadDraft = () => {
    if (!group) return
    form.setFieldsValue({ planned_molds: group.planned_molds, molds_per_card: group.molds_per_card, standard_id: group.snapshot.standard?.id,
      whole_mold: group.whole_mold, whole_mold_basis: group.snapshot.whole_mold_basis, plan_basis: group.plan_basis,
      appearance_ids: group.snapshot.appearance?.map((a) => a.id), diagram_id: group.snapshot.diagram_id,
      confirmed_total_cavities: group.snapshot.confirmed_total_cavities, cavities_basis: group.snapshot.cavities_basis,
      material_evidence: group.snapshot.material_evidence, requirements_review: group.snapshot.requirements_review })
    form.setFieldsValue({ paper_width_mm: (group.snapshot.template as { width_mm?: number }).width_mm, paper_height_mm: (group.snapshot.template as { height_mm?: number }).height_mm })
    setPreview(undefined); message.info('已载入上方表单；修改后点击“保存修改到当前准备组”，不会另建组')
  }
  return <div className="page-container flow-cards-page">
    <PageTitle title="工序流转卡" description="一包产品的记录与身份；不另记产量、库存和交付。" extra={<Button onClick={() => navigate(safeBusinessReturn(params.get('return_to'), '/orders'))}>返回来源页面</Button>} />
    <CardLookup />
    <FlowTemplatePanel standardId={selectedStandard} />
    {!scope.size ? <Alert type="info" title="扫描内部卡查看记录；制卡请从订单项次或生产任务进入" /> : context.isError ? <Alert type="error" title="来源读取失败，不猜配产品" description={context.error.message} action={<Button onClick={() => void context.refetch()}>重试</Button>} /> : !data ? <Skeleton active /> : <>
      {!data.write_enabled && <Alert type="info" title="当前只读试点；正式站尚未启用制卡写入" />}
      <Card title={`${data.product.specification} / ${data.product.material}`} extra={<Button disabled={disabled} onClick={() => {
        standardForm.resetFields()
        const chosen = data.standards.find((s) => s.id === selectedStandard)
        standardForm.setFieldsValue({ appearance_ids: (chosen?.snapshot.appearance || data.appearance.slice(0, 4)).map((a) => a.id), whole_mold: chosen?.whole_mold || 'UNKNOWN', bake: chosen?.snapshot.bake || { state: 'UNKNOWN' },
          hardness: chosen?.snapshot.hardness, dimension_count: chosen?.snapshot.dimension_count,
          criteria: chosen?.snapshot.criteria.map((c) => ({ criterion_id: c.id, stage: c.stage, method: c.method, numeric: c.numeric })) })
        setStandardOpen(true)
      }}>维护产品确认标准</Button>}>
        <Typography.Paragraph>来源：{data.run_id ? `生产任务 #${data.run_id}` : '制卡准备（不会创建生产任务）'} · {data.orders.map((o) => `${o.order_no}/${o.item_no || '-'}`).join('、') || '尚未选择订单项次，只维护产品要求'}</Typography.Paragraph>
        <Alert type="info" title="计划关联不等于实物已生产；到料不等于实际用料" />
        {data.existing_groups.length > 0 && <><Typography.Title level={5}>已有卡组 · 请先查看，避免整单重复制卡</Typography.Title><Space wrap>{data.existing_groups.map((g) => <Button key={g.id} onClick={() => { setGroup(g); setSelected([]); setCardPage(1) }}>{g.id.slice(0, 8)} · {g.planned_molds ?? '未确认'}模 · {g.status === 'GENERATED' ? '已制卡' : '准备组'}</Button>)}</Space></>}
        <details><summary>查找全部卡组（含更早记录）</summary><Input.Search aria-label="查找全部卡组" placeholder="卡号、订单号或明确计划范围" onSearch={(v) => { setGroupSearch(v.trim()); setGroupListPage(1) }} />{allGroups.isError ? <Alert type="error" title="卡组读取失败，不代表没有记录" /> : allGroups.isPending ? <Skeleton active /> : <><Space wrap>{allGroups.data.results.map((g) => <Button key={g.id} onClick={() => { setGroup(g); setCardPage(1); setSelected([]) }}>{g.id.slice(0, 8)} · {g.plan_basis}</Button>)}</Space><Pagination current={groupListPage} pageSize={20} total={allGroups.data.count} showSizeChanger={false} onChange={setGroupListPage} /></>}</details>
      </Card>
      {data.order_ids.length > 0 && <Card title="明确本次范围 / 追加新卡（原序号不变）">
        <Form name="flow-group" form={form} layout="vertical" onValuesChange={() => setPreview(undefined)}>
          {Boolean(data.requirement_warnings?.length) && <Alert type="warning" title="检验要求需核对" description={data.requirement_warnings?.join('；')} />}
          <div className="flow-form-grid">
            <Form.Item name="planned_molds" label="本次确认制卡模数（空白仅存准备组）"><InputNumber min={1} precision={0} /></Form.Item>
            <Form.Item name="molds_per_card" label="每卡计划模数" rules={[{ required: true }]}><InputNumber min={1} precision={0} /></Form.Item>
            <Form.Item name="standard_id" label="已确认要求版本（多个候选请核对来源）"><Select allowClear options={data.standards.map((s) => ({ value: s.id, label: `版本${s.version} · ${s.source}` }))} onChange={(id) => { const s = data.standards.find((v) => v.id === id); if (s) form.setFieldsValue({ whole_mold: s.whole_mold, appearance_ids: (s.snapshot.appearance || data.appearance.slice(0, 4)).map((a) => a.id) }) }} /></Form.Item>
            <Form.Item name="appearance_ids" label="本组四项外观问题" rules={[{ required: true }, { type: 'array', len: 4, message: '请选择四项' }]}><Select mode="multiple" options={data.appearance.map((a) => ({ value: a.id, label: a.name }))} /></Form.Item>
            <Form.Item name="whole_mold" label="本组整模工序" rules={[{ required: true }]}><Select options={['UNKNOWN', 'REQUIRED', 'NOT_REQUIRED'].map((v) => ({ value: v, label: WHOLE[v] }))} /></Form.Item>
            <Form.Item name="whole_mold_basis" label="整组选择依据（正式制卡必填）"><Input placeholder="已核对本产品现场需要/不需要" /></Form.Item>
            <Form.Item name="diagram_id" label="定点图（可空，不猜另一副实物模具）"><Select allowClear options={data.diagrams.map((d) => ({ value: d.id, label: `图版本${d.version} · ${d.direction}` }))} /></Form.Item>
            <Form.Item name="confirmed_total_cavities" label="本次已核对总孔数（可选，不是有效孔数）"><InputNumber min={1} precision={0} /></Form.Item>
            <Form.Item name="cavities_basis" label="总孔数核对依据"><Input /></Form.Item>
            <Form.Item name="paper_width_mm" label="实际纸宽mm（默认190）"><InputNumber min={180} max={220} precision={0} /></Form.Item><Form.Item name="paper_height_mm" label="实际纸高mm（默认130）"><InputNumber min={125} max={165} precision={0} /></Form.Item>
          </div>
          <Form.Item name="plan_basis" label="本次计划范围及来源" rules={[{ required: true }]}><Input placeholder="例：沿用任务确认计划；或追加第…模至第…模，不重复已有范围" /></Form.Item>
          {data.existing_groups.length > 0 && <Form.Item name="append_confirmed" valuePropName="checked"><Checkbox>已查看已有卡组，确认本次是明确范围的新准备/追加，不是重打原卡</Checkbox></Form.Item>}
          <Form.Item name="requirements_review" label="有差异/订单专用要求时：本组采用依据（无冲突可空）"><Input placeholder="核对原要求与所选版本，说明本次采用哪份要求及原因" /></Form.Item>
          <Form.Item name="material_evidence" label="已知实际用料依据（可空，不把到料当实际投料）"><Input /></Form.Item>
        </Form>
        <Space wrap><Button disabled={disabled} loading={busy} onClick={() => void act(async () => setPreview(await flowApi.preview(await payload())))}>预览分卡（不保存）</Button><Button disabled={disabled} loading={busy} onClick={() => void save(false)}>保存准备组</Button><Button disabled={disabled} type="primary" loading={busy} onClick={() => void save(true)}>确认生成本次独立卡</Button></Space>
        {group?.status === 'DRAFT' && <Button disabled={disabled} loading={busy} onClick={() => void act(async () => { const body = await payload(); const slot = `revise:${group.id}`; setGroup(await flowApi.revise(group.id, { ...body, request_key: flowRequestKey(slot, body) })); finishFlowRequest(slot); void context.refetch() })}>保存修改到当前准备组 {group.id.slice(0, 8)}（不新建）</Button>}
        {preview && <Alert style={{ marginTop: 12 }} type="info" title={`${preview.count}张；尾卡 ${preview.tail_molds ?? '未确认'}模。仅预览，尚无有效包身份。`} />}
      </Card>}
      {error && <Alert type="error" showIcon title="操作未完成，请核对原请求结果后重试" description={error} />}
      {group && <Card title={`卡组 ${group.id.slice(0, 8)} · ${group.status === 'GENERATED' ? '已生成（不代表已生产）' : '准备组'}`}>
        <Typography.Paragraph>计划 {group.planned_molds ?? '未确认'} 模；{group.plan_basis}；{WHOLE[group.whole_mold]}。请求号：<Typography.Text copyable>{group.request_key}</Typography.Text></Typography.Paragraph>
        {group.status === 'DRAFT' ? <Space wrap><Button disabled={disabled} onClick={loadDraft}>载入此准备组到上方表单修改</Button><Button disabled={disabled} loading={busy} onClick={() => void act(async () => setGroup(await flowApi.generate(group.id)))}>确认生成此准备组</Button></Space> : cards.isError ? <Alert type="error" title="卡列表读取失败" /> : <>
          <Table size="small" rowKey="id" dataSource={cards.data?.results} loading={cards.isPending} pagination={false} scroll={{ x: 600 }} rowSelection={{ selectedRowKeys: selected, onChange: (keys) => setSelected(keys.map(String)) }} columns={[
            { title: '独立卡号', dataIndex: 'card_no', render: (v, c) => <Button type="link" onClick={() => navigate(`/flow-cards/cards/${c.id}?return_to=${encodeURIComponent(location.pathname+location.search)}`)}>{v}</Button> },
            { title: '计划（非实际）', render: (_, c) => `${c.planned_molds}M | ${c.sequence}/${c.denominator}批` },
            { title: '补打', dataIndex: 'reprint_count' },
          ]} />
          <Pagination current={cardPage} total={cards.data?.count} pageSize={100} showSizeChanger={false} onChange={(p) => { setCardPage(p); setSelected([]) }} />
          <Button disabled={disabled || !selected.length} loading={busy} onClick={() => void act(async () => { const body = { card_ids: selected }; setJob(await flowApi.print({ ...body, request_key: flowRequestKey(`print:${group.id}`, body) })); finishFlowRequest(`print:${group.id}`) })}>生成选中卡PDF / 原卡补打（最多100张）</Button>
        </>}
      </Card>}
      {job && <PrintResults job={job} changed={setJob} disabled={disabled} />}
      <Modal title="确认产品要求（新版本，不改旧卡）" open={standardOpen} width={880} onCancel={() => setStandardOpen(false)} confirmLoading={busy} okButtonProps={{ disabled }} onOk={() => void act(async () => { const values = await standardForm.validateFields(); const body = { ...values, product_id: data.product.id, mold_id: data.mold_id }; await flowApi.standard({ ...body, request_key: flowRequestKey(`standard:${data.product.id}`, body) }); finishFlowRequest(`standard:${data.product.id}`); setStandardOpen(false); void context.refetch(); message.success('已保存确认版本；新要求进入原产品检验项目资料') })}>
        <Alert type="info" title="复用原产品检验项目；上下模、成型硬度和烤后硬度分别确认，不从材质猜限值。" />
        <Typography.Paragraph>原产品参考（未自动解析/确认）：一次硫化 {data.raw_process_reference?.primary_curing || '未登记'}；二烤 {data.raw_process_reference?.secondary_curing || '未登记'}。秒/min等歧义须核对来源，不会自动当实际执行。</Typography.Paragraph>
        <Form name="flow-standard" form={standardForm} layout="vertical">
          <Form.Item name="source" label="已核对来源（不能只写导入成功）" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="scope" label="适用范围 / 条件"><Input /></Form.Item>
          <Form.Item name="whole_mold" label="整模默认"><Select options={['UNKNOWN', 'REQUIRED', 'NOT_REQUIRED'].map((v) => ({ value: v, label: WHOLE[v] }))} /></Form.Item>
          <Form.Item name="dimension_count" label="需要测量的关键尺寸项目总数（0=确认不适用，空白=待确认）"><InputNumber min={0} max={30} precision={0} /></Form.Item>
          <details><summary>硬度测法：配置一次，卡上实测仍只填数字</summary>{[['MOLDING_HARDNESS', '成型后'], ['BAKED_HARDNESS', '二次加硫后']].map(([stage, label]) => <Card key={stage} size="small" title={label}>
            <Form.Item name={['hardness', stage, 'object']} label="默认测量对象"><Select options={[{ value: 'UNKNOWN', label: '待确认（不按尺寸猜）' }, { value: 'PRODUCT', label: '成品' }, { value: 'COUPON', label: '本厂当批胶料自制胶片' }]} /></Form.Item>
            <div className="flow-form-grid">{[['scale', '已确认量表（未知留空，不默认A）'], ['instrument', '手持硬度计型号/编号（未知可空）'], ['points', '适用测点'], ['specimen', '试样/制样要求'], ['conditioning', '冷却/调节条件'], ['reading_time', '接触后读数时间'], ['aggregation', '结果取值方式'], ['accepted_method', '客户认可方法及依据']].map(([key, title]) => <Form.Item key={key} name={['hardness', stage, key]} label={title}><Input maxLength={500} /></Form.Item>)}</div>
          </Card>)}</details>
          <Form.Item name="appearance_ids" label="四项外观问题（旧卡保留当时名称）" rules={[{ required: true }]}><Select mode="multiple" options={data.appearance.map((a) => ({ value: a.id, label: a.name }))} /></Form.Item>
          <Form.Item name={['bake', 'state']} label="二烤建议适用性"><Select options={[{ value: 'UNKNOWN', label: '待确认' }, { value: 'NOT_APPLICABLE', label: '不适用' }, { value: 'APPLICABLE', label: '适用（确认下列温度和时间）' }]} /></Form.Item>
          <div className="flow-form-grid">{[['temperature_lower', '建议温度下限 ℃'], ['temperature_upper', '建议温度上限 ℃'], ['minutes_lower', '建议时间下限 min'], ['minutes_upper', '建议时间上限 min']].map(([key, label]) => <Form.Item key={key} name={['bake', key]} label={label}><InputNumber min={0} /></Form.Item>)}</div>
          <Form.List name="criteria">{(fields, { add, remove }) => <>{fields.map((f) => <Card size="small" key={f.key} extra={<Button onClick={() => remove(f.name)}>移除此项</Button>}>
            <div className="flow-form-grid">
              <Form.Item name={[f.name, 'stage']} label="测量阶段" rules={[{ required: true }]}><Select options={STAGES} /></Form.Item>
              <Form.Item name={[f.name, 'criterion_id']} label="复用已有检验项目（没有则下方填写新增）"><Select allowClear options={data.criteria.map((c) => ({ value: c.id, label: `#${c.id} ${c.inspection_item} ${c.lower_limit}~${c.upper_limit}${c.unit}` }))} /></Form.Item>
              <Form.Item name={[f.name, 'name']} label="新增项目名称"><Input /></Form.Item>
              <Form.Item name={[f.name, 'lower']} label="下限/建议原文"><Input /></Form.Item>
              <Form.Item name={[f.name, 'upper']} label="上限"><Input /></Form.Item>
              <Form.Item name={[f.name, 'unit']} label="单位/硬度量表"><Input /></Form.Item>
              <Form.Item name={[f.name, 'method']} label="确认的测量方法" rules={[{ required: true }]}><Input /></Form.Item>
              <Form.Item name={[f.name, 'numeric']} valuePropName="checked"><Checkbox>明确数值上下限，可自动对照</Checkbox></Form.Item>
            </div>
          </Card>)}<Button onClick={() => add()}>增加要求（超过两项尺寸会附续页）</Button></>}</Form.List>
        </Form>
        <details><summary>外观问题库维护（不改旧卡名称）</summary><Space wrap><Input placeholder="新增外观问题名称" value={appearanceName} onChange={(e) => setAppearanceName(e.target.value)} /><Button disabled={disabled || !appearanceName.trim()} onClick={() => void act(async () => { await flowApi.appearance({ name: appearanceName }); setAppearanceName(''); void context.refetch() })}>增加问题</Button></Space><Typography.Paragraph>停用只影响后续选项；旧卡快照保留。</Typography.Paragraph>{data.appearance.map((a) => <Space key={a.id} wrap><Input defaultValue={a.name} aria-label={`外观问题${a.id}`} onBlur={(e) => { if (e.target.value.trim() && e.target.value !== a.name) void act(async () => { await flowApi.appearance({ id: a.id, name: e.target.value }); void context.refetch() }) }} disabled={disabled} /><Button disabled={disabled} onClick={() => void act(async () => { await flowApi.appearance({ id: a.id, name: a.name, is_active: false }); void context.refetch() })}>停用</Button></Space>)}</details>
        <details><summary>可选：本次实际模具的已标记定点图（独立确认版本）</summary>
          {!data.mold_id ? <Alert type="info" title="请从已明确实物模具的生产任务进入；不能只按型号把图套给另一副模具。" /> : <Space orientation="vertical" style={{ width: '100%' }}>
            <Typography.Text>适用实物模具 #{data.mold_id}。上传已标记照片，原图保留；不自动识孔。</Typography.Text>
            <Upload accept="image/jpeg,image/png,image/webp" showUploadList={false} beforeUpload={(file) => { void act(async () => { const body = new FormData(); body.set('file', file); setDiagramImage((await flowApi.upload(body)).id) }); return false }}><Button disabled={disabled} loading={busy}>{diagramImage ? '原图已上传，可更换新图' : '上传已标记原图'}</Button></Upload>
            <Input aria-label="定点图方向基准" placeholder="方向/基准，如正面箭头朝上" value={diagramDirection} onChange={(e) => setDiagramDirection(e.target.value)} />
            <Input aria-label="定点图适用范围" placeholder="适用范围与条件" value={diagramScope} onChange={(e) => setDiagramScope(e.target.value)} />
            <Input aria-label="定点图位置定义" placeholder="固定剔除位置及数量定义，不从孔数差额推算" value={diagramPositions} onChange={(e) => setDiagramPositions(e.target.value)} />
            <Button disabled={disabled || !diagramImage || !diagramDirection || !diagramScope || !diagramPositions} loading={busy} onClick={() => void act(async () => { await flowApi.diagram({ product_id: data.product.id, mold_id: data.mold_id, attachment_id: diagramImage, direction: diagramDirection, scope: diagramScope, positions: diagramPositions }); setDiagramImage(undefined); void context.refetch(); message.success('已确认新图版本；旧卡不变，新卡可选择') })}>确认此图版本（不改旧图）</Button>
          </Space>}
        </details>
      </Modal>
    </>}
  </div>
}

export function FlowCardDetailPage() {
  const { id = '' } = useParams()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const readOnly = useReadOnly()
  const { message } = App.useApp()
  const [form] = Form.useForm()
  const [recordOpen, setRecordOpen] = useState(false)
  const [tab, setTab] = useState('events')
  const [page, setPage] = useState(1)
  const [job, setJob] = useState<PrintJob>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const card = useQuery({ queryKey: ['flow-card', id], queryFn: () => flowApi.card(id) })
  const events = useQuery({ queryKey: ['flow-events', id, page], queryFn: () => flowApi.events(id, page), enabled: tab === 'events' })
  const photos = useQuery({ queryKey: ['flow-photos', id, page], queryFn: () => flowApi.attachments(id, page), enabled: tab === 'photos' })
  const trace = useQuery({ queryKey: ['flow-trace', id, page], queryFn: () => flowApi.trace(id, page), enabled: tab === 'trace' })
  const prints = useQuery({ queryKey: ['flow-prints', id, page], queryFn: () => flowApi.prints(id, page), enabled: tab === 'prints' })
  const employees = useQuery({ queryKey: ['flow-employees'], queryFn: async () => toList(await qualityApi.listEmployees({ active: true })), enabled: recordOpen })
  const diagram = useQuery({ queryKey: ['flow-diagram', card.data?.snapshot.diagram_id], queryFn: () => flowApi.diagramDetail(card.data!.snapshot.diagram_id!), enabled: tab === 'diagram' && Boolean(card.data?.snapshot.diagram_id) })
  const kind = Form.useWatch('kind', form)
  const measurementObject = Form.useWatch('measurement_object', form)
  const data = card.data
  const disabled = readOnly || data?.write_enabled === false
  const act = async (work: () => Promise<void>) => { setBusy(true); setError(''); try { await work() } catch (e) { setError((e as Error).message) } finally { setBusy(false) } }
  const record = () => act(async () => {
    const v = await form.validateFields()
    const values: Record<string, unknown> = { note: v.note }
    if (v.kind === 'WHOLE_MOLD') Object.assign(values, { status: v.status, fixed_positions: v.fixed_positions ?? null, coverage: v.coverage || 'UNKNOWN', whole_package_confirmed: v.whole_package_confirmed === true })
    if (v.kind === 'SOURCE_CONFIRM') Object.assign(values, { confirmed: v.source_confirmed === true, actual_run_id: v.actual_run_id || null })
    if (v.kind === 'RESOLUTION') Object.assign(values, { anomaly_id: v.anomaly_id, result: v.note, resolved: v.resolved === true })
    if (STAGES.some((s) => s.value === v.kind)) Object.assign(values, { state: v.state, actual: v.actual, criterion_id: v.criterion_id, unit: v.unit, method: v.method })
    if (['MOLDING_HARDNESS', 'BAKED_HARDNESS'].includes(v.kind)) Object.assign(values, { measurement_object: v.measurement_object || 'UNKNOWN', object_change_reason: v.object_change_reason, sample_ref: v.sample_ref, material_ref: v.material_ref, bake_event_id: v.bake_event_id })
    const cardIds = [id]
    for (const code of (v.shared_cards || '').split(/\n/).map((s: string) => s.trim()).filter(Boolean)) cardIds.push((await flowApi.scan(code)).id)
    const body = { card_ids: [...new Set(cardIds)], kind: v.kind, values, evidence: v.evidence, scope: v.scope, employee_id: v.employee_id || null, business_at: v.business_at?.toISOString() || null, replaces_id: v.replaces_id || null }
    await flowApi.record({ ...body, request_key: flowRequestKey(`event:${id}`, body) }); finishFlowRequest(`event:${id}`)
    setRecordOpen(false); form.resetFields(); void card.refetch(); void events.refetch(); message.success('已保存原记录，不改变数量账')
  })
  return <div className="page-container flow-cards-page">
    <PageTitle title="工序卡详情" description="实物来源、工序事实与客户交付分别查看。" extra={<Space wrap><Button onClick={() => navigate(safeBusinessReturn(params.get('return_to'), '/production'))}>返回来源</Button><Button onClick={() => void card.refetch()}>刷新</Button></Space>} />
    {card.isError ? <Alert type="error" title="内部卡读取失败，不视为不存在的客户卡" description={card.error.message} /> : !data ? <Skeleton active /> : <>
      <Card title={data.card_no} extra={<Button disabled={disabled} onClick={() => { form.resetFields(); form.setFieldsValue({ kind: 'WHOLE_MOLD', status: 'PARTIAL', state: 'UNKNOWN' }); setRecordOpen(true) }}>登记 / 续记 / 复测</Button>}>
        <Descriptions column={{ xs: 1, sm: 2 }} items={[
          { key: 'product', label: '产品', children: `${data.snapshot.product.specification} / ${data.snapshot.product.material}` },
          { key: 'plan', label: '原计划', children: `${data.planned_molds}M | ${data.sequence}/${data.denominator}批 · ${data.original_date}` },
          { key: 'source', label: '实物来源', children: data.source_status === 'PLAN_ONLY' ? '仅计划关联，实物未在系统核对' : '已登记实物来源核对（查看原记录依据）' },
          { key: 'whole', label: '整模登记', children: WHOLE[data.whole_mold_status] || data.whole_mold_status },
          { key: 'cavities', label: '有效孔数快照', children: data.snapshot.effective_cavities ?? '未确认；不猜孔数' },
          { key: 'anomaly', label: '未解决异常', children: data.unresolved_anomalies },
        ]} />
        <Space wrap>{data.snapshot.orders.map((o) => <Button key={o.id} href={`/order-overview/${o.id}?return_to=${encodeURIComponent(location.pathname)}`}>{o.order_no}/{o.item_no || '-'}</Button>)}{data.snapshot.run_id && <Button href={`/production/records/${data.snapshot.run_id}?return_to=${encodeURIComponent(location.pathname)}`}>原生产任务</Button>}</Space>
        <Alert type="info" title="定点不自动扣孔；签名/照片/出货不代表其他工序已完成" description={data.paper_status} />
        {data.whole_mold_status === 'SYSTEM_UNREGISTERED' && <Alert type="warning" title="需整模 · 系统未登记；请核对纸卡，不认定现场未做" />}
        {data.unresolved_anomalies > 0 && <Alert type="warning" title={`异常待处理 ${data.unresolved_anomalies} 条；出货不会自动关闭异常`} />}
        <FlowBakePanel card={data} disabled={disabled} changed={() => { void card.refetch(); void events.refetch() }} />
      </Card>
      {error && <Alert type="error" title="操作失败或结果需核对" description={error} />}
      <Card>
        <Space wrap>{[['events', '工序/异常原记录'], ['photos', '原图归档'], ['trace', '出货/退回追溯'], ['diagram', '当时定点图'], ['prints', '原打印任务']].map(([value, label]) => <Button key={value} type={tab === value ? 'primary' : 'default'} onClick={() => { setTab(value); setPage(1) }}>{label}</Button>)}</Space>
        {tab === 'events' && (events.isError ? <Alert type="error" title="原记录读取失败，不显示0/正常" /> : events.isPending ? <Skeleton active /> : <><Typography.Paragraph>未登记仅表示系统无数字记录，不认定现场未做。实际0保留；更正/复测不覆盖原值。</Typography.Paragraph>{!events.data.results.length && <Empty description="系统未登记 / 纸面待核对" />}{events.data.results.map((e) => <Card size="small" key={e.id} title={`${e.kind} · ${e.employee_name || '实际人员未登记'}`}><div>业务时间：{e.business_at || '未知'} · 录入：{e.created_at}</div><div>范围：{e.scope}；依据：{e.evidence}</div><pre>{JSON.stringify(e.values, null, 2)}</pre>{e.replaces_id && <Tag>续记/复测原记录 {e.replaces_id}</Tag>}</Card>)}<Pagination current={page} total={events.data.count} pageSize={20} onChange={setPage} /></>)}
        {tab === 'photos' && <>
          <Upload accept="image/jpeg,image/png,image/webp" showUploadList={false} disabled={disabled || busy} beforeUpload={(file) => { void act(async () => { const body = new FormData(); body.set('card_id', id); body.set('file', file); await flowApi.upload(body); void photos.refetch() }); return false }}><Button disabled={disabled} loading={busy}>拍照 / 上传原图（不自动判完成）</Button></Upload>
          {photos.isError ? <Alert type="error" title="原图列表读取失败" /> : photos.data?.results.map((p) => <div key={p.id}><Button href={p.url} target="_blank">查看原图 · {p.original_name}</Button><Typography.Text copyable>{p.id}</Typography.Text>{p.replaces_id && <Tag>补拍替换原图 {p.replaces_id}（原件保留）</Tag>}<Upload accept="image/jpeg,image/png,image/webp" showUploadList={false} disabled={disabled || busy} beforeUpload={(file) => { void act(async () => { const body = new FormData(); body.set('card_id', id); body.set('replaces_id', p.id); body.set('file', file); await flowApi.upload(body); void photos.refetch() }); return false }}><Button disabled={disabled}>补拍 / 替换此原图（留旧件）</Button></Upload></div>)}<Pagination current={page} total={photos.data?.count} pageSize={20} onChange={setPage} />
        </>}
        {tab === 'trace' && (trace.isError ? <Alert type="error" title="追溯读取失败" /> : trace.isPending ? <Skeleton active /> : <><Typography.Paragraph>源集合未确认份额时，不把每张卡乘一遍出货/退回量。原出货更正后保留快照，需重新核对；物流事件不自动生成返工作业。</Typography.Paragraph>{!trace.data.results.length && <Empty description="未建立明确出货追溯；不猜填历史关系" />}{trace.data.results.map((s) => <Card key={s.id} size="small" title={s.shipment_no}><Button href={`/quality?shipment_batch_id=${s.batch_id}`}>查看原出货</Button><Tag>{s.current_shipment_status}</Tag><Tag>{SOURCE_STATE[s.state] || '状态需核对'}</Tag><SourceReturnHistory cardId={id} source={s} /></Card>)}<Pagination current={page} total={trace.data.count} pageSize={20} showSizeChanger={false} onChange={setPage} /></>)}
        {tab === 'diagram' && (!data.snapshot.diagram_id ? <Alert type="info" title="当时未建立定点图，不能声称位置已核对；仍可登记定点数" /> : diagram.isError ? <Alert type="error" title="当时定点图读取失败" /> : diagram.data && <><div>版本{diagram.data.version} · 方向/基准 {diagram.data.direction} · 适用 {diagram.data.scope}</div><Button href={diagram.data.url} target="_blank">查看当时标记图</Button></>)}
        {tab === 'prints' && <>{prints.isError && <Alert type="error" title="原打印任务读取失败" />}{prints.data?.results.map((p) => <Button key={p.id} onClick={() => setJob(p)}>任务 {p.id.slice(0, 8)} · {p.status}</Button>)}<Pagination current={page} total={prints.data?.count} pageSize={20} showSizeChanger={false} onChange={setPage} /><Button disabled={disabled} loading={busy} onClick={() => void act(async () => { const body = { card_ids: [id] }; setJob(await flowApi.print({ ...body, request_key: flowRequestKey(`print:${id}`, body) })); finishFlowRequest(`print:${id}`); void prints.refetch() })}>原卡打印 / 补打（保留当时快照）</Button></>}
      </Card>
      {job && <PrintResults job={job} changed={setJob} disabled={disabled} />}
      <Modal title="真实作业 / 异常同号续记" open={recordOpen} onCancel={() => setRecordOpen(false)} onOk={() => void record()} confirmLoading={busy} okButtonProps={{ disabled }}>
        <Form form={form} layout="vertical">
          <Form.Item name="kind" label="记录类型" rules={[{ required: true }]}><Select options={[{ value: 'WHOLE_MOLD', label: '整模作业' }, { value: 'ANOMALY', label: '异常' }, { value: 'RESOLUTION', label: '原异常处理结果' }, { value: 'SOURCE_CONFIRM', label: '核对实际来源' }, ...STAGES.filter((s) => s.value !== 'SECONDARY_CURE')]} onChange={(stage) => {
            const matches = data.snapshot.standard?.snapshot.criteria.filter((c) => c.stage === stage) || []
            const criterion = matches.length === 1 ? matches[0] : undefined
            const config = data.snapshot.standard?.snapshot.hardness?.[stage]
            form.setFieldsValue({ criterion_id: criterion?.id, unit: criterion?.unit, method: criterion?.method, measurement_object: config?.object || 'UNKNOWN', actual: undefined, sample_ref: undefined, bake_event_id: undefined })
          }} /></Form.Item>
          <Form.Item name="scope" label="本次实际范围/模段（未知则明确写未知）" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="employee_id" label="实际作业人（不取登录人）"><Select loading={employees.isPending} allowClear options={employees.data?.map((e) => ({ value: e.id, label: e.name }))} /></Form.Item>
          <Form.Item name="business_at" label="实际业务时间（有依据再填）"><DatePicker showTime format="YYYY-MM-DD HH:mm" classNames={{ popup: { root: 'flow-datetime-popup' } }} style={{ width: '100%' }} /></Form.Item>
          {kind === 'WHOLE_MOLD' && <>
            <Form.Item name="status" label="整模进度"><Select options={['PARTIAL', 'COMPLETED', 'EXCEPTION'].map((v) => ({ value: v, label: WHOLE[v] }))} /></Form.Item>
            <Form.Item name="coverage" label="实际覆盖范围"><Select options={[{ value: 'UNKNOWN', label: '范围未知' }, { value: 'PARTIAL', label: '明确部分模段（在范围栏说明）' }, { value: 'WHOLE', label: '本卡整包' }]} /></Form.Item>
            <Form.Item name="fixed_positions" label="定点 孔/模（空白未知；0是实际0）"><InputNumber min={0} precision={0} /></Form.Item>
            <Form.Item name="whole_package_confirmed" valuePropName="checked"><Checkbox>确认本卡整包约定作业确已完成（不是仅抽看一两模）</Checkbox></Form.Item>
          </>}
          {kind === 'SOURCE_CONFIRM' && <><Form.Item name="source_confirmed" valuePropName="checked"><Checkbox>已按实物及原记录核对来源（不是仅打印/计划）</Checkbox></Form.Item><Form.Item name="actual_run_id" label="实际来源生产任务ID（可空；不按相近日期猜）"><InputNumber min={1} precision={0} /></Form.Item></>}
          {kind === 'RESOLUTION' && <><Form.Item name="anomaly_id" label="原异常记录ID" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name="resolved" valuePropName="checked"><Checkbox>实际处理完成并确认原异常已解决</Checkbox></Form.Item></>}
          {STAGES.some((s) => s.value === kind) && <>
            <Form.Item name="shared_cards" label="同一次真实检测覆盖的其他卡号（每行一个，可空，不按材质自动扩展）"><Input.TextArea /></Form.Item>
            <Form.Item name="state" label="值的状态"><Select options={[{ value: 'UNKNOWN', label: '未知' }, { value: 'NOT_MEASURED', label: '未测' }, { value: 'NOT_APPLICABLE', label: '不适用' }, { value: 'MEASURED', label: '实测（允许0）' }]} /></Form.Item>
            <Form.Item name="criterion_id" label="当时要求"><Select allowClear options={data.snapshot.standard?.snapshot.criteria.filter((c) => c.stage === kind).map((c) => ({ value: c.id, label: `${c.name} ${c.lower}~${c.upper}${c.unit}` }))} onChange={(v) => { const c = data.snapshot.standard?.snapshot.criteria.find((c) => c.id === v); if (c) form.setFieldsValue({ unit: c.unit, method: c.method }) }} /></Form.Item>
            <Form.Item name="actual" label="实际值"><InputNumber /></Form.Item><Form.Item name="unit" label="实际单位/量表"><Input /></Form.Item><Form.Item name="method" label="实际测量方法"><Input /></Form.Item>
            {['MOLDING_HARDNESS', 'BAKED_HARDNESS'].includes(kind) && <><Form.Item name="measurement_object" label="本次实际测量对象"><Select options={[{ value: 'UNKNOWN', label: '待确认（不自动判合格）' }, { value: 'PRODUCT', label: '成品' }, { value: 'COUPON', label: '胶片' }]} /></Form.Item><Form.Item name="object_change_reason" label="与当时默认测量对象不同的原因（变化时必填）"><Input /></Form.Item>{measurementObject === 'COUPON' && <><Form.Item name="sample_ref" label="本厂自制胶片原始标识"><Input /></Form.Item><Form.Item name="material_ref" label="当批胶料原始标识"><Input /></Form.Item>{kind === 'BAKED_HARDNESS' && <Form.Item name="bake_event_id" label="实际随烤记录ID（见工序原记录，不抄建议）"><Input /></Form.Item>}</>}</>}
          </>}
          <Form.Item name="note" label="变化位置、处理内容等"><Input.TextArea /></Form.Item>
          <Form.Item name="evidence" label="确认依据 / 异常说明" rules={[{ required: true }]}><Input.TextArea /></Form.Item>
          <Form.Item name="replaces_id" label="更正/复测关联原记录ID（可空，原值不会被删）"><Input /></Form.Item>
        </Form>
      </Modal>
    </>}
  </div>
}
