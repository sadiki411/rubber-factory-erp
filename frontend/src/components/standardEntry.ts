import type { CustomerInspection } from '../customerInspection'
import type { Criterion } from '../flowcards'
import type { ProcessParameter } from './productStandardFields'

export type DraftCriterion = Partial<Criterion> & {name:string; stage:string; criterion_id?:number}

const inspectionAliases = new Map([
  ['硬度','HD'], ['成型硬度','HD'], ['成型后硬度','HD'], ['烤后硬度','HD'], ['二烤后硬度','HD'],
  ['密度','SG'], ['比重','SG'], ['内径','ID'], ['外径','OD'], ['厚度','THK'],
  ['轴向线径','C/S-A'], ['径向线径','C/S-R'], ['合模错位','MISMATCH'], ['错模','MISMATCH'],
  ['毛边厚度','FLASHT'], ['毛边伸出长度','FLASHLEN'], ['毛边长度','FLASHLEN'],
  ['线径','C/S'], ['截面直径','C/S'], ['毛边','FLASH'],
])

function inspectionItemKey(name:string) {
  const text=name.normalize('NFKC').replace(/\s/g,'').toUpperCase()
  return inspectionAliases.get(text) || text
}

export function standardItemKey(name:string, stage:string) {
  return `${stage}:${inspectionItemKey(name)}`
}

export function factoryItemLabel(name:string, stage:string) {
  const key=standardItemKey(name,stage).slice(stage.length+1)
  if(key==='HD') return stage==='BAKED_HARDNESS'?'HD（二烤后硬度）':'HD（成型后硬度）'
  if(key==='C/S') return 'C/S（线径）'
  if(inspectionAliases.has(name.normalize('NFKC').replace(/\s/g,'').toUpperCase())) return `${key}（${name}）`
  return name
}

export function mergeStandardEntries(current:DraftCriterion[], incoming:DraftCriterion[]) {
  const next=[...current]
  const seen=new Map<string,string>()
  for(const entry of incoming) {
    const key=standardItemKey(entry.name,entry.stage)
    const limits=JSON.stringify([entry.lower,entry.upper,entry.unit,entry.lower_inclusive,entry.upper_inclusive])
    if(seen.has(key)) {
      if(seen.get(key)!==limits) throw new Error(`原表的「${entry.name}」在同一阶段有不同要求，请先核对原表。`)
      continue
    }
    seen.set(key,limits)
    const matches=next.map((row,index)=>[row,index] as const).filter(([row])=>standardItemKey(row.name,row.stage)===key)
    if(matches.length>1) throw new Error(`本版有多个同名同阶段的「${entry.name}」，请先移除重复项，避免猜配。`)
    if(matches.length===0) next.push(entry)
    else {
      const [previous,index]=matches[0]
      if(previous.criterion_id && entry.criterion_id===previous.criterion_id) continue
      next[index]={...previous,...entry,name:entry.criterion_id?entry.name:previous.name,criterion_id:entry.criterion_id||previous.criterion_id}
    }
  }
  return next
}

export function adoptedConditions(data:CustomerInspection) {
  const {primary,secondary}=data.reference
  const process_parameters:Record<string,ProcessParameter> = primary.state === 'REFERENCE' ? {
    primary_curing_time:{state:'CONFIRMED',unit:'s',lower:primary.time_lower,upper:primary.time_upper,raw_text:primary.raw,source:'本单翔邦原表'},
    mold_temperature_range:{state:'CONFIRMED',unit:'℃',lower:primary.temperature_lower,upper:primary.temperature_upper,raw_text:primary.raw,source:'本单翔邦原表'},
  } : {}
  const bake = secondary.state==='NOT_REQUIRED' ? {state:'NOT_APPLICABLE'} : secondary.state==='REFERENCE' ? {
    state:'APPLICABLE',temperature_lower:secondary.temperature_lower,temperature_upper:secondary.temperature_upper,
    minutes_lower:secondary.time_lower,minutes_upper:secondary.time_upper,
  } : undefined
  return {process_parameters,bake,customer_key:data.customer_code,applicable_order_id:data.order_id,
    customer_reference:{key:data.adoption_key},source:`翔邦原表 · ${data.order_no} / ${data.item_no} · ${data.project_no}`}
}

export function adoptedInspectionEntries(data:CustomerInspection, current:DraftCriterion[], bakeState?:string) {
  const primary=data.selection?.main.filter(row=>row.stage) || []
  const keys=new Set(primary.map(row=>row.key))
  const rows=current.filter(c=>!c.customer_item&&!keys.has(inspectionItemKey(c.name)))
  for(const row of primary) {
    if(!row.stage) continue
    const stages=row.key==='HD'&&bakeState==='APPLICABLE'?[row.stage,'BAKED_HARDNESS']:[row.stage]
    for(const stage of stages) {
      const previous=current.find(c=>c.customer_item===row.key&&c.stage===stage)
      rows.push({name:row.item,stage,customer_item:row.key,lower:row.lower,upper:row.upper,unit:row.unit,
        limit_mode:row.lower&&row.upper?'RANGE':row.lower?'LOWER':'UPPER',
        requirement_state:'CONFIRMED',method:previous?.method||'',follow_package:row.key==='HD'||previous?.follow_package===true,
        provenance:{...row,raw_text:`${row.lower||'—'} ～ ${row.upper||'—'} ${row.unit}`}})
    }
  }
  return rows
}
