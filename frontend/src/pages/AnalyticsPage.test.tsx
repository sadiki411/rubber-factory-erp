import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import { AnalyticsPage } from './AnalyticsPage'

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({ matches: !query.includes('max-width'), media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })),
})
class ResizeObserverMock { observe() {} unobserve() {} disconnect() {} }
globalThis.ResizeObserver = ResizeObserverMock

const apiMocks = vi.hoisted(() => ({
  dashboard: vi.fn(), listManualEntries: vi.fn(), listFinancialEntries: vi.fn(), voidManualEntry: vi.fn(), voidFinancialEntry: vi.fn(),
  stations: vi.fn(), machines: vi.fn(), employees: vi.fn(),
}))

vi.mock('../api/client', () => ({
  analyticsApi: {
    dashboard: apiMocks.dashboard,
    listManualEntries: apiMocks.listManualEntries,
    listFinancialEntries: apiMocks.listFinancialEntries,
    voidManualEntry: apiMocks.voidManualEntry,
    voidFinancialEntry: apiMocks.voidFinancialEntry,
  },
  productionApi: { stations: apiMocks.stations },
  qualityApi: { listEmployees: apiMocks.employees },
  masterApi: () => ({ list: apiMocks.machines }),
  toList: (payload: any) => Array.isArray(payload) ? payload : payload.results,
}))

const productionMetrics = {
  produced_mold_count: 120, theoretical_output_quantity: 480, automatic_equivalent_hours: '8.00',
  automatic_actual_machine_hours: '10.00', manual_reported_hours: '2.00', molds_per_equivalent_hour: '15.00',
  molds_per_reported_hour: null, efficiency_percent: '80.00',
}
const qualityMetrics = {
  inspection_quantity: 480, qualified_quantity: 470, defective_quantity: 10, shipped_quantity: 450,
  returned_quantity: 4, reworked_quantity: 4, recovered_quantity: 3, scrap_quantity: 1,
  first_pass_rate: '97.92', return_rate: '0.89', rework_pass_rate: '75.00',
}
const financeMetrics = { revenue: '12000.00', material_cost: '4000.00', labor_cost: '1500.00', energy_cost: '500.00', other_cost: '0.00', total_cost: '6000.00', profit: '6000.00', profit_margin: '50.00' }

describe('AnalyticsPage', () => {
  beforeEach(() => {
    apiMocks.stations.mockResolvedValue([{ id: 1, code: 'M-01', group: 'Z', position_no: 1, is_active: true, machine: { id: 7, code: 'M-01', name: '扩展机台', is_active: true } }])
    apiMocks.machines.mockResolvedValue([{ id: 7, code: 'M-01', name: '扩展机台', is_active: true }])
    apiMocks.employees.mockResolvedValue([])
    apiMocks.listManualEntries.mockResolvedValue([])
    apiMocks.listFinancialEntries.mockResolvedValue([])
    apiMocks.dashboard.mockResolvedValue({
      period: { date_from: '2026-07-01', date_to: '2026-07-14', month: '2026-07' },
      data_basis: { production_quantity_date: 'date', automatic_production_hours: '折算', automatic_actual_machine_hours: '机时', manual_production_hours: '填报', automatic_finance_date: 'settled_at', manual_finance_date: 'occurred_on', quality_date: 'shipment_date', rework_date: 'rework_date', order_link: 'text', quality_filter_scope: 'date', zero_denominator_rate: null },
      sources: { production: { automatic: 2, manual: 1, total: 3 }, quality: { automatic: 1, manual: 0, total: 1 }, rework: { automatic: 0, manual: 0, total: 0 }, finance: { automatic: 1, manual: 1, total: 2 } },
      production: { automatic: productionMetrics, manual: { ...productionMetrics, efficiency_percent: null }, total: productionMetrics, production_days: 2, operator_count: 1, run_count: 3, settled_run_count: 1, unsettled_completed_run_count: 2, status_counts: { PLANNED: 0, RUNNING: 0, COMPLETED: 3, CANCELLED: 0 }, settled_good_quantity: 470, settled_defective_quantity: 10 },
      finance: { automatic: financeMetrics, manual: { ...financeMetrics, revenue: '1000.00', total_cost: '200.00', profit: '800.00' }, total: financeMetrics },
      quality: { automatic: qualityMetrics, manual: { ...qualityMetrics, inspection_quantity: 0 }, total: qualityMetrics, shipment_count: 1, rework_count: 0 },
      daily_trend: [],
      operator_performance: [{ operator: '张三', automatic_mold_count: 100, manual_mold_count: 20, total_mold_count: 120, theoretical_output_quantity: 480, automatic_equivalent_hours: '8', manual_reported_hours: '2', automatic_molds_per_equivalent_hour: '12.5', manual_molds_per_reported_hour: '10', average_daily_mold_count: '60', production_days: 2, participated_run_count: 1, automatic_record_count: 2, manual_record_count: 1, source: 'COMBINED' }],
      station_performance: [], quality_employee_performance: [], defect_reason_breakdown: [{ reason_category: 'APPEARANCE', reason_category_display: '外观', returned_quantity: 4, reworked_quantity: 4, recovered_quantity: 3, scrap_quantity: 1, rework_hours: '2', share_of_returns: '100', rework_pass_rate: '75', automatic_record_count: 1, manual_record_count: 0, source: 'AUTOMATIC' }], order_performance: [], manual_entries: [], manual_financial_entries: [],
    })
  })

  it('shows a separate transparent analysis board using dynamic data', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><App><AnalyticsPage /></App></QueryClientProvider>)

    expect(await screen.findByText('数据分析与绩效')).toBeInTheDocument()
    expect((await screen.findAllByText('张三')).length).toBeGreaterThan(0)
    expect(screen.getByText('合计收入')).toBeInTheDocument()
    expect(screen.getByText('有 2 个已完成订单尚未结算')).toBeInTheDocument()
    expect(screen.getByText('这些订单尚未计入系统自动收入、成本和利润；完成结算后，本页数据会自动更新。')).toBeInTheDocument()
    expect(document.body).toHaveTextContent(/自动\s*2/)
    expect(screen.getByRole('button', { name: /补录绩效/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /记录收支/ })).toBeInTheDocument()
    expect(screen.queryByText(/13人|固定三组|共6台/)).not.toBeInTheDocument()
    await userEvent.setup().click(screen.getByText('全部机台分组'))
    await userEvent.setup().click(await screen.findByText('Z组'))
    await waitFor(() => {
      expect(apiMocks.listManualEntries).toHaveBeenLastCalledWith(expect.objectContaining({ group: 'Z' }))
      expect(apiMocks.listFinancialEntries).toHaveBeenLastCalledWith(expect.objectContaining({ group: 'Z' }))
    })
    await userEvent.setup().click(screen.getByRole('tab', { name: '品检与返工绩效' }))
    expect(await screen.findByText('外观')).toBeInTheDocument()
  }, 15_000)
})
