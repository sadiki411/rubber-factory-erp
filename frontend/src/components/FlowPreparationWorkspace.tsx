import { Alert, App, Button, Card, Checkbox, Descriptions, Form, Input, InputNumber, Modal, Pagination, Select, Space, Table, Typography } from 'antd'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useReadOnly } from '../access'
import { flowApi, flowLimitText, flowRequestKey, finishFlowRequest, type PreparationContext, type PreparationGroup, type PreparationStandard, type PreparationSource, type FlowGroup, type PrintJob } from '../flowcards'

const STATE = { LEGACY: '旧记录·待复核适用性', DRAFT: '草稿', ACTIVE: '生效', SUPERSEDED: '已被替代' }
const STAGES = [{value: 'UPPER_TEMP', label: '上模接触实测'}, {value: 'LOWER_TEMP', label: '下模接触实测'}, {value: 'MOLDING_HARDNESS', label: '成型硬度'}, {value: 'BAKED_HARDNESS', label: '烤后硬度'}, {value: 'DIMENSION', label: '关键尺寸'}]
const PARAMETERS = [
  ['upper_set_temperature', '上模设定温度'], ['lower_set_temperature', '下模设定温度'],
  ['upper_contact_temperature', '上模接触温度目标'], ['lower_contact_temperature', '下模接触温度目标'],
  ['primary_curing_time', '一次加硫时间'], ['pressure', '压力（来源注明类别）'], ['venting', '排气'],
  ['arrangement', '排列/排料'], ['release_agent', '离型剂'], ['machine_type', '机型/吨位'], ['oil', '毛边洒油'], ['experience', '异常经验'],
]
const sourceText = (c: PreparationSource) => `${c.order_id ? `订单项#${c.order_id}` : '产品通用'} · ${c.source_file || '人工资料'} · ${c.source_sheet || '未登记表'} ${c.source_row == null ? '' : `第${c.source_row}行`} · ${c.version || '原版号未登记'}`
const sourceLink = (c: PreparationSource) => c.source_url ? <a href={c.source_url} target="_blank" rel="noopener noreferrer">{sourceText(c)} · 查看原件</a> : sourceText(c)
const parameterValues = (values: Record<string, Record<string, unknown>> = {}) => Object.fromEntries(Object.entries(values).filter(([,v])=>Object.values(v).some((item)=>item!==undefined&&item!==''&&item!==null)))
const groupedSources = (sources:PreparationSource[]) => {
  const groups=new Map<string,PreparationSource&{evidence:PreparationSource[]}>()
  for(const row of sources){
    const key=JSON.stringify([row.inspection_item,row.stage,row.method,row.lower_limit,row.upper_limit,row.unit,row.limit_mode,row.lower_inclusive,row.upper_inclusive])
    const found=groups.get(key)
    if(found) found.evidence.push(row)
    else groups.set(key,{...row,evidence:[row]})
  }
  return [...groups.values()]
}

