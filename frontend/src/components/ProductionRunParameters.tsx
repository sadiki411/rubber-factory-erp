import { parameterNumber, parameterText } from '../materialLayout'
import type { ProductionRun } from '../types'
import { MaterialLayoutSummary } from './MaterialLayoutSummary'

export function ProductionRunParameters({ run, compact = false }: { run: ProductionRun; compact?: boolean }) {
  const otherParameters = <dl className="production-parameter-grid" aria-label="其他任务生产参数">
    <div><dt>胶料尺寸</dt><dd>{parameterText(run.compound_size)}</dd></div>
    <div><dt>本次有效孔数</dt><dd>{parameterNumber(run.cavities, '孔')}</dd></div>
    <div><dt>硫化时间</dt><dd>{parameterNumber(run.curing_seconds, '秒')}</dd></div>
    <div><dt>预计生产工时</dt><dd>{parameterNumber(run.estimated_hours, '小时')}</dd></div>
    <div><dt>本次合计生产数量</dt><dd>{parameterNumber(run.order_quantity, '件')}</dd></div>
    <div><dt>{run.estimated_defect_mode === 'QUANTITY' ? '预估不良件数' : '预估不良率'}</dt><dd>{run.estimated_defect_mode === 'QUANTITY' ? parameterNumber(run.estimated_defect_quantity, '件') : parameterNumber(run.estimated_defect_rate, '%')}</dd></div>
    <div><dt>默认作业员</dt><dd>{parameterText(run.operator)}</dd></div>
    <div className="production-parameter-wide"><dt>任务备注</dt><dd>{parameterText(run.notes)}</dd></div>
  </dl>
  return (
    <section className="production-run-parameters" aria-label="任务生产参数">
      <strong>本任务生产参数</strong>
      <MaterialLayoutSummary record={run} />
      {compact ? <details><summary>其他生产参数</summary>{otherParameters}</details> : otherParameters}
      {!!run.order_allocations?.length && <details open={!compact}>
        <summary>关联生产订单（{run.order_allocations.length}）</summary>
        <ul className="production-parameter-orders">{run.order_allocations.map((order) => <li key={order.id}>
          <strong>{order.order_no}{order.item_no ? ` / ${order.item_no}` : ''}</strong>
          <span>交期 {order.due_date || '未填写'} · 本次计划 {parameterNumber(order.planned_quantity, '件')} · 已分配 {parameterNumber(order.allocated_quantity, '件')}</span>
        </li>)}</ul>
      </details>}
      {run.pause_note && <p className="production-parameter-hint">暂停备注：{run.pause_note}</p>}
      {!compact && <small className="production-parameter-hint">以上为任务已保存参数；未填写项不会用产品当前资料替换。二烤等产品参考条件不代表本批已执行。</small>}
    </section>
  )
}
