import { render, screen } from '@testing-library/react'
import type { AnalyticsDailyTrend } from '../types'
import { DailyLinkageChart, DailyProfitChart, RankBarChart } from './AnalyticsCharts'

describe('analytics charts', () => {
  it('renders a linked daily trend with real values in accessible SVG titles', () => {
    const row = {
      date: '2026-07-14', produced_mold_count: 120, inspection_quantity: 430, shipped_quantity: 400, returned_quantity: 3,
      automatic_produced_mold_count: 100, manual_produced_mold_count: 20, theoretical_output_quantity: 480,
      automatic_equivalent_hours: 8, manual_reported_hours: 2,
      qualified_quantity: 420, defective_quantity: 10, reworked_quantity: 3, recovered_quantity: 2, scrap_quantity: 1,
      automatic_revenue: 1000, manual_revenue: 0, revenue: 1000, automatic_total_cost: 700, manual_total_cost: 0,
      total_cost: 700, automatic_profit: 300, manual_profit: 0, profit: 300, material_cost: 0, labor_cost: 0,
      energy_cost: 0, other_cost: 0,
    } as AnalyticsDailyTrend
    render(<DailyLinkageChart rows={[row]} />)

    expect(screen.getByRole('img', { name: '生产、品检、出货和退回每日联动趋势' })).toBeInTheDocument()
    expect(screen.getByText('各指标按自身峰值归一，仅比较变化趋势；悬停数据点查看实际数值。')).toBeInTheDocument()
  })

  it('labels mixed automatic and manual ranking sources and explains the top-20 limit', () => {
    render(<RankBarChart items={Array.from({ length: 21 }, (_, index) => ({ key: index, label: `员工${index + 1}`, value: 21 - index, source: 'COMBINED' as const }))} valueSuffix=" 模" />)
    expect(screen.getAllByText('自动 + 手工')).toHaveLength(20)
    expect(screen.getByText('排行仅显示前20名，完整数据请查看明细表。')).toBeInTheDocument()
    expect(screen.queryByText('员工21')).not.toBeInTheDocument()
  })

  it('draws revenue, cost and negative profit around a visible zero line', () => {
    const row = {
      date: '2026-07-14', revenue: 1000, total_cost: 1200, profit: -200,
      produced_mold_count: 0, automatic_produced_mold_count: 0, manual_produced_mold_count: 0, theoretical_output_quantity: 0,
      automatic_equivalent_hours: 0, manual_reported_hours: 0, inspection_quantity: 0, qualified_quantity: 0,
      defective_quantity: 0, shipped_quantity: 0, returned_quantity: 0, reworked_quantity: 0, recovered_quantity: 0,
      scrap_quantity: 0, automatic_revenue: 1000, manual_revenue: 0, automatic_total_cost: 1200, manual_total_cost: 0,
      automatic_profit: -200, manual_profit: 0, material_cost: 0, labor_cost: 0, energy_cost: 0, other_cost: 0,
    } as AnalyticsDailyTrend
    render(<DailyProfitChart rows={[row]} />)
    expect(screen.getByRole('img', { name: '每日收入、成本和利润趋势' })).toBeInTheDocument()
    expect(screen.getByText('利润低于0时会落在零线下方；悬停数据点查看实际金额。')).toBeInTheDocument()
  })
})