function StandardEditor({ data, open, close, changed }: { data: PreparationContext; open: boolean; close: () => void; changed: () => void }) {
  const [form] = Form.useForm()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const { message } = App.useApp()
  const [saved, setSaved] = useState<PreparationStandard>()
  const [sourcePage, setSourcePage] = useState(1)
  const sourceParams = new URLSearchParams({product_id:String(data.product.id),...(data.run_id?{run_id:String(data.run_id)}:data.order_ids[0]?{order_id:String(data.order_ids[0])}:{})})
  const sourceQuery = useQuery({queryKey:['preparation-sources',sourceParams.toString(),sourcePage],queryFn:()=>flowApi.preparationSources(sourceParams,sourcePage),enabled:(data.criteria_count||0)>50})
  const sources = sourceQuery.data?.results || data.criteria
  const write = data.can_maintain_standard && data.write_enabled
  const reference = Form.useWatch('reference_id', form)
  const fill = (standard?: PreparationStandard) => {
    setSaved(undefined)
    form.resetFields()
    form.setFieldsValue({ customer_key: standard?.customer_key || data.orders[0]?.customer || '', source_kind: standard?.source_kind || 'FACTORY_EXPERIENCE', whole_mold: standard?.whole_mold || 'UNKNOWN',
      source: standard?.source, scope: standard?.scope, applicable_order_id: standard?.applicable_order_id,
      appearance_ids: (standard?.snapshot.appearance || data.appearance.slice(0, 4)).map((a) => a.id), route: standard?.snapshot.route || { confirmed: false, molding: true, patrol: true, deburr: true, inspection: true },
      applicability: (standard?.snapshot as PreparationStandard['snapshot'] & {applicability?: unknown})?.applicability,
      packaging: standard?.snapshot.packaging || { molds_per_card: 100 }, process_parameters: standard?.snapshot.process_parameters,
      bake: standard?.snapshot.bake || { state: 'UNKNOWN' }, hardness: standard?.snapshot.hardness,
      criteria: standard?.snapshot.criteria.map((c) => ({ ...c, criterion_id: c.id, mode: 'REFERENCE' })) || [] })
  }
  const initial = data.standards.find((s) => s.id === data.auto_standard_id)
  const save = async () => {
    setBusy(true); setError('')
    try {
      const values = await form.validateFields()
      const criteria = (values.criteria || []).map((c: Record<string, unknown>) => {
        if (c.mode === 'REFERENCE') return { criterion_id: c.criterion_id, stage: c.stage, method: c.method, numeric: c.limit_mode === 'RANGE' || c.limit_mode === 'UPPER', requirement_state: c.requirement_state }
        return { ...c, override: Boolean(c.criterion_id), numeric: c.limit_mode === 'RANGE' || c.limit_mode === 'UPPER' }
      })
      const body = { ...values, process_parameters: parameterValues(values.process_parameters), criteria, product_id: data.product.id, mold_id: data.mold_id }
      const slot = `standard-draft:${data.product.id}`
      const result = await flowApi.saveStandard({ ...body, request_key: flowRequestKey(slot, body) })
      setSaved(result); finishFlowRequest(slot); changed(); message.success('已另存产品标准草稿，尚未生效；旧卡不变')
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  return <Modal title="产品工艺与检验标准 · 原始资料不等于生效版" open={open} onCancel={close} width={1050} footer={<Space wrap><Button onClick={close}>关闭</Button><Button disabled={!write} loading={busy} onClick={() => void save()}>另存产品标准草稿</Button>{saved && <Button disabled={!write} loading={busy} onClick={() => {
    setBusy(true); flowApi.activateStandard(saved.id).then(() => { changed(); close(); message.success('已复核生效，仅供以后适用任务采用') }).catch((e: Error) => setError(e.message)).finally(() => setBusy(false))
  }}>复核并生效 v{saved.version}</Button>}</Space>}>
    {!write && <Alert type="info" title="只读查看依据；操作员不能维护全厂标准，可回到工作页保存本次准备。" />}
    <Select style={{width: '100%', marginBlock: 12}} placeholder="查看现有版本（不会落库）" options={data.standards.map((s) => ({value: s.id, label: `v${s.version} · ${STATE[s.status as keyof typeof STATE] || s.status} · ${s.source}`}))} onChange={(id) => fill(data.standards.find((s) => s.id === id))} />
    {error && <Alert type="error" title="尚未完成" description={error} />}
    <Form form={form} layout="vertical" disabled={!write} initialValues={{customer_key:initial?.customer_key||data.orders[0]?.customer||'',source_kind:initial?.source_kind||'FACTORY_EXPERIENCE',source:initial?.source,whole_mold:initial?.whole_mold||'UNKNOWN',appearance_ids:(initial?.snapshot.appearance||data.appearance.slice(0,4)).map((a)=>a.id),route:initial?.snapshot.route||{confirmed:false,molding:true,patrol:true,deburr:true,inspection:true},packaging:initial?.snapshot.packaging||{molds_per_card:100},bake:initial?.snapshot.bake||{state:'UNKNOWN'},hardness:initial?.snapshot.hardness,process_parameters:initial?.snapshot.process_parameters,criteria:initial?.snapshot.criteria.map((c)=>({...c,criterion_id:c.id,mode:'REFERENCE'}))||[]}}>
      <div className="flow-form-grid">
        <Form.Item name="source" label="来源、文件/版号及复核依据" rules={[{required: true}]}><Input /></Form.Item>
        <Form.Item name="source_kind" label="资料性质"><Select options={[{value: 'CUSTOMER', label: '客户要求'}, {value: 'FACTORY_PROCESS', label: '本厂工艺'}, {value: 'FACTORY_EXPERIENCE', label: '本厂经验待核'}]} /></Form.Item>
        <Form.Item name="customer_key" label="适用客户（空=确认通用）"><Input /></Form.Item>
        <Form.Item name="applicable_order_id" label="仅此订单特规（不污染下一单）"><Select allowClear options={data.orders.map((o) => ({value: o.id, label: `${o.order_no}/${o.item_no || '-'}`}))} /></Form.Item>
        <Form.Item name="scope" label="适用条件/版本/模具说明"><Input /></Form.Item>
        <Form.Item name="whole_mold" label="整模工序"><Select options={[{value: 'UNKNOWN', label: '待确认'}, {value: 'REQUIRED', label: '需要整模'}, {value: 'NOT_REQUIRED', label: '确认不需整模'}]} /></Form.Item>
        <Form.Item name={['applicability', 'customer_product_no']} label="适用客户料号"><Input /></Form.Item>
        <Form.Item name={['applicability', 'drawing_no']} label="客户图号"><Input /></Form.Item>
        <Form.Item name={['applicability', 'drawing_revision']} label="客户图纸版次"><Input /></Form.Item>
        <Form.Item name={['applicability', 'station_id']} label="适用机台ID（可空）"><InputNumber min={1} precision={0} /></Form.Item>
        <Form.Item name={['packaging', 'molds_per_card']} label="每包模数M"><InputNumber min={1} precision={0} /></Form.Item>
        <Form.Item name={['packaging', 'patrol_every_molds']} label="巡检频率（模，独立于包装）"><InputNumber min={1} precision={0} /></Form.Item>
      </div>
      <Card size="small" title="本产品适用路线（不预填实测、签名和时间）"><Space wrap>{[['molding', '成型实测'], ['patrol', '巡检'], ['deburr', '毛边'], ['bake', '二烤'], ['baked_hardness', '烤后测硬度'], ['inspection', '品检员']].map(([key,label]) => <Form.Item key={key} name={['route',key]} valuePropName="checked"><Checkbox>{label}</Checkbox></Form.Item>)}</Space><Form.Item name={['route','confirmed']} valuePropName="checked"><Checkbox>已核对本产品路线</Checkbox></Form.Item></Card>
      <details><summary>产品工艺参数（设定与接触温度分开、原单位不转换）</summary>{PARAMETERS.map(([key,label]) => <Card key={key} size="small" title={label}><div className="flow-form-grid"><Form.Item name={['process_parameters',key,'state']} label="状态"><Select allowClear options={[{value: 'NOT_PROVIDED',label: '未提供'}, {value: 'RAW_UNCONFIRMED', label: '原文待核'}, {value: 'NOT_APPLICABLE', label: '确认不适用'}, {value: 'CONFIRMED', label: '已确认'}]} /></Form.Item>{[['lower','下限'],['upper','上限'],['unit','单位'],['raw_text','原文'],['source','出处/类别/方法']].map(([field,name]) => <Form.Item key={field} name={['process_parameters',key,field]} label={name}><Input /></Form.Item>)}</div></Card>)}</details>
      <details><summary>二烤建议与硬度方法（与实际执行记录分开）</summary><Form.Item name={['bake','state']} label="二烤建议状态"><Select options={[{value: 'UNKNOWN',label: '待确认'}, {value:'APPLICABLE',label:'需要'}, {value:'NOT_APPLICABLE',label:'不适用'}]} /></Form.Item><div className="flow-form-grid">{[['temperature_lower','温度下限℃'],['temperature_upper','温度上限℃'],['minutes_lower','时间下限min'],['minutes_upper','时间上限min']].map(([key,label]) => <Form.Item key={key} name={['bake',key]} label={label}><InputNumber min={0} /></Form.Item>)}</div>{[['MOLDING_HARDNESS','成型硬度'],['BAKED_HARDNESS','烤后硬度']].map(([stage,label]) => <Card key={stage} size="small" title={label}><Form.Item name={['hardness',stage,'object']} label="对象"><Select options={[{value:'UNKNOWN',label:'待核'}, {value:'PRODUCT',label:'成品'}, {value:'COUPON',label:'当批胶料自制胶片'}]} /></Form.Item><div className="flow-form-grid">{[['scale','量表'],['instrument','仪器'],['accepted_method','客户认可测法'],['points','测点'],['conditioning','冷却/调节'],['reading_time','读数时间']].map(([key,name]) => <Form.Item key={key} name={['hardness',stage,key]} label={name}><Input /></Form.Item>)}</div></Card>)}</details>
      <Form.Item name="appearance_ids" label="产品四项外观问题" rules={[{required: true},{type:'array',len:4}]}><Select mode="multiple" options={data.appearance.map((a) => ({value:a.id,label:a.name}))} /></Form.Item>
      <Typography.Title level={5}>候选原值与出处（没有生效版也可查看）</Typography.Title>
      {sourceQuery.isError && <Alert type="error" title="候选来源读取失败，不视为没有要求" />}
      <Table size="small" rowKey="id" dataSource={groupedSources(sources)} pagination={false} scroll={{x:700}} columns={[{title:'原项目',dataIndex:'inspection_item'}, {title:'范围/单位',render:(_,c) => `${c.lower_limit || '无下限'}～${c.upper_limit || '无上限'} ${c.unit || '单位待核'}`}, {title:'全部出处（仅合并完全相同项）',render:(_,c) => c.evidence.map((row)=><div key={row.id}>{sourceLink(row)}</div>)}]} />
      {(data.criteria_count||0)>50 && <Pagination current={sourcePage} total={data.criteria_count} pageSize={50} showSizeChanger={false} onChange={(page)=>{setSourcePage(page);form.setFieldValue('reference_id',undefined)}} />}
      <Form.Item name="reference_id" label="从原表候选带入草稿（不会确认或发卡）"><Select allowClear options={sources.map((c) => ({value:c.id,label:`${c.inspection_item} · ${c.lower_limit}～${c.upper_limit} ${c.unit} · ${sourceText(c)}`}))} /></Form.Item>
      <Button disabled={!write || !reference} onClick={() => { const c = sources.find((c) => c.id === reference); if (c) form.setFieldValue('criteria', [...(form.getFieldValue('criteria') || []), {criterion_id:c.id,name:c.inspection_item,lower:c.lower_limit,upper:c.upper_limit,unit:c.unit,stage:c.stage,method:c.method,mode:'REFERENCE',limit_mode:c.limit_mode || 'PENDING',requirement_state:'RAW_UNCONFIRMED',upper_inclusive:c.upper_inclusive!==false,lower_inclusive:c.lower_inclusive!==false}]) }}>带入引用（待核）</Button>
      <Form.List name="criteria">{(fields, {add,remove}) => <>{fields.map((field) => <Card key={field.key} size="small" title={`项目${field.name+1}`} extra={<Button onClick={() => remove(field.name)}>移除此项</Button>}>
        <Form.Item name={[field.name,'criterion_id']} hidden><InputNumber /></Form.Item>
        <Form.Item name={[field.name,'mode']} label="保存方式"><Select options={[{value:'REFERENCE',label:'引用原行（限值不可改）'}, {value:'OVERRIDE',label:'明确新版本/本单例外（另存，不覆盖原行）'}]} /></Form.Item>
        <div className="flow-form-grid">
          <Form.Item name={[field.name,'name']} label="中文项目/原代号"><Input /></Form.Item>
          <Form.Item name={[field.name,'stage']} label="检测阶段" rules={[{required:true}]}><Select options={STAGES} /></Form.Item>
          <Form.Item name={[field.name,'method']} label="测法"><Input /></Form.Item>
          <Form.Item name={[field.name,'requirement_state']} label="要求状态"><Select options={[{value:'RAW_UNCONFIRMED',label:'有原文待核'}, {value:'NOT_PROVIDED',label:'未提供'}, {value:'NOT_APPLICABLE',label:'确认不适用'}, {value:'MEASURE_NO_LIMIT',label:'要测但无界限，不判合格'}, {value:'CONFIRMED',label:'已确认'}]} /></Form.Item>
          <Form.Item noStyle shouldUpdate>{() => { const locked = form.getFieldValue(['criteria',field.name,'mode']) === 'REFERENCE' || !write; return <>
            <Form.Item name={[field.name,'limit_mode']} label="界限模式"><Select disabled={locked} options={[{value:'UPPER',label:'只测上限'}, {value:'RANGE',label:'区间'}, {value:'PENDING',label:'无确认界限'}, {value:'NONE',label:'不适用'}]} /></Form.Item>
            <Form.Item name={[field.name,'upper_inclusive']} valuePropName="checked"><Checkbox disabled={locked}>上限含等号≤（不勾为&lt;）</Checkbox></Form.Item>
            <Form.Item name={[field.name,'lower_inclusive']} valuePropName="checked"><Checkbox disabled={locked}>下限含等号（不勾为&gt;）</Checkbox></Form.Item>
            {([['lower','下限'],['upper','上限'],['unit','单位/量表']] as const).map(([key,label]) => <Form.Item key={key} name={[field.name,key]} label={label}><Input disabled={locked} /></Form.Item>)}
          </> }}</Form.Item>
        </div>
      </Card>)}<Button disabled={!write} onClick={() => add({mode:'OVERRIDE',limit_mode:'PENDING',requirement_state:'RAW_UNCONFIRMED',upper_inclusive:true})}>新增待核项目</Button></>}</Form.List>
    </Form>
  </Modal>
}

export function FlowPreparationWorkspace({ data, refresh, lookup, settings, printResults, print }: { data: PreparationContext; refresh: () => void; lookup: ReactNode; settings: ReactNode; printResults: ReactNode; print: (ids: string[]) => Promise<PrintJob | void> }) {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const readOnly = useReadOnly()
  const [form] = Form.useForm()
  const [group,setGroup] = useState<PreparationGroup | null>(null)
  const [busy,setBusy] = useState(false)
  const [error,setError] = useState('')
  const [editor,setEditor] = useState(false)
  const [preview,setPreview] = useState<{count:number;tail_molds:number|null}>()
  const [cardPage,setCardPage] = useState(1)
  const init = useRef('')
  const contextKey = `${data.product.id}:${data.run_id}:${data.order_ids.join(',')}`
  const cards = useQuery({queryKey:['prepared-cards',group?.id,group?.status,cardPage],queryFn:() => flowApi.cards(group!.id,cardPage),enabled:group?.status==='GENERATED'})
  const disabled = readOnly || !data.write_enabled
  const applyGroup = useCallback((g: PreparationGroup | null) => {
    setGroup(g); setCardPage(1); setPreview(undefined)
    const s = data.standards.find((s) => s.id === (g?.snapshot.standard?.id || data.auto_standard_id))
    form.resetFields()
    const snapshot = g?.snapshot as (FlowGroup['snapshot'] & {decisions?: Record<string,number>;preparation_notes?:string;arrangement?:Record<string,unknown>}) | undefined
    form.setFieldsValue({planned_molds:g?.run_id?g.planned_molds:data.suggested_molds,range_start:g?.range_start ?? data.available_ranges[0]?.[0],molds_per_card:g?.molds_per_card ?? s?.snapshot.packaging?.molds_per_card ?? 100,standard_id:s?.id,whole_mold:g?.whole_mold&&g.whole_mold!=='UNKNOWN'?g.whole_mold:s?.whole_mold||'UNKNOWN',diagram_id:g?.snapshot.diagram_id,decisions:snapshot?.decisions || {},preparation_notes:snapshot?.preparation_notes,process_adjustments:Object.entries(g?.snapshot.process_overrides||{}).map(([name,value])=>({name,...value})),...snapshot?.arrangement})
  }, [data, form])
  useEffect(() => {if(init.current !== contextKey) {init.current=contextKey; queueMicrotask(() => applyGroup(data.preferred_group))}},[contextKey,data,applyGroup])
  const standardId = Form.useWatch('standard_id',form)
  const molds = Form.useWatch('planned_molds',form)
  const per = Form.useWatch('molds_per_card',form)
  const standard = data.standards.find((s) => s.id === (group?.status==='GENERATED'?group.snapshot.standard?.id:standardId))
  const selectedContext = useQuery({queryKey:['selected-preparation-standard',contextKey,standardId],queryFn:async()=>{
    const query=new URLSearchParams({product_id:String(data.product.id),standard_id:String(standardId),...(data.run_id?{run_id:String(data.run_id)}:{order_id:String(data.order_ids[0])})})
    return await flowApi.context(query) as PreparationContext
  },enabled:Boolean(standardId&&standardId!==data.auto_standard_id&&group?.status!=='GENERATED')})
  const currentContext=standardId&&standardId!==data.auto_standard_id?selectedContext.data||data:data
  const count = molds && per ? Math.ceil(molds/per) : null
  const perform = async (work: () => Promise<void>) => {setBusy(true);setError('');try{await work()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
  const payload = async () => {
    const values=await form.validateFields()
    const rows: Array<{name:string}&Record<string,unknown>>=values.process_adjustments||[]
    if(new Set(rows.map((r)=>r.name)).size!==rows.length) throw new Error('本次工艺调整同一参数只能填写一项')
    const {process_adjustments:_,...rest}=values
    void _
    return {...rest,process_overrides:Object.fromEntries(rows.map(({name,...value})=>[name,value])),product_id:data.product.id,order_id:data.order_ids[0],run_id:data.run_id,order_ids:data.order_ids,...(group?.status==='DRAFT'?{group_id:group.id,expected_revision:group.revision}:{})}
  }
  const save = (generate:boolean) => perform(async () => {
    const body = await payload(); const slot=`preparation:${contextKey}:${group?.id||'new'}`
    let result = await flowApi.create({...body,request_key:flowRequestKey(slot,body)}) as PreparationGroup
    setGroup(result); finishFlowRequest(slot)
    if(generate) {
      try { result=await flowApi.generate(result.id,result.revision) as PreparationGroup }
      catch(e) {
        // A lost success response must resolve the same identity before retry.
        const known=await flowApi.group(result.id) as PreparationGroup
        if(known.status!=='GENERATED') throw e
        result=known
      }
    }
    applyGroup(result); refresh()
  })
  const active = data.runs.filter((r)=>!r.historical)
  return <>
    <Card title={`${data.product.specification} / ${data.product.material} · 制卡准备`} extra={<Button onClick={refresh}>刷新服务器资料</Button>}>
      <Typography.Paragraph>{data.orders.map((o)=>`${o.order_no}/${o.item_no||'-'}`).join('、')} · M=模；制卡不报产量、不动库存或交付</Typography.Paragraph>
      <Descriptions title="已带入" column={{xs:1,sm:2}} items={[{key:'run',label:'生产段',children:data.run_id?`任务#${data.run_id} · 已确认计划${data.runs.find((r)=>r.id===data.run_id)?.planned_molds ?? '见原任务'}模`:'尚未安排，不以欠交推算'}, {key:'standard',label:'采用依据',children:standard?`v${standard.version} · ${standard.source}`:'尚无唯一适用生效版'}, {key:'saved',label:'服务器草稿',children:group?`${group.id} · 版本${group.revision} · ${group.updated_at} · 经手人#${group.updated_by||'未记录'}`:'尚未保存（查看不会自动创建）'}]} />
      {currentContext.missing.length>0 && <Alert type="warning" title="还需处理" description={currentContext.missing.join('；')} />}
      {selectedContext.isError && <Alert type="error" title="所选版本的来源核对读取失败，不视为没有差异" />}
      {!data.run_id && active.length>1 && <Select style={{width:'100%',marginBlock:12}} placeholder="多个生产段，请明确选择，不自动取最新" options={active.map((r)=>({value:r.id,label:`任务#${r.id} 第${r.segment_no}段 · 机台#${r.station_id||'未定'} · 模具#${r.mold_id||'未定'} · ${r.status_display} · ${r.planned_molds}模`}))} onChange={(id)=>{const next=new URLSearchParams(params);next.set('run_id',String(id));navigate(`?${next}`)}} />}
      {data.runs.some((r)=>r.historical) && <details><summary>历史任务（只追溯，不自动开始新生产）</summary>{data.runs.filter((r)=>r.historical).map((r)=><Typography.Paragraph key={r.id}>任务#{r.id} · {r.status_display} · 原计划{r.planned_molds}模 <Button href={`/production/records/${r.id}`}>查看原任务/卡</Button></Typography.Paragraph>)}<Button href="/production">确有续做：到生产页新增生产段并确认范围</Button></details>}
      <Space wrap style={{marginBlock:12}}><Button onClick={()=>setEditor(true)}>查看标准、候选与来源 / 维护</Button>{data.existing_groups.map((g)=><Button key={g.id} onClick={()=>applyGroup(g)}>{g.status==='GENERATED'?'查看/补打原卡':'继续上次准备'} {g.id.slice(0,8)} · {g.range_start?`${g.range_start}–${g.range_end}模`:'范围待核'}</Button>)}</Space>
      {Object.keys(data.raw_process_reference||{}).length>0 && <details><summary>完整产品参数（只读，条重单位g，不进入库存）</summary><Descriptions column={{xs:1,sm:2}} items={Object.entries(data.raw_process_reference||{}).map(([key,value])=>({key,label:({primary_curing:'一次加硫原文',secondary_curing:'二烤原文',total_cavities:'总孔',effective_cavities:'有效孔',actual_material_length:'实际料长',actual_cut_weight:'实际料重',large_strip_weight_g:'大条条重g',large_strip_count:'大条数量',small_strip_weight_g:'小条条重g',small_strip_count:'小条数量',forming_material_weight_g:'一模排料总重g'} as Record<string,string>)[key]||key,children:value==null||value===''?'未登记':String(value)}))} /></details>}
    </Card>
    {group?.status==='GENERATED'?<Card title={`原卡组 ${group.id.slice(0,8)} · ${group.range_start}–${group.range_end}模`}>
      <Alert type="success" title="本组已发号；重复打开查看原卡，不另开首次生产" />
      {cards.isError?<Alert type="error" title="卡列表读取失败，不视为没有卡" />:cards.isPending?<Typography.Paragraph>正在读取原卡与岗位记录…</Typography.Paragraph>:<Space wrap>{cards.data?.results.map((c)=><div key={c.id} style={{maxWidth:'100%'}}><Button href={`/flow-cards/cards/${c.id}`}>{c.card_no} · {c.planned_molds}模</Button><Typography.Paragraph type="secondary" style={{maxWidth:360}}>{c.process_todos?.map((t)=>`${t.label}·${t.role}：${t.state==='SYSTEM_UNREGISTERED'?'系统未登记':`有记录（${t.employees.join('、')||'看原单'}），核对范围`}`).join('；')}</Typography.Paragraph></div>)}</Space>}
      {cards.data && <><Button disabled={disabled||busy} onClick={()=>void perform(async()=>{await print(cards.data.results.map((c)=>c.id))})}>打印/补打本页原卡（不改旧号旧标准）</Button>{cards.data.count>100 && <Space><Button disabled={cardPage===1} onClick={()=>setCardPage(cardPage-1)}>上一页</Button><Button disabled={cardPage*100>=cards.data.count} onClick={()=>setCardPage(cardPage+1)}>下一页</Button></Space>}</>}
      {data.available_ranges.length>0 && <Button onClick={()=>applyGroup(null)}>明确追加未覆盖模段</Button>}
    </Card>:<Card title="分卡结果 / 保存本次准备">
      <Form form={form} layout="vertical" onValuesChange={()=>setPreview(undefined)}><div className="flow-form-grid">
        <Form.Item name="standard_id" label="适用标准（选择即重算）"><Select allowClear options={data.standards.filter((s)=>s.status==='ACTIVE'||s.status==='DRAFT').map((s)=>({value:s.id,label:`v${s.version} · ${STATE[s.status as keyof typeof STATE]} · ${s.source}`}))} onChange={(id)=>{const s=data.standards.find((s)=>s.id===id);if(s)form.setFieldsValue({whole_mold:s.whole_mold,molds_per_card:s.snapshot.packaging?.molds_per_card||100})}} /></Form.Item>
        <Form.Item name="range_start" label="本次起始模号"><InputNumber min={1} precision={0} disabled={!data.run_id} /></Form.Item>
        <Form.Item name="planned_molds" label="本次模数（缺真实任务则留空）"><InputNumber min={1} precision={0} disabled={!data.run_id} /></Form.Item>
        <Form.Item name="molds_per_card" label="每包模数M" rules={[{required:true}]}><InputNumber min={1} precision={0} /></Form.Item>
        <Form.Item name="diagram_id" label="本副实物模具的定点图（可空）"><Select allowClear options={data.diagrams.map((d)=>({value:d.id,label:`v${d.version} · ${d.direction}`}))} /></Form.Item>
        <Form.Item name="whole_mold" hidden><Input /></Form.Item>
      </div>
      {!data.run_id && <details><summary>先保存明确安排建议（不创建生产任务，不用总孔代替有效孔）</summary><div className="flow-form-grid"><Form.Item name="arranged_quantity" label="本次明确安排件数（不是欠交）"><InputNumber min={1} precision={0} /></Form.Item><Form.Item name="confirmed_effective_output" label="经确认的每模有效产出"><InputNumber min={1} precision={0} /></Form.Item><Form.Item name="output_unit" label="有效产出单位"><Select allowClear options={[{value:'件/模',label:'件/模'}]} /></Form.Item><Form.Item name="output_basis" label="有效产出依据"><Input /></Form.Item></div><Form.Item name="effective_output_confirmed" valuePropName="checked"><Checkbox>已核对有效产出，未把总孔或定点数当成有效孔</Checkbox></Form.Item></details>}
      <Form.Item name="preparation_notes" label="本次待准备说明（保存服务器，不回写母版）"><Input.TextArea maxLength={2000} /></Form.Item>
      <details><summary>仅调整本次生产段工艺（不回写产品标准）</summary>
        <Form.List name="process_adjustments">{(fields,{add,remove})=><>{fields.map((field)=><Card size="small" key={field.key} extra={<Button onClick={()=>remove(field.name)}>移除本次调整</Button>}>
          <div className="flow-form-grid"><Form.Item name={[field.name,'name']} label="工艺参数" rules={[{required:true}]}><Select options={PARAMETERS.map(([value,label])=>({value,label}))} /></Form.Item>
          <Form.Item name={[field.name,'state']} label="确认状态" rules={[{required:true}]}><Select options={[{value:'CONFIRMED',label:'已确认本段条件'},{value:'RAW_UNCONFIRMED',label:'待核，仅保存'},{value:'NOT_APPLICABLE',label:'本段不适用'}]} /></Form.Item>
          {([['lower','下限'],['upper','上限'],['unit','原单位'],['source','本次调整出处/依据']] as const).map(([key,label])=><Form.Item key={key} name={[field.name,key]} label={label} rules={key==='source'?[{required:true}]:[]}><Input /></Form.Item>)}</div>
        </Card>)}<Button onClick={()=>add({state:'RAW_UNCONFIRMED'})}>增加本次调整</Button></>}</Form.List>
      </details>
      {currentContext.conflicts.map((problem)=><Card key={problem.key} size="small" title={`${problem.name} · ${problem.stage||'原阶段待核'}`}><Table rowKey="id" size="small" dataSource={problem.sources} pagination={false} scroll={{x:600}} columns={[{title:'原值与单位',render:(_,c)=>`${c.lower_limit||'无下限'}～${c.upper_limit||'无上限'} ${c.unit||'单位待核'}`},{title:'出处',render:(_,c)=>sourceLink(c)}]} /><Form.Item name={['decisions',problem.key]} label="本次明确采用（未含订单特规须维护新版本，不能写说明绕过）"><Select allowClear options={(standard?.snapshot.criteria||[]).filter((c)=>problem.sources.some((v)=>v.id===c.id)).map((c)=>({value:c.id,label:`${c.name} · ${flowLimitText(c)} · 已选v${standard?.version}`}))} /></Form.Item></Card>)}
      </Form>
      <Typography.Title level={4}>{count?`${molds}模 → ${count}张 · 尾包${molds-(count-1)*per}模`:'范围待确认，不编造模数'}</Typography.Title>
      {standard && <><Typography.Paragraph>本次要测：{standard.snapshot.criteria.map((c)=>`${c.name} ${flowLimitText(c)}`).join('；')||'该版没有已确认测项'}</Typography.Paragraph><Typography.Paragraph>适用工序：{Object.entries(standard.snapshot.route||{}).filter(([k,v])=>k!=='confirmed'&&v).map(([k])=>({molding:'成型实测',patrol:'巡检',deburr:'毛边',bake:'二烤',baked_hardness:'烤后硬度',inspection:'品检员'} as Record<string,string>)[k]||k).join(' → ')}；巡检每{standard.snapshot.packaging?.patrol_every_molds||'待确认'}模，与每包模数分开。</Typography.Paragraph></>}
      {preview && <Alert type="info" title={`不落库预览：${preview.count}张，尾包${preview.tail_molds??'待确认'}模`} />}
      <Space wrap><Button loading={busy} onClick={()=>void perform(async()=>{setPreview(await flowApi.preview(await payload()))})}>预览（不保存，只读可用）</Button><Button disabled={disabled} loading={busy} onClick={()=>void save(false)}>保存本次准备</Button><Button type="primary" disabled={disabled||!data.run_id||!molds||!standard||standard.status!=='ACTIVE'} loading={busy} onClick={()=>void save(true)}>确认生成{count?` ${count} 张`:''}</Button></Space>
      {group && <Typography.Paragraph copyable>同一草稿ID：{group.id} · 版本{group.revision}；保存只更新本组。</Typography.Paragraph>}
    </Card>}
    {error && <Alert type="error" title="尚未完成，请核对服务器原结果" description={error} />}
    {printResults}
    <details><summary>独立扫码查卡</summary>{lookup}</details>
    <details><summary>设置（普通制卡无需先发布模板）</summary>{settings}</details>
    {editor && <StandardEditor data={data} open close={()=>setEditor(false)} changed={refresh} />}
  </>
}
