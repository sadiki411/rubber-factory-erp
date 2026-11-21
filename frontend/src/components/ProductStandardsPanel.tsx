import { Alert, Button, Card, Descriptions, Empty, Space, Tag, Typography } from 'antd'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { flowApi, flowLimitText, type PreparationContext } from '../flowcards'
import { useReadOnly } from '../access'
import { ProductStandardEditor } from './ProductStandardEditor'
import { bakeText, VISIBLE_NUMERIC_PROCESS_FIELDS, processParameterText, TEXT_PROCESS_FIELDS } from './productStandardFields'
import '../pages/FlowCardsPage.css'
import { ProductCustomerInspectionPanel } from './CustomerInspectionPanel'
import { inspectionItemLabel } from '../inspectionItemLabel'
import { ProductMachineTemperatures } from './ProductMachineTemperatures'

export function ProductStandardsPanel({productId,importFirst=false}: {productId:number;importFirst?:boolean}) {
  const readOnly=useReadOnly()
  const [editing,setEditing]=useState(importFirst)
  const query=useQuery({queryKey:['product-standards',productId],queryFn:()=>flowApi.context(new URLSearchParams({product_id:String(productId)}))})
  const data=query.data as PreparationContext|undefined
  return <Card size="small" title="硬度、尺寸与产品标准" className="product-standard-panel">
    <ProductMachineTemperatures key={productId} productId={productId} />
    <Typography.Paragraph type="secondary">来源是客户标准书 / 图纸，或人工确认的实际工艺。保存在本条产品资料下；订单选用此产品后引用，现场实测另行登记。</Typography.Paragraph>
    {query.isLoading&&<Typography.Paragraph>正在读取产品标准…</Typography.Paragraph>}
    {query.isError&&<Alert type="error" title="标准读取失败，不代表没有标准" description={(query.error as Error).message} action={<Button onClick={()=>void query.refetch()}>重试</Button>} />}
    {data&&<>
      <ProductCustomerInspectionPanel productId={productId} />
      <Space wrap><Button type="primary" disabled={readOnly||!data.can_maintain_standard||!data.write_enabled} onClick={()=>setEditing(true)}>填写 / 导入硬度与尺寸标准</Button></Space>
      {!data.standards.length&&<Empty description="尚无已确认产品标准。请填写或导入；不会从材质编号猜硬度，也不会从规格猜公差。" />}
      {data.standards.slice(0,5).map(s=><Card key={s.id} size="small" className="product-standard-field-card" title={<Space wrap><span>v{s.version}</span><Tag color={s.status==='ACTIVE'?'green':'default'}>{{ACTIVE:'生效',DRAFT:'草稿',SUPERSEDED:'已替代',LEGACY:'旧记录待核'}[s.status]||s.status}</Tag><span>{s.customer_key||'通用'}{s.applicable_order_id?` · 仅订单项#${s.applicable_order_id}`:''}</span></Space>}>
        <Typography.Paragraph type="secondary">依据：{s.source} · {s.scope||'范围未另注明'}</Typography.Paragraph>
        <Descriptions column={1} size="small" items={s.snapshot.criteria.map(c=>({key:c.id,label:`${c.stage==='BAKED_HARDNESS'?'烤后':c.stage==='MOLDING_HARDNESS'?'成型后':''}${inspectionItemLabel(c.name)}`,children:<>{flowLimitText(c)} · {c.follow_package===false?'仅留标准资料':'印到流转卡'}{c.requirement_state!=='CONFIRMED'?' · 待核':''}</>}))} />
        <Descriptions column={1} size="small" items={[{key:'bake',label:'二烤',children:bakeText(s.snapshot.bake)},...[...VISIBLE_NUMERIC_PROCESS_FIELDS,...TEXT_PROCESS_FIELDS].flatMap(([key,label])=>{const p=s.snapshot.process_parameters?.[key];return p?[{key,label,children:<>{processParameterText(p,TEXT_PROCESS_FIELDS.some(([name])=>name===key))}{p.state==='RAW_UNCONFIRMED'?' · 原文待核':''}</>}]:[]})]} />
      </Card>)}
      {editing&&<ProductStandardEditor data={data} open close={()=>setEditing(false)} changed={()=>void query.refetch()} initialTab={importFirst?'import':'quality'} />}
    </>}
  </Card>
}
