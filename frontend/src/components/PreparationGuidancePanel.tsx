import { Alert, Button, Card, Descriptions, Grid, Typography } from 'antd'
import type { PreparationContext, PreparationGroup, PreparationGuidance } from '../flowcards'
import { bakeText, processParameterText } from './productStandardFields'

export function PreparationGuidancePanel({data,group,standardId,onEdit,canEdit=false}:{data:PreparationContext;group:PreparationGroup|null;standardId?:number;onEdit?:()=>void;canEdit?:boolean}) {
  const {sm}=Grid.useBreakpoint()
  const frozen=group?.status==='GENERATED'
  const selected=data.standards.find(s=>s.id===(frozen?group.snapshot.standard?.id:standardId))
  const rows:PreparationGuidance[]=frozen?(group.snapshot.guidance||[]):data.guidance||[]
  const originalReview=frozen?data.original_reference_reviews?.[group.id]||[]:[]
  return <Card size="small" title={frozen?`已发 ${group.snapshot.card_molds?.length||Math.ceil((group.planned_molds||0)/group.molds_per_card)} 张 · 原卡采用 ${group.snapshot.standard?.customer_key||selected?.customer_key||'适用范围见原卡'} v${group.snapshot.standard?.version||'未登记'}`:selected?'本单已选制卡标准':'本单还未选择制卡标准'}>
    <Typography.Paragraph>依据：{frozen?group.snapshot.standard?.source||'原卡未登记':selected?.source||'尚未选定标准。先核对下方订单原表，再带入；也可以手工维护产品标准'}。{!frozen&&onEdit&&<Button type="link" onClick={onEdit}>{canEdit?'维护产品标准':'查看产品标准'}</Button>}</Typography.Paragraph>
    <Descriptions size="small" layout={sm?'horizontal':'vertical'} column={{xs:1,sm:2}} items={rows.filter(row=>row.value!=='未提供'||row.raw_reference).map(row=>({key:row.key,label:row.label,children:<div>{row.value}{row.state!=='CONFIRMED'&&row.state!=='NOT_APPLICABLE'?'（参考）':''}{row.warning&&!row.warning.startsWith('翔邦原表')&&row.state==='CONFIRMED'&&<div>{row.warning}</div>}</div>}))} />
    {frozen&&!rows.length&&<><Typography.Paragraph>原卡工艺：{Object.entries(group.snapshot.process_parameters||group.snapshot.standard?.snapshot.process_parameters||{}).map(([key,p])=>`${({oil:'洒油',primary_curing_time:'一次加硫',pressure:'压力'} as Record<string,string>)[key]||key} ${processParameterText(p)}（${p.state==='CONFIRMED'?'已确认':'待核'}）`).join('；')||'原卡未保存结构化工艺'}</Typography.Paragraph><Typography.Paragraph>二烤：{bakeText(group.snapshot.standard?.snapshot.bake)}。查看历史卡不套用新标准；补充作业指示须另留依据、日期和经手人。</Typography.Paragraph></>}
    {originalReview.map(row=><Alert key={row.key} type="warning" title={`${row.label} · ${row.warning}`} description={`原卡版本参考：${row.value}${row.raw_reference?`；当前产品档案原文：${row.raw_reference}`:''}。仅提醒核对，不改原卡。`} />)}
    {!frozen&&Boolean(data.standard_candidates?.length)&&<details><summary>并排核对适用版本、冲突字段及来源（不自动取最新）</summary>{data.standard_candidates?.map(s=><Card size="small" key={s.id} title={`${s.customer_key||'通用'}适用 v${s.version} · ${s.source_kind==='CUSTOMER'?'客户要求':'本厂资料'}`}><div>依据：{s.source}</div>{s.guidance.filter(r=>r.value!=='未提供'||r.raw_reference).map(r=><div key={r.key}>{r.label}：{r.value} · {r.state==='CONFIRMED'?'已确认':'待核'}{r.raw_reference?`；产品原文 ${r.raw_reference}`:''}{r.warning?`；${r.warning}`:''}</div>)}</Card>)}</details>}
  </Card>
}
