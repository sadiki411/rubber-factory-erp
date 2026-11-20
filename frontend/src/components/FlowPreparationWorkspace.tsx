import { Alert, Button, Card, Checkbox, Descriptions, Form, Input, InputNumber, Select, Space, Table, Typography } from 'antd'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useReadOnly } from '../access'
import { flowApi, flowLimitText, flowRequestKey, finishFlowRequest, splitPlannedMolds, type PreparationContext, type PreparationGroup, type PreparationSource, type FlowGroup, type PrintJob } from '../flowcards'
import { ProductStandardEditor as StandardEditor } from './ProductStandardEditor'

const STATE = { LEGACY: '旧记录·待复核适用性', DRAFT: '草稿', ACTIVE: '生效', SUPERSEDED: '已被替代' }
const PARAMETERS = [
  ['upper_set_temperature', '上模设定温度'], ['lower_set_temperature', '下模设定温度'],
  ['upper_contact_temperature', '上模接触温度目标'], ['lower_contact_temperature', '下模接触温度目标'],
  ['primary_curing_time', '一次加硫时间'], ['pressure', '压力（来源注明类别）'], ['venting', '排气'],
  ['arrangement', '排列/排料'], ['release_agent', '离型剂'], ['machine_type', '机型/吨位'], ['oil', '毛边洒油'], ['experience', '异常经验'],
]
const sourceText = (c: PreparationSource) => `${c.order_id ? `订单项#${c.order_id}` : '产品通用'} · ${c.source_file || '人工资料'} · ${c.source_sheet || '未登记表'} ${c.source_row == null ? '' : `第${c.source_row}行`} · ${c.version || '原版号未登记'}`
const sourceLink = (c: PreparationSource) => c.source_url ? <a href={c.source_url} target="_blank" rel="noopener noreferrer">{sourceText(c)} · 查看原件</a> : sourceText(c)


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
    const planned = g?.planned_molds ?? data.suggested_molds
    const perCard = g?.molds_per_card ?? s?.snapshot.packaging?.molds_per_card ?? 100
    form.setFieldsValue({planned_molds:planned,card_molds:g?.snapshot.card_molds || splitPlannedMolds(planned,perCard),range_start:g?.range_start ?? data.available_ranges[0]?.[0],molds_per_card:perCard,standard_id:s?.id,whole_mold:g?.whole_mold&&g.whole_mold!=='UNKNOWN'?g.whole_mold:s?.whole_mold||'UNKNOWN',diagram_id:g?.snapshot.diagram_id,decisions:snapshot?.decisions || {},preparation_notes:snapshot?.preparation_notes,process_adjustments:Object.entries(g?.snapshot.process_overrides||{}).map(([name,value])=>({name,...value})),...snapshot?.arrangement})
  }, [data, form])
  useEffect(() => {if(init.current !== contextKey) {init.current=contextKey; queueMicrotask(() => applyGroup(data.preferred_group))}},[contextKey,data,applyGroup])
  const standardId = Form.useWatch('standard_id',form)
  const molds = Form.useWatch('planned_molds',form)
  const per = Form.useWatch('molds_per_card',form)
  const allocation: Array<number | null> = Form.useWatch('card_molds',form) || []
  const total = allocation.reduce<number>((sum,value)=>sum+(Number(value)||0),0)
  const validPlan = allocation.length>0 && allocation.length<=500 && allocation.every(v=>Number.isInteger(v)&&Number(v)>0&&Number(v)<=100000) && total===molds
  const standard = data.standards.find((s) => s.id === (group?.status==='GENERATED'?group.snapshot.standard?.id:standardId))
  const selectedContext = useQuery({queryKey:['selected-preparation-standard',contextKey,standardId],queryFn:async()=>{
    const query=new URLSearchParams({product_id:String(data.product.id),standard_id:String(standardId),...(data.run_id?{run_id:String(data.run_id)}:{order_id:String(data.order_ids[0]),order_plan:'1'})})
    return await flowApi.context(query) as PreparationContext
  },enabled:Boolean(standardId&&standardId!==data.auto_standard_id&&group?.status!=='GENERATED')})
  const currentContext=standardId&&standardId!==data.auto_standard_id?selectedContext.data||data:data
  const count = allocation.length || null
  const perform = async (work: () => Promise<void>) => {setBusy(true);setError('');try{await work()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
  const payload = async () => {
    const values=await form.validateFields()
    if(values.planned_molds && !validPlan) throw new Error('请核对逐卡模数：每张为正整数，合计必须等于本次预计生产模数。')
    const rows: Array<{name:string}&Record<string,unknown>>=values.process_adjustments||[]
    if(new Set(rows.map((r)=>r.name)).size!==rows.length) throw new Error('本次工艺调整同一参数只能填写一项')
    const {process_adjustments:_,...rest}=values
    void _
    return {...rest,card_molds:values.planned_molds?values.card_molds:undefined,order_plan:!data.run_id,process_overrides:Object.fromEntries(rows.map(({name,...value})=>[name,value])),product_id:data.product.id,order_id:data.order_ids[0],run_id:data.run_id,order_ids:data.order_ids,...(group?.status==='DRAFT'?{group_id:group.id,expected_revision:group.revision}:{})}
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
    if(generate) {
      const firstPage=await flowApi.cards(result.id)
      if(firstPage.results.length) await print(firstPage.results.map(c=>c.id))
    }
  })
  const active = data.runs.filter((r)=>!r.historical)
  return <>
    <Card title={`${data.product.specification} / ${data.product.material} · 制卡准备`} extra={<Button onClick={refresh}>刷新服务器资料</Button>}>
      <Typography.Paragraph>{data.orders.map((o)=>`${o.order_no}/${o.item_no||'-'}`).join('、')} · M=模；制卡不报产量、不动库存或交付</Typography.Paragraph>
      <Descriptions title="已带入" column={{xs:1,sm:2}} items={[{key:'run',label:'计划来源',children:data.run_id?`任务#${data.run_id} · 已确认计划${data.runs.find((r)=>r.id===data.run_id)?.planned_molds ?? '见原任务'}模`:'订单直接安排：下方填写预计生产模数即可，不必先建生产任务'}, {key:'standard',label:'采用依据',children:standard?`v${standard.version} · ${standard.source}`:'尚无唯一适用生效版'}, {key:'saved',label:'服务器草稿',children:group?`${group.id} · 版本${group.revision} · ${group.updated_at} · 经手人#${group.updated_by||'未记录'}`:'尚未保存（查看不会自动创建）'}]} />
      {currentContext.missing.length>0 && <Alert type="warning" title="还需处理" description={currentContext.missing.join('；')} />}
      {selectedContext.isError && <Alert type="error" title="所选版本的来源核对读取失败，不视为没有差异" />}
      {!data.run_id && active.length>1 && <Select style={{width:'100%',marginBlock:12}} placeholder="多个生产段，请明确选择，不自动取最新" options={active.map((r)=>({value:r.id,label:`任务#${r.id} 第${r.segment_no}段 · 机台#${r.station_id||'未定'} · 模具#${r.mold_id||'未定'} · ${r.status_display} · ${r.planned_molds}模`}))} onChange={(id)=>{const next=new URLSearchParams(params);next.set('run_id',String(id));navigate(`?${next}`)}} />}
      {data.runs.some((r)=>r.historical) && <details><summary>历史任务（只追溯，不自动开始新生产）</summary>{data.runs.filter((r)=>r.historical).map((r)=><Typography.Paragraph key={r.id}>任务#{r.id} · {r.status_display} · 原计划{r.planned_molds}模 <Button href={`/production/records/${r.id}`}>查看原任务/卡</Button></Typography.Paragraph>)}<Button href="/production">确有续做：到生产页新增生产段并确认范围</Button></details>}
      <Space wrap style={{marginBlock:12}}><Button onClick={()=>setEditor(true)}>查看标准、候选与来源 / 维护</Button>{data.existing_groups.map((g)=><Button key={g.id} onClick={()=>applyGroup(g)}>{g.status==='GENERATED'?'查看/补打原卡':'继续上次准备'} {g.id.slice(0,8)} · {g.range_start?`${g.range_start}–${g.range_end}模`:'范围待核'}</Button>)}</Space>
      {Object.keys(data.raw_process_reference||{}).length>0 && <details><summary>完整产品参数（只读，条重单位g，不进入库存）</summary><Descriptions column={{xs:1,sm:2}} items={Object.entries(data.raw_process_reference||{}).map(([key,value])=>({key,label:({primary_curing:'一次加硫原文',secondary_curing:'二烤原文',total_cavities:'总孔',effective_cavities:'有效孔',actual_material_length:'实际料长',actual_cut_weight:'实际料重',large_strip_weight_g:'大条条重g',large_strip_count:'大条数量',small_strip_weight_g:'小条条重g',small_strip_count:'小条数量',forming_material_weight_g:'一模排料总重g'} as Record<string,string>)[key]||key,children:value==null||value===''?'未登记':String(value)}))} /></details>}
    </Card>
    {group?.status==='GENERATED'?<Card title={`原卡组 ${group.id.slice(0,8)} · ${group.planned_molds}模${group.range_start?`（${group.range_start}–${group.range_end}）`:''}`}>
      <Alert type="success" title="本组已发号；重复打开查看原卡，不另开首次生产" />
      {cards.isError?<Alert type="error" title="卡列表读取失败，不视为没有卡" />:cards.isPending?<Typography.Paragraph>正在读取原卡与岗位记录…</Typography.Paragraph>:<Space wrap>{cards.data?.results.map((c)=><div key={c.id} style={{maxWidth:'100%'}}><Button href={`/flow-cards/cards/${c.id}`}>{c.card_no} · {c.planned_molds}模</Button><Typography.Paragraph type="secondary" style={{maxWidth:360}}>{c.process_todos?.map((t)=>`${t.label}·${t.role}：${t.state==='SYSTEM_UNREGISTERED'?'系统未登记':`有记录（${t.employees.join('、')||'看原单'}），核对范围`}`).join('；')}</Typography.Paragraph></div>)}</Space>}
      {cards.data && <><Button disabled={disabled||busy} onClick={()=>void perform(async()=>{await print(cards.data.results.map((c)=>c.id))})}>打印/补打本页原卡（不改旧号旧标准）</Button>{cards.data.count>100 && <Space><Button disabled={cardPage===1} onClick={()=>setCardPage(cardPage-1)}>上一页</Button><Button disabled={cardPage*100>=cards.data.count} onClick={()=>setCardPage(cardPage+1)}>下一页</Button></Space>}</>}
      {(data.available_ranges.length>0 || !data.run_id) && <Button onClick={()=>applyGroup(null)}>{data.run_id?'明确追加未覆盖模段':'确有另一批：另建预计生产计划'}</Button>}
    </Card>:<Card title="分卡结果 / 保存本次准备">
      <Form form={form} layout="vertical" onValuesChange={(changed,values)=>{setPreview(undefined);if('planned_molds' in changed || 'molds_per_card' in changed) form.setFieldValue('card_molds',splitPlannedMolds(values.planned_molds,values.molds_per_card))}}><div className="flow-form-grid">
        <Form.Item name="standard_id" label="适用标准（选择即重算）"><Select allowClear options={data.standards.filter((s)=>s.status==='ACTIVE'||s.status==='DRAFT').map((s)=>({value:s.id,label:`v${s.version} · ${STATE[s.status as keyof typeof STATE]} · ${s.source}`}))} onChange={(id)=>{const s=data.standards.find((s)=>s.id===id);if(s)form.setFieldsValue({whole_mold:s.whole_mold,molds_per_card:s.snapshot.packaging?.molds_per_card||100})}} /></Form.Item>
        {data.run_id && <Form.Item name="range_start" label="本次起始模号"><InputNumber min={1} precision={0} /></Form.Item>}
        <Form.Item name="planned_molds" label="本次预计生产模数"><InputNumber min={1} max={10000000} precision={0} placeholder="例如360模" /></Form.Item>
        <Form.Item name="molds_per_card" label="默认每张卡模数（自动分卡）" rules={[{required:true}]}><InputNumber min={1} max={100000} precision={0} /></Form.Item>
        <Form.Item name="diagram_id" label="本副实物模具的定点图（可空）"><Select allowClear options={data.diagrams.map((d)=>({value:d.id,label:`v${d.version} · ${d.direction}`}))} /></Form.Item>
        <Form.Item name="whole_mold" hidden><Input /></Form.Item>
      </div>
      <Card size="small" title="逐张分配模数（每张及尾数均可修改）">
        <Form.List name="card_molds">{(fields,{add,remove})=><><div className="flow-card-allocation">{fields.map(field=><div key={field.key}><Form.Item name={field.name} label={`第${field.name+1}张${field.name===fields.length-1?' · 尾卡':''}`} rules={[{required:true,message:'请填写模数'}]}><InputNumber aria-label={`第${field.name+1}张预计模数`} min={1} max={100000} precision={0} addonAfter="模" /></Form.Item><Button size="small" onClick={()=>remove(field.name)}>移除此卡</Button></div>)}</div><Space wrap><Button onClick={()=>form.setFieldValue('card_molds',splitPlannedMolds(molds,per))}>按默认模数重新分配</Button><Button disabled={fields.length>=500} onClick={()=>add(1)}>增加一张</Button><Button disabled={!total} onClick={()=>{form.setFieldValue('planned_molds',total);setPreview(undefined)}}>预计模数改为分卡合计 {total} 模</Button></Space></>}</Form.List>
        <Alert type={validPlan?'success':'warning'} title={`预计 ${molds||0} 模 · 已分配 ${total} 模 · 差额 ${(molds||0)-total} 模`} description="改尾数或其他卡后，请保持合计一致；也可用上方按钮把预计模数改为分卡合计。" />
      </Card>
      {data.existing_groups.some(g=>g.status==='GENERATED'&&!g.run_id) && <Form.Item name="append_confirmed" valuePropName="checked"><Checkbox>已核对原卡，这确实是另一批预计生产计划，不是重复打印</Checkbox></Form.Item>}
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
      <Typography.Title level={4}>{count?`${molds||0}模 → ${count}张 · ${allocation.join(' + ')}模`:'填写预计模数后自动分卡'}</Typography.Title>
      {standard && <><Typography.Paragraph>本次要测：{standard.snapshot.criteria.filter(c=>c.follow_package!==false&&c.limit_mode!=='NONE').map((c)=>`${c.name} ${flowLimitText(c)}`).join('；')||'该版没有已确认测项'}</Typography.Paragraph><Typography.Paragraph>适用工序：{Object.entries(standard.snapshot.route||{}).filter(([k,v])=>k!=='confirmed'&&v).map(([k])=>({molding:'成型实测',patrol:'巡检',deburr:'毛边',bake:'二烤',baked_hardness:'烤后硬度',inspection:'品检员'} as Record<string,string>)[k]||k).join(' → ')}；巡检每{standard.snapshot.packaging?.patrol_every_molds||'待确认'}模，与每包模数分开。</Typography.Paragraph></>}
      {preview && <Alert type="info" title={`不落库预览：${preview.count}张，尾包${preview.tail_molds??'待确认'}模`} />}
      <Space wrap><Button loading={busy} onClick={()=>void perform(async()=>{setPreview(await flowApi.preview(await payload()))})}>预览（不保存，只读可用）</Button><Button disabled={disabled} loading={busy} onClick={()=>void save(false)}>保存本次准备</Button><Button type="primary" disabled={disabled||!validPlan||!standard||standard.status!=='ACTIVE'} loading={busy} onClick={()=>void save(true)}>生成{count?` ${count} 张`:''}并准备打印</Button></Space>
      {count && count>100 && <Alert type="info" title="超过100张将分批打印；本次先准备前100张，余卡在原卡组翻页打印，不重新发号。" />}
      {group && <Typography.Paragraph copyable>同一草稿ID：{group.id} · 版本{group.revision}；保存只更新本组。</Typography.Paragraph>}
    </Card>}
    {error && <Alert type="error" title={group?.status==='GENERATED'?'卡已生成，打印尚未完成；请继续原打印任务，不重新制卡':'尚未完成，请核对服务器原结果'} description={error} />}
    {printResults}
    <details><summary>独立扫码查卡</summary>{lookup}</details>
    <details><summary>设置（普通制卡无需先发布模板）</summary>{settings}</details>
    {editor && <StandardEditor data={data} open close={()=>setEditor(false)} changed={refresh} />}
  </>
}
