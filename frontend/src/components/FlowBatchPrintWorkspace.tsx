import { Alert, Button, Card, Checkbox, Col, Form, Input, Modal, Pagination, Row, Space, Tag, Typography } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { flowApi, flowRequestKey, finishFlowRequest, type FlowPrintOrder, type FlowPrintOrderScope, type FlowPrintPlan, type PaperLayout, type PrintJob } from '../flowcards'
import { withFlowPrintWindow } from '../flowPrintWindow'
import { FlowPrintLayout, FlowPrintPreview } from './FlowPrintLayout'
import { FlowPrintResults } from './FlowPrintResults'

type Filters = { specification:string; material:string; q?:string }
const EMPTY: Filters = { specification:'', material:'' }

export function FlowBatchPrintWorkspace({ specification, material, readOnly }: { specification:string; material:string; readOnly:boolean }) {
  const [filters, setFilters] = useState<Filters>(specification && material ? {specification, material} : EMPTY)
  const [page, setPage] = useState(1)
  const [orders, setOrders] = useState<FlowPrintOrder[]>([])
  const [layout, setLayout] = useState<PaperLayout>('A5_SINGLE')
  const [second, setSecond] = useState(false)
  const [reprints, setReprints] = useState(false)
  const [plan, setPlan] = useState<FlowPrintPlan>()
  const [packet, setPacket] = useState(0)
  const [ids, setIds] = useState<string[]>([])
  const [job, setJob] = useState<PrintJob>()
  const [existingScope,setExistingScope] = useState<string[]>()
  const [used, setUsed] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const submitting = useRef(false)
  const scope: FlowPrintOrderScope = { specification:filters.specification, material:filters.material, order_ids:orders.map(o=>o.id).sort((a,b)=>a-b) }
  const options = useQuery({queryKey:['flow-print-orders',filters,page], enabled:Boolean(filters.specification && filters.material),
    queryFn:({signal})=>flowApi.printOrderOptions(new URLSearchParams({...filters,page:String(page)}),signal)})
  const reset = () => { setPlan(undefined); setPacket(0); setIds([]); setUsed([]); setError('') }
  const act = async (work:()=>Promise<void>) => {
    if(submitting.current) return
    submitting.current=true;setError('');setBusy(true)
    try { await work() } catch(e) { setError((e as Error).message) } finally { submitting.current=false;setBusy(false) }
  }
  const inspect = () => act(async()=>{
    const next = await flowApi.printOrderPlan({order_scope:scope,paper_layout:layout,include_reprints:reprints})
    setPlan(next);setPacket(0);setUsed([]);setIds(next.packets[0]?.map(c=>c.id)||[])
  })
  const allowPrint = Boolean(plan?.write_enabled) && !readOnly
  const selectPacket = (index:number) => {
    setPacket(index)
    setIds(plan?.packets[index]?.filter(c=>!used.includes(c.id)).map(c=>c.id) || [])
  }
  const print = () => act(async()=>{
    // One job per confirmed selection; the same frozen job is restored on retry.
    const body={card_ids:ids,order_scope:scope,include_reprints:reprints,paper_layout:layout,second_inspection:second,resume_active:true}
    const slot='batch-order-print:' + ids.slice().sort().join(',')
    const result = await withFlowPrintWindow(()=>flowApi.print({...body,request_key:flowRequestKey(slot,body)}))
    setExistingScope(undefined);setJob(result);finishFlowRequest(slot)
    setUsed(previous=>[...new Set([...previous,...ids])])
    setIds([])
  })
  return <Space orientation="vertical" size="middle" style={{width:'100%'}}>
    <Alert type="info" title="只打已经生成的原卡，不合并制卡" description="各订单的卡号、二维码、批序和标准保持原样。同一卡关联多个订单只选一次；没制卡的先回订单制卡。默认不补打已经确认出纸的卡。" />
    <Card size="small" title="1. 按规格和材质找订单">
      <Form<Filters> layout="vertical" initialValues={{specification,material,q:''}} disabled={busy} onFinish={(values)=>{
        const next={specification:values.specification.trim(),material:values.material.trim(),q:values.q?.trim()||''}
        if(next.specification.toLowerCase()!==filters.specification.toLowerCase() || next.material.toLowerCase()!==filters.material.toLowerCase()) {
          setOrders([]);reset()
        }
        setFilters(next);setPage(1)
      }}>
        <Row gutter={12}>
          <Col xs={24} sm={7}><Form.Item name="specification" label="规格" rules={[{required:true,whitespace:true}]}><Input maxLength={150} placeholder="例如 DN125" /></Form.Item></Col>
          <Col xs={24} sm={7}><Form.Item name="material" label="材质" rules={[{required:true,whitespace:true}]}><Input maxLength={150} placeholder="例如 E6514" /></Form.Item></Col>
          <Col xs={24} sm={10}><Form.Item name="q" label="筛订单号／项次（可空）"><Input maxLength={100}/></Form.Item></Col>
        </Row>
        <Button type="primary" htmlType="submit" loading={options.isFetching}>查订单</Button>
      </Form>
      {options.isError && <Alert type="error" title="订单查询失败，不代表没有卡" description={(options.error as Error).message} action={<Button onClick={()=>void options.refetch()}>重试</Button>} />}
      {options.data && <Space orientation="vertical" style={{width:'100%',marginTop:12}}>
        <Space wrap><Tag>已选 {orders.length} 个项次（可跨页）</Tag>
          <Button disabled={busy || options.isFetching} onClick={()=>{
            const next=[...new Map([...orders,...options.data!.results.filter(o=>o.has_cards)].map(o=>[o.id,o])).values()]
            if(next.length>200){setError('每次最多选择 200 个订单项次，请分次处理。');return}
            setOrders(next);reset()
          }}>全选本页已有卡的订单</Button>
          <Button disabled={busy || !orders.length} onClick={()=>{setOrders([]);reset()}}>清空选择</Button>
        </Space>
        {!options.data.results.length && <Typography.Text>没有完全匹配的订单。请核对规格和材质的写法。</Typography.Text>}
        {options.data.results.map(o=><div key={o.id}>
          <Checkbox checked={orders.some(v=>v.id===o.id)} disabled={busy || !o.has_cards} onChange={e=>{
            if(e.target.checked && orders.length>=200){setError('每次最多选择 200 个订单项次，请分次处理。');return}
            setOrders(prev=>e.target.checked?[...prev,o]:prev.filter(v=>v.id!==o.id));reset()
          }}>{o.order_no} / {o.item_no||'—'} · {o.customer||'客户未登记'} · {o.due_date||'交期未登记'}</Checkbox>
          <Tag>{o.status}</Tag>{!o.has_cards&&<Button type="link" href={`/flow-cards?order_id=${o.id}&order_plan=1&return_to=${encodeURIComponent(location.pathname+location.search)}`}>没有可用原卡 · 去制卡</Button>}
        </div>)}
        <Pagination current={page} pageSize={20} total={options.data.count} showSizeChanger={false} onChange={setPage} hideOnSinglePage />
      </Space>}
    </Card>
    <Card size="small" title="2. 核对纸型与本次卡片">
      <Space wrap>
        <FlowPrintLayout value={layout} onChange={value=>{setLayout(value);reset()}} disabled={busy}/>
        <Checkbox checked={second} disabled={busy} onChange={e=>setSecond(e.target.checked)}>本次打印需要二检</Checkbox>
        <Checkbox checked={reprints} disabled={busy} onChange={e=>{setReprints(e.target.checked);reset()}}>包含已打印卡（补打）</Checkbox>
        <Button loading={busy} disabled={readOnly || !orders.length} onClick={()=>void inspect()}>汇总所选订单的卡</Button>
      </Space>
      {layout==='A5_SINGLE'&&<Typography.Paragraph type="secondary" style={{marginBlock:8}}>打印机选 A5 横向、实际大小 100%、每张纸 1 页。已有续页仍按原卡号保留。</Typography.Paragraph>}
      {error&&<Alert type="error" showIcon title={error} style={{marginTop:8}}/>}
      {plan&&<Space orientation="vertical" style={{marginTop:10,width:'100%'}}>
        <Typography.Text>可打印 {plan.card_count} 张 · {plan.counts.pending} 张已有待确认任务 · {plan.counts.reprint} 张曾打印</Typography.Text>
        {plan.counts.identity_mismatch>0&&<Alert type="warning" title={plan.counts.identity_mismatch+' 张原卡的规格／材质快照不一致，未收入本次'} description="请打开对应订单的原卡组核对，不按相似文字猜配。" />}
        {plan.counts.pending>0&&<Alert type="info" title="已有待确认任务的卡未纳入" description={
          <><div>已经出纸则确认；还没出纸可继续原任务，或确认取消旧任务后重新汇总，再和其他订单一起打。</div>
            <Space wrap>{plan.pending_jobs.map(p=><Button key={p.id} size="small" disabled={busy||Boolean(job)} onClick={()=>void act(async()=>{
              const original=await flowApi.printJob(p.id);setExistingScope(p.card_ids);setJob(original)
            })}>原任务 {p.id.slice(0,8)} · {p.card_ids.length} 张</Button>)}</Space></>
        } />}
        {!plan.card_count&&<Typography.Text>目前没有符合本次选择的原卡。</Typography.Text>}
        {plan.packets.length>1&&<Space wrap><Typography.Text>按产品档案、客户分册，每册最多 100 张；非 A5 模式下不同原尺寸或版式也分开。</Typography.Text><Pagination simple disabled={busy} current={packet+1} total={plan.packets.length} pageSize={1} onChange={index=>selectPacket(index-1)}/></Space>}
        {!!plan.packets[packet]?.length&&<>
          <Space wrap><Tag>本册已选 {ids.length} 张</Tag>
            <Button size="small" disabled={busy} onClick={()=>selectPacket(packet)}>选本册待打</Button><Button size="small" disabled={busy} onClick={()=>setIds([])}>本册全不选</Button>
          </Space>
          <details><summary>逐张核对 / 取消不需要打印的卡</summary>
            <Checkbox.Group value={ids} onChange={setIds} style={{width:'100%',display:'grid',gap:6,marginTop:8}}
              options={plan.packets[packet].map(c=>({value:c.id,disabled:busy||used.includes(c.id),label:c.card_no+' · '+c.sequence+'/'+c.denominator+' 批 · '+c.orders.map(o=>o.order_no+'/'+(o.item_no||'—')).join('、')+(c.reprint?' · 补打':'')+(used.includes(c.id)?' · 已建任务':'')}))} />
          </details>
          <Space wrap><FlowPrintPreview ids={ids} layout={layout} second={second} scope={scope} includeReprints={reprints} disabled={!allowPrint||busy}/>
            <Button type="primary" disabled={!ids.length||!allowPrint||Boolean(job)} loading={busy} onClick={()=>void print()}>打印本册所选原卡</Button></Space>
        </>}
      </Space>}
    </Card>
    <Modal open={Boolean(job)} title="多订单工序卡 · 核对打印结果" onCancel={()=>setJob(undefined)} footer={<Button onClick={()=>setJob(undefined)}>关闭（不登记结果）</Button>} destroyOnHidden width={680}>
      {job&&<FlowPrintResults key={job.id} job={job} changed={setJob} disabled={!allowPrint} scopeIds={existingScope}/>}
    </Modal>
  </Space>
}
