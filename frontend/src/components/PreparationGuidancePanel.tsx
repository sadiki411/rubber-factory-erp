import { Alert, Card, Typography } from 'antd'
import type { PreparationContext, PreparationGroup, PreparationGuidance } from '../flowcards'
import { bakeText, processParameterText } from './productStandardFields'

export function PreparationGuidancePanel({data,group,standardId}:{data:PreparationContext;group:PreparationGroup|null;standardId?:number}) {
  const frozen=group?.status==='GENERATED'
  const selected=data.standards.find(s=>s.id===(frozen?group.snapshot.standard?.id:standardId))
  const rows:PreparationGuidance[]=frozen?(group.snapshot.guidance||[]):data.guidance||[]
  const originalReview=frozen?data.original_reference_reviews?.[group.id]||[]:[]
  return <Card size="small" title={frozen?`已发 ${group.snapshot.card_molds?.length||Math.ceil((group.planned_molds||0)/group.molds_per_card)} 张 · 原卡采用 ${group.snapshot.standard?.customer_key||selected?.customer_key||'适用范围见原卡'} v${group.snapshot.standard?.version||'未登记'}`:'本单采用什么 / 哪项待确认'}>
    <Typography.Paragraph>依据：{frozen?group.snapshot.standard?.source||'原卡未登记':selected?.source||'尚未选定标准，请产品标准负责人核对下列参考'}。适用客户不代表由客户签发；标准确认不等于本批已实测。</Typography.Paragraph>
    <div className="flow-guidance-grid">{rows.map(row=><div key={row.key} style={{marginBlock:8}}><Typography.Text strong>{row.label}：</Typography.Text>{row.instruction||row.value} · {row.state==='CONFIRMED'?'已确认':row.state==='NOT_APPLICABLE'?'确认不适用':'待核'}{row.raw_reference&&<div>产品原文参考：{row.raw_reference}</div>}{row.warning&&<Alert type="warning" title={row.warning} description={`下一步：${row.owner}`} />}</div>)}</div>
    {frozen&&!rows.length&&<><Typography.Paragraph>原卡工艺：{Object.entries(group.snapshot.process_parameters||group.snapshot.standard?.snapshot.process_parameters||{}).map(([key,p])=>`${({oil:'洒油',primary_curing_time:'一次加硫',pressure:'压力'} as Record<string,string>)[key]||key} ${processParameterText(p)}（${p.state==='CONFIRMED'?'已确认':'待核'}）`).join('；')||'原卡未保存结构化工艺'}</Typography.Paragraph><Typography.Paragraph>二烤：{bakeText(group.snapshot.standard?.snapshot.bake)}。查看历史卡不套用新标准；补充作业指示须另留依据、日期和经手人。</Typography.Paragraph></>}
    {originalReview.map(row=><Alert key={row.key} type="warning" title={`${row.label} · ${row.warning}`} description={`原卡版本参考：${row.value}${row.raw_reference?`；当前产品档案原文：${row.raw_reference}`:''}。仅提醒核对，不改原卡。`} />)}
    {!frozen&&Boolean(data.standard_candidates?.length)&&<details><summary>并排核对适用版本、冲突字段及来源（不自动取最新）</summary>{data.standard_candidates?.map(s=><Card size="small" key={s.id} title={`${s.customer_key||'通用'}适用 v${s.version} · ${s.source_kind==='CUSTOMER'?'客户要求':'本厂资料'}`}><div>依据：{s.source}</div>{s.guidance.filter(r=>r.value!=='未提供'||r.raw_reference).map(r=><div key={r.key}>{r.label}：{r.value} · {r.state==='CONFIRMED'?'已确认':'待核'}{r.raw_reference?`；产品原文 ${r.raw_reference}`:''}{r.warning?`；${r.warning}`:''}</div>)}</Card>)}</details>}
  </Card>
}
