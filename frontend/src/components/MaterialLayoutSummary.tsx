import { materialLayout, parameterNumber, type MaterialLayoutRecord } from '../materialLayout'

export function MaterialLayoutSummary({ record }: { record: MaterialLayoutRecord }) {
  const layout = materialLayout(record)
  return (
    <div className="material-layout-summary">
      <dl className="production-parameter-grid material-layout-grid" aria-label="一模排料参数">
        <div><dt>大条条重</dt><dd>{parameterNumber(record.large_strip_weight_g, 'g')}</dd></div>
        <div><dt>大条数量</dt><dd>{parameterNumber(record.large_strip_count, '条')}</dd></div>
        <div><dt>小条条重</dt><dd>{parameterNumber(record.small_strip_weight_g, 'g')}</dd></div>
        <div><dt>小条数量</dt><dd>{parameterNumber(record.small_strip_count, '条')}</dd></div>
        {layout.legacy && <>
          <div><dt>历史单条条重</dt><dd>{parameterNumber(record.strip_weight_kg == null ? undefined : Number(record.strip_weight_kg) * 1000, 'g')}</dd></div>
          <div><dt>历史每批条数</dt><dd>{parameterNumber(record.strips_per_batch, '条')}</dd></div>
        </>}
        <div className="material-layout-total"><dt>一模排料总重</dt><dd>{layout.incomplete ? '参数不完整，暂不计算' : parameterNumber(layout.total, 'g')}</dd></div>
      </dl>
      {layout.legacy && <small className="production-parameter-hint">历史单条记录已换算为 g，总重按历史每批条数折算供核对；未推断大条、小条。</small>}
    </div>
  )
}
