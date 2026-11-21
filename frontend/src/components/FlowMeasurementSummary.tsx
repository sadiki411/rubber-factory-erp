import { Alert, Button, Card, Space, Typography } from 'antd'
import dayjs from 'dayjs'
import { flowLimitText, type Criterion, type FlowCardDetail, type FlowEvent } from '../flowcards'

export const FLOW_KIND: Record<string, string> = { WHOLE_MOLD: '整模作业', UPPER_TEMP: '上模实测温度', LOWER_TEMP: '下模实测温度', MOLDING_HARDNESS: '成型硬度', BAKED_HARDNESS: '烤后硬度', DIMENSION: '尺寸测量', BAKE: '二烤作业', DEBURR: '去毛边', APPEARANCE: '外观检查', ANOMALY: '异常', RESOLUTION: '异常处理', SOURCE_CONFIRM: '实物来源核对', VOID: '作废',WORK_GUIDANCE:'补充作业指示（不改原卡）' }
export const flowTime = (v: string | null) => v ? dayjs(v).format('YYYY-MM-DD HH:mm') : '未知'
export type CouponSample = { sample_ref: string; material_ref: string; card_ids: string[]; trays: number[]; accompanied: boolean }
export function FlowMeasurementSummary({ card, disabled, measure }: { card: FlowCardDetail; disabled: boolean; measure: (c: Criterion, original?: FlowEvent) => void }) {
  return <section aria-label="实测与复核状态">
    {card.measurement_alerts?.map((e) => {
      const c = e.values.standard_snapshot as Criterion
      const name = c?.name || FLOW_KIND[e.kind]
      return <Alert key={e.id} type="error" showIcon title={`${name}${e.values.limit_side === 'LOWER' ? '低于下限' : '超上限'}，待复核`}
        description={<><div>实测 {String(e.values.actual)} {String(e.values.unit || '')}；标准 {flowLimitText(c)}</div><div>{String(e.values.measurement_message)} · {card.card_no}</div><div>{e.employee_name} · {flowTime(e.business_at)}；仅为本次检查，不判整批合格。</div></>}
        action={<Button disabled={disabled} onClick={() => measure(c, e.review_target || e)}>复测 / 更正</Button>} />
    })}
    {card.measurement_status?.filter((m) => !card.measurement_alerts?.some((e) => e.id === m.event?.id)).map((m) => <Alert key={`${m.criterion.stage}:${m.criterion.id}`} type={m.event?.values.judgment === 'WITHIN' ? 'info' : 'warning'} title={`${m.criterion.name} · ${m.message}`} description={`标准 ${m.limit_text}${m.event?.values.actual != null ? `；实测 ${m.event.values.actual} ${m.event.values.unit || ''}` : ''}`} action={<Button disabled={disabled} onClick={() => measure(m.criterion, m.event || undefined)}>{m.event ? '复测 / 更正' : '登记实测'}</Button>} />)}
    {card.baked_measurement_notice && <Alert type="info" title={card.baked_measurement_notice} />}
    {Boolean(card.bake_measurement_status?.length) && <Card size="small" title="二烤各次胶片检测（各次结果不通用）">{card.bake_measurement_status?.map((b) => <div key={b.id}><strong>{flowTime(b.business_at)} · 本包盘{b.trays?.join('、') || '未登记'} · {b.scope}</strong><div>{b.notice}</div>{b.checks.map((c, i) => <div key={i}>胶片{c.sample_ref} · 胶料{c.material_ref} · 取样盘{c.trays.join('、')}：{c.message}{c.actual != null ? `；实测${c.actual} ${c.unit || ''}` : ''}</div>)}</div>)}</Card>}
    {Boolean(card.baked_product_measurements?.length) && <Card size="small" title="烤后成品实测（独立事实，不代替胶片）">{card.baked_product_measurements?.map((e) => <div key={e.id}>{flowTime(e.business_at)} · {e.scope} · {e.values.actual != null ? `实测 ${e.values.actual} ${e.values.unit || ''}` : '未测 / 未判定'} · {String(e.values.measurement_message || '未判定')} · {e.employee_name}</div>)}</Card>}
  </section>
}

export function FlowEventSummary({ event: e, cardId, disabled, bakeMeasure, remeasure }: { event: FlowEvent; cardId: string; disabled: boolean; bakeMeasure: (e: FlowEvent, s: CouponSample) => void; remeasure: (c: Criterion, e: FlowEvent) => void }) {
  const c = e.values.standard_snapshot as Criterion | undefined
  const samples = (e.values.coupon_samples || []) as CouponSample[]
  return <Card size="small" title={`${FLOW_KIND[e.kind] || e.kind} · ${e.employee_name || '实际人员未登记'}`}>
    <div>业务时间：{flowTime(e.business_at)} · 录入：{flowTime(e.created_at)}</div>
    <div>范围：{e.scope}；依据：{e.evidence}</div>
    {e.kind==='WORK_GUIDANCE'&&<Typography.Paragraph>{String(e.values.note||'')} · 经手账号 #{e.recorded_by??'未登记'}。另记指示，不算实测或工序完成。</Typography.Paragraph>}
    {typeof e.values.oil_warning==='string'&&<Alert type="error" title={e.values.oil_warning} description="实际作业已保留，同时登记了关联异常；不自动更改实际洒油选择。" />}
    {e.values.actual != null && <Typography.Paragraph>实测 {String(e.values.actual)} {String(e.values.unit || '')} · {String(e.values.measurement_message || '未判定')}{c && `；标准 ${flowLimitText(c)}`}</Typography.Paragraph>}
    {e.kind === 'BAKE' && <><div>实际 {String(e.values.temperature_c)}°C × {String(e.values.minutes)}min</div>{!samples.some((s) => s.accompanied && s.card_ids.includes(cardId)) && <div>本次无代表本包的实际随烤胶片，胶片烤后硬度未测（不沿用上次）。</div>}{samples.filter((s) => s.accompanied).map((s, i) => <Space wrap key={i}><span>胶片 {s.sample_ref} · 胶料 {s.material_ref} · 盘{s.trays.join('、')} · 代表{s.card_ids.length}卡</span>{s.card_ids.includes(cardId) ? <Button disabled={disabled} onClick={() => bakeMeasure(e, s)}>记烤后硬度</Button> : <Typography.Text type="secondary">与本包无关，仅作历史展示，不能在此登记</Typography.Text>}</Space>)}</>}
    {e.followups?.map((f) => <div key={f.id}>已有后续复核：{f.message} · {f.employee_name || '处理人未登记'} · {flowTime(f.business_at)}。原事实保留，继续复核请选择最新记录。</div>)}
    {c && !e.followups?.length && <Button disabled={disabled} onClick={() => remeasure(c, e)}>复测 / 更正本次测量</Button>}
    {e.values.review_effect === 'HISTORICAL_ONLY' && <Alert type="warning" title="历史补录：实际时间较早或同一分钟，不解除较晚超限" />}
    {e.replaces_id && <div>{e.values.review_mode === 'CORRECTION' ? `更正原录入：${String(e.values.correction_reason || '')}` : '实际复测'}；原值、原处理人和时间保留。只有同范围且时间有效的判定或明确更正才解除原提醒。</div>}
    <details><summary>展开原始记录及关联编号</summary><pre>{JSON.stringify(e, null, 2)}</pre></details>
  </Card>
}
