import { Empty, Tag, Typography } from 'antd'
import dayjs from 'dayjs'
import type { AnalyticsDailyTrend, AnalyticsSource } from '../types'

export function SourceTag({ source }: { source: AnalyticsSource }) {
  const meta = {
    AUTOMATIC: { text: '系统自动', color: 'blue' },
    MANUAL: { text: '手工补录', color: 'orange' },
    COMBINED: { text: '自动 + 手工', color: 'purple' },
  }[source]
  return <Tag color={meta.color}>{meta.text}</Tag>
}

interface RankItem {
  key: string | number
  label: string
  value: number
  detail?: string
  source?: AnalyticsSource
}

export function RankBarChart({ items, valueSuffix = '', emptyText = '暂无排行数据' }: { items: RankItem[]; valueSuffix?: string; emptyText?: string }) {
  const visible = items.slice(0, 20)
  const maximum = Math.max(1, ...visible.map((item) => item.value))
  if (!visible.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={emptyText} />
  return (
    <div>
      <div className="analytics-rank-list">{visible.map((item, index) => (
        <div className="analytics-rank-row" key={item.key}>
          <span className="analytics-rank-index">{index + 1}</span>
          <div className="analytics-rank-main">
            <div className="analytics-rank-label">
              <span><strong>{item.label || '未填写'}</strong>{item.detail && <small>{item.detail}</small>}</span>
              <span>{item.source && <SourceTag source={item.source} />}<b>{item.value.toLocaleString('zh-CN')}{valueSuffix}</b></span>
            </div>
            <div className="analytics-rank-track"><span style={{ width: `${Math.max(2, (item.value / maximum) * 100)}%` }} /></div>
          </div>
        </div>
      ))}</div>
      {items.length > 20 && <Typography.Text className="analytics-rank-note" type="secondary">排行仅显示前20名，完整数据请查看明细表。</Typography.Text>}
    </div>
  )
}

const SERIES = [
  { key: 'produced_mold_count' as const, label: '生产模数', color: '#147356' },
  { key: 'inspection_quantity' as const, label: '质检数量', color: '#2f6f9f' },
  { key: 'shipped_quantity' as const, label: '出货数量', color: '#8b5fbf' },
  { key: 'returned_quantity' as const, label: '退回数量', color: '#cf5549' },
]

export function DailyLinkageChart({ rows }: { rows: AnalyticsDailyTrend[] }) {
  const active = rows.filter((row) => SERIES.some((series) => Number(row[series.key] || 0) > 0))
  if (!active.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="所选期间暂无生产、品检或出货趋势数据" />
  const width = Math.max(760, active.length * 42)
  const height = 270
  const left = 38
  const top = 24
  const bottom = 46
  const plotWidth = width - left - 18
  const plotHeight = height - top - bottom
  const pointsFor = (key: typeof SERIES[number]['key']) => {
    const max = Math.max(1, ...active.map((row) => Number(row[key] || 0)))
    return active.map((row, index) => {
      const x = left + (active.length === 1 ? plotWidth / 2 : (index / (active.length - 1)) * plotWidth)
      const y = top + plotHeight - (Number(row[key] || 0) / max) * plotHeight
      return { x, y, value: Number(row[key] || 0), date: row.date }
    })
  }
  const labelStep = Math.max(1, Math.ceil(active.length / 8))
  return (
    <div>
      <div className="analytics-chart-legend">
        {SERIES.map((series) => <span key={series.key}><i style={{ background: series.color }} />{series.label}</span>)}
        <Typography.Text type="secondary">各指标按自身峰值归一，仅比较变化趋势；悬停数据点查看实际数值。</Typography.Text>
      </div>
      <div className="analytics-chart-scroll">
        <svg className="analytics-line-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="生产、品检、出货和退回每日联动趋势">
          {[0, 0.25, 0.5, 0.75, 1].map((ratio) => (
            <line key={ratio} x1={left} x2={width - 18} y1={top + plotHeight * ratio} y2={top + plotHeight * ratio} stroke="#e1e9e7" strokeWidth="1" />
          ))}
          {SERIES.map((series) => {
            const points = pointsFor(series.key)
            return (
              <g key={series.key}>
                <polyline fill="none" stroke={series.color} strokeWidth="3" strokeLinejoin="round" strokeLinecap="round" points={points.map((point) => `${point.x},${point.y}`).join(' ')} />
                {points.map((point) => (
                  <circle key={`${series.key}-${point.date}`} cx={point.x} cy={point.y} r="4" fill="#fff" stroke={series.color} strokeWidth="2">
                    <title>{`${dayjs(point.date).format('MM-DD')} ${series.label}：${point.value.toLocaleString('zh-CN')}`}</title>
                  </circle>
                ))}
              </g>
            )
          })}
          {active.map((row, index) => index % labelStep === 0 || index === active.length - 1 ? (
            <text key={row.date} x={left + (active.length === 1 ? plotWidth / 2 : (index / (active.length - 1)) * plotWidth)} y={height - 18} textAnchor="middle" fill="#718680" fontSize="11">{dayjs(row.date).format('MM-DD')}</text>
          ) : null)}
        </svg>
      </div>
    </div>
  )
}

const FINANCE_SERIES = [
  { key: 'revenue' as const, label: '收入', color: '#2f806b' },
  { key: 'total_cost' as const, label: '成本', color: '#c58a3e' },
  { key: 'profit' as const, label: '利润', color: '#5b6fb2' },
]

export function DailyProfitChart({ rows }: { rows: AnalyticsDailyTrend[] }) {
  const active = rows.filter((row) => FINANCE_SERIES.some((series) => Number(row[series.key] || 0) !== 0))
  if (!active.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="所选期间暂无结算或手工收支趋势" />
  const width = Math.max(760, active.length * 42)
  const height = 260
  const left = 50
  const top = 20
  const bottom = 42
  const plotWidth = width - left - 18
  const plotHeight = height - top - bottom
  const values = active.flatMap((row) => FINANCE_SERIES.map((series) => Number(row[series.key] || 0)))
  const minimum = Math.min(0, ...values)
  const maximum = Math.max(1, ...values)
  const range = Math.max(1, maximum - minimum)
  const yFor = (value: number) => top + ((maximum - value) / range) * plotHeight
  const xFor = (index: number) => left + (active.length === 1 ? plotWidth / 2 : (index / (active.length - 1)) * plotWidth)
  const zeroY = yFor(0)
  const labelStep = Math.max(1, Math.ceil(active.length / 8))
  return (
    <div>
      <div className="analytics-chart-legend">
        {FINANCE_SERIES.map((series) => <span key={series.key}><i style={{ background: series.color }} />{series.label}</span>)}
        <Typography.Text type="secondary">利润低于0时会落在零线下方；悬停数据点查看实际金额。</Typography.Text>
      </div>
      <div className="analytics-chart-scroll">
        <svg className="analytics-line-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="每日收入、成本和利润趋势">
          <line x1={left} x2={width - 18} y1={zeroY} y2={zeroY} stroke="#80938e" strokeDasharray="5 4" />
          <text x={left - 7} y={Math.max(12, zeroY + 4)} textAnchor="end" fill="#718680" fontSize="10">¥0</text>
          {FINANCE_SERIES.map((series) => {
            const points = active.map((row, index) => ({ x: xFor(index), y: yFor(Number(row[series.key] || 0)), value: Number(row[series.key] || 0), date: row.date }))
            return <g key={series.key}><polyline fill="none" stroke={series.color} strokeWidth="3" strokeLinejoin="round" strokeLinecap="round" points={points.map((point) => `${point.x},${point.y}`).join(' ')} />{points.map((point) => <circle key={`${series.key}-${point.date}`} cx={point.x} cy={point.y} r="4" fill="#fff" stroke={series.color} strokeWidth="2"><title>{`${dayjs(point.date).format('MM-DD')} ${series.label}：¥${point.value.toLocaleString('zh-CN')}`}</title></circle>)}</g>
          })}
          {active.map((row, index) => index % labelStep === 0 || index === active.length - 1 ? <text key={row.date} x={xFor(index)} y={height - 15} textAnchor="middle" fill="#718680" fontSize="11">{dayjs(row.date).format('MM-DD')}</text> : null)}
        </svg>
      </div>
    </div>
  )
}

export function FinanceSourceComparison({ automatic, manual, total }: { automatic: { revenue: number; cost: number; profit: number }; manual: { revenue: number; cost: number; profit: number }; total: { revenue: number; cost: number; profit: number } }) {
  const rows = [
    { key: 'automatic', label: '系统自动', ...automatic },
    { key: 'manual', label: '手工补录', ...manual },
    { key: 'total', label: '合计', ...total },
  ]
  const max = Math.max(1, ...rows.flatMap((row) => [row.revenue, row.cost].map(Math.abs)))
  return (
    <div className="analytics-finance-comparison">
      {rows.map((row) => (
        <div className={`finance-source-row ${row.key}`} key={row.key}>
          <strong>{row.label}</strong>
          <div className="finance-source-bars">
            <span className="income" style={{ width: `${Math.max(row.revenue ? 3 : 0, (Math.abs(row.revenue) / max) * 100)}%` }}>收入 ¥{row.revenue.toLocaleString('zh-CN')}</span>
            <span className="cost" style={{ width: `${Math.max(row.cost ? 3 : 0, (Math.abs(row.cost) / max) * 100)}%` }}>成本 ¥{row.cost.toLocaleString('zh-CN')}</span>
          </div>
          <b className={row.profit < 0 ? 'negative-value' : 'profit-value'}>利润 ¥{row.profit.toLocaleString('zh-CN')}</b>
        </div>
      ))}
    </div>
  )
}
