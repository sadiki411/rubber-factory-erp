import {
  AuditOutlined,
  BarChartOutlined,
  ClockCircleOutlined,
  DollarOutlined,
  EditOutlined,
  EyeOutlined,
  FormOutlined,
  SendOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import { Alert, App, Button, Card, Col, DatePicker, Drawer, Empty, Grid, Popconfirm, Row, Select, Space, Spin, Statistic, Table, Tabs, Tag, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import dayjs, { type Dayjs } from 'dayjs'
import { useMemo, useState } from 'react'
import { analyticsApi, masterApi, productionApi, qualityApi, toList } from '../api/client'
import { DailyLinkageChart, DailyProfitChart, FinanceSourceComparison, RankBarChart, SourceTag } from '../components/AnalyticsCharts'
import { ManualFinancialDrawer, ManualPerformanceDrawer } from '../components/AnalyticsEntryDrawers'
import { PageTitle } from '../components/PageTitle'
import type {
  AnalyticsOperatorPerformance,
  AnalyticsOrderPerformance,
  AnalyticsQualityProductPerformance,
  AnalyticsQualityEmployeePerformance,
  AnalyticsQualityWorkRecord,
  AnalyticsStationPerformance,
  Machine,
  ManualFinancialEntry,
  ManualPerformanceEntry,
} from '../types'

function numberValue(value: number | string | null | undefined) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

function numberText(value: number | string | null | undefined, digits = 0) {
  if (value === null || value === undefined || value === '') return '—'
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed.toLocaleString('zh-CN', { maximumFractionDigits: digits }) : '—'
}

function rateText(value: number | string | null | undefined) {
  return value === null || value === undefined || value === '' ? '—' : `${numberText(value, 2)}%`
}

function moneyText(value: number | string | null | undefined) {
  return `¥${numberText(value, 2)}`
}

function reworkMetricText(row: { rework_record_count?: number; reworked_quantity?: number | string }) {
  return row.rework_record_count
    ? `返工${numberText(row.reworked_quantity)}`
    : '返工未登记'
}

function manualHoursText(row: { manual_record_count?: number; manual_reported_hours?: number | string }) {
  if (!row.manual_record_count) return '未登记'
  // Legacy forms defaulted hours to 0. A row proves a manual event exists,
  // but cannot prove that its default zero was an explicit actual-hours entry.
  return Number(row.manual_reported_hours) === 0
    ? '0 h（记录值，含义待核对）'
    : `${numberText(row.manual_reported_hours, 2)} h`
}

const WORK_STATUS: Record<string, string> = { CONFIRMED: '已确认', RESHIPPED: '已重新出货', WAITING_REWORK: '待返工', REWORKING: '正在返工', WAITING_REINSPECTION: '待复检', READY_TO_RESHIP: '待重新出货', SCRAPPED: '已报废', CANCELLED: '已取消', COMPLETED: '已完成', LEGACY: '历史记录', MANUAL: '手工补录' }
const productLabel = (row: AnalyticsQualityProductPerformance | AnalyticsQualityWorkRecord) => row.product_name || (row.product_code ? `产品编号 ${row.product_code}` : row.specification ? `${row.specification}（名称未填写）` : '产品身份待关联')
const productFilterKey = (row: AnalyticsQualityProductPerformance | AnalyticsQualityWorkRecord): string => row.product_key || JSON.stringify([row.product_name, row.specification, row.material])

function workReworkText(row: AnalyticsQualityWorkRecord) {
  const returned = numberText(row.returned_quantity)
  if (row.event_type === 'REWORK' || row.event_type === 'MANUAL_REWORK') return `${returned} / ${numberText(row.reworked_quantity)} / ${numberText(row.recovered_quantity)} / ${numberText(row.scrap_quantity)}`
  return `${returned} / ${row.event_type === 'RETURN_RESPONSIBILITY' ? '返工作业未登记在此记录' : '不适用（出货记录）'}`
}

function workInspectionText(row: AnalyticsQualityWorkRecord) {
  if (row.inspection_quantity != null) return `${numberText(row.inspection_quantity)} / ${numberText(row.qualified_quantity)} / ${numberText(row.defective_quantity)}`
  return row.event_type === 'SHIPMENT' || row.event_type === 'MANUAL_QUALITY' ? '未登记' : '不适用（退回/返工记录）'
}

function workWeightText(row: AnalyticsQualityWorkRecord) {
  return row.net_weight_kg == null ? row.source_weight_kg != null ? `原退货整批 ${numberText(row.source_weight_kg, 3)} kg（来源参考，不按项次累加，非另一次实称）` : '实称重量未登记' : `${numberText(row.net_weight_kg, 3)} kg`
}

const ENTRY_META = {
  PRODUCTION: { label: '生产', color: 'blue' },
  QUALITY: { label: '品检 / 出货', color: 'green' },
  REWORK: { label: '退回 / 返工', color: 'orange' },
} as const

const FINANCE_CATEGORY: Record<string, string> = {
  SALES: '销售', MATERIAL: '材料', LABOR: '人工', ENERGY: '能耗', OTHER: '其他', ADJUSTMENT: '调整',
}

function performanceSummary(entry: ManualPerformanceEntry) {
  if (entry.entry_type === 'PRODUCTION') return `${numberText(entry.produced_mold_count)} 模 · 填报 ${numberText(entry.production_hours, 2)} 小时`
  if (entry.entry_type === 'QUALITY') return `质检 ${numberText(entry.inspection_quantity)} · 合格 ${numberText(entry.qualified_quantity)} · 出货 ${numberText(entry.shipped_quantity)}`
  return `退回 ${numberText(entry.returned_quantity)} · 返工 ${numberText(entry.reworked_quantity)} · 合格 ${numberText(entry.recovered_quantity)}`
}

export function AnalyticsPage() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const screens = Grid.useBreakpoint()
  const mobile = screens.md === false
  const [month, setMonth] = useState<Dayjs>(dayjs().startOf('month'))
  const [group, setGroup] = useState(new URLSearchParams(window.location.search).get('group') || '')
  const [machineId, setMachineId] = useState<number | undefined>(() => Number(new URLSearchParams(window.location.search).get('machine_id')) || undefined)
  const [activeTab, setActiveTab] = useState(new URLSearchParams(window.location.search).get('tab') || (new URLSearchParams(window.location.search).has('employee_id') ? 'quality' : 'production'))
  const [performanceForm, setPerformanceForm] = useState<{ entry?: ManualPerformanceEntry }>()
  const [financialForm, setFinancialForm] = useState<{ entry?: ManualFinancialEntry }>()
  const [qualityDetailEmployeeId, setQualityDetailEmployeeId] = useState<number | undefined>(() => Number(new URLSearchParams(window.location.search).get('employee_id')) || undefined)
  const [qualityProductFilter, setQualityProductFilter] = useState<string | undefined>(new URLSearchParams(window.location.search).get('product_filter') || undefined)
  const [customRange, setCustomRange] = useState<[Dayjs, Dayjs] | undefined>(() => {
    const params = new URLSearchParams(window.location.search)
    const from = dayjs(params.get('date_from'))
    const to = dayjs(params.get('date_to'))
    return params.has('date_from') && params.has('date_to') && from.isValid() && to.isValid() ? [from, to] : undefined
  })
  const dateFrom = (customRange?.[0] || month.startOf('month')).format('YYYY-MM-DD')
  const dateTo = customRange?.[1].format('YYYY-MM-DD') || (month.isSame(dayjs(), 'month') ? dayjs().format('YYYY-MM-DD') : month.endOf('month').format('YYYY-MM-DD'))

  const dashboardQuery = useQuery({
    queryKey: ['analytics', 'dashboard', { dateFrom, dateTo, group, machineId }],
    queryFn: () => analyticsApi.dashboard({ date_from: dateFrom, date_to: dateTo, group: group || undefined, machine_id: machineId }),
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
  })
  const stationsQuery = useQuery({ queryKey: ['production', 'stations'], queryFn: async () => toList(await productionApi.stations()) })
  const machinesQuery = useQuery({ queryKey: ['machines'], queryFn: async () => toList(await masterApi<Machine>('machines').list()) })
  const employeesQuery = useQuery({ queryKey: ['quality', 'employees'], queryFn: async () => toList(await qualityApi.listEmployees({ page_size: 1000 })) })
  const manualQuery = useQuery({
    queryKey: ['analytics', 'manual-entries', dateFrom, dateTo, group, machineId],
    queryFn: async () => toList(await analyticsApi.listManualEntries({ date_from: dateFrom, date_to: dateTo, group: group || undefined, machine_id: machineId, include_voided: true, page_size: 1000 })),
    enabled: activeTab === 'manual',
  })
  const financialQuery = useQuery({
    queryKey: ['analytics', 'financial-entries', dateFrom, dateTo, group, machineId],
    queryFn: async () => toList(await analyticsApi.listFinancialEntries({ date_from: dateFrom, date_to: dateTo, group: group || undefined, machine_id: machineId, include_voided: true, page_size: 1000 })),
    enabled: activeTab === 'manual',
  })
  const qualityDetailQuery = useQuery({
    queryKey: ['analytics', 'quality-employee-details', qualityDetailEmployeeId, dateFrom, dateTo],
    queryFn: () => analyticsApi.qualityEmployeeDetails({
      quality_employee_id: qualityDetailEmployeeId!,
      date_from: dateFrom,
      date_to: dateTo,
    }),
    enabled: Boolean(qualityDetailEmployeeId),
  })

  const data = dashboardQuery.data
  const qualityDetailEmployee = data?.quality_employee_performance.find((row) => row.employee_id === qualityDetailEmployeeId)
  const machines = machinesQuery.data || []
  const employees = employeesQuery.data || []
  const groups = useMemo(() => Array.from(new Set((stationsQuery.data || []).map((item) => item.group).filter(Boolean))).sort(), [stationsQuery.data])
  const visibleMachines = machines.filter((machine) => {
    if (!group) return true
    return (stationsQuery.data || []).some((station) => station.group === group && station.machine?.id === machine.id)
  })

  const finance = data?.finance
  const financeTotal = finance?.total
  const hasFinanceRecords = Boolean(data?.sources.finance.total)
  const production = data?.production.total
  const quality = data?.quality.total
  const qualityAttribution = data?.quality_attribution
  const workRecords = (qualityDetailQuery.data?.records || []).filter((row) => !qualityProductFilter || productFilterKey(row) === qualityProductFilter)
  const returnParams = new URLSearchParams({ date_from: dateFrom, date_to: dateTo, tab: activeTab })
  if (group) returnParams.set('group', group)
  if (machineId) returnParams.set('machine_id', String(machineId))
  if (qualityDetailEmployeeId) returnParams.set('employee_id', String(qualityDetailEmployeeId))
  if (qualityProductFilter) returnParams.set('product_filter', qualityProductFilter)
  const analyticsReturn = encodeURIComponent(`/analytics?${returnParams}`)
  const orderLink = (id: number) => `/orders?order_id=${id}&return_to=${analyticsReturn}`
  const sourceLink = (row: AnalyticsQualityWorkRecord) => row.source_type === 'REWORK_CASE' || row.source_type === 'REWORK_ATTEMPT' || row.source_type === 'LEGACY_REWORK'
    ? `/quality?tab=reworks&q=${encodeURIComponent(row.source_no)}&date_from=${dateFrom}&date_to=${dateTo}&return_to=${analyticsReturn}`
    : row.shipment_batch_id
    ? `/quality?shipment_batch_id=${row.shipment_batch_id}&date_from=${dateFrom}&date_to=${dateTo}&return_to=${analyticsReturn}`
    : `/quality?q=${encodeURIComponent(row.source_no)}&date_from=${dateFrom}&date_to=${dateTo}&return_to=${analyticsReturn}`
  const cardReferences = (row: AnalyticsQualityWorkRecord) => {
    const cards = row.process_cards?.length ? row.process_cards : row.process_card_no ? [{ id: 0, card_no: row.process_card_no }] : []
    return cards.length ? <Space wrap>{cards.map((card) => <a key={card.card_no} href={`/quality?q=${encodeURIComponent(card.card_no)}&return_to=${analyticsReturn}`}>{card.card_no}</a>)}</Space> : '流程卡未关联（无已存关系）'
  }
  const profitMargin = financeTotal?.profit_margin

  const voidPerformance = useMutation({
    mutationFn: (entry: ManualPerformanceEntry) => analyticsApi.voidManualEntry(entry.id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['analytics'] })
      message.success('绩效补录已作废，历史仍保留')
    },
    onError: (error: Error) => message.error(error.message),
  })
  const voidFinance = useMutation({
    mutationFn: (entry: ManualFinancialEntry) => analyticsApi.voidFinancialEntry(entry.id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['analytics'] })
      message.success('收支记录已作废，历史仍保留')
    },
    onError: (error: Error) => message.error(error.message),
  })
  const restorePerformance = useMutation({
    mutationFn: (entry: ManualPerformanceEntry) => analyticsApi.restoreManualEntry(entry.id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['analytics'] })
      message.success('绩效补录已恢复并重新计入分析')
    },
    onError: (error: Error) => message.error(error.message),
  })
  const restoreFinance = useMutation({
    mutationFn: (entry: ManualFinancialEntry) => analyticsApi.restoreFinancialEntry(entry.id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['analytics'] })
      message.success('收支记录已恢复并重新计入利润')
    },
    onError: (error: Error) => message.error(error.message),
  })

  const operatorColumns: TableColumnsType<AnalyticsOperatorPerformance> = [
    { title: '作业员', dataIndex: 'operator', fixed: 'left', width: 130, render: (value) => <strong>{value || '未填写'}</strong> },
    { title: '来源', dataIndex: 'source', width: 125, render: (value) => <SourceTag source={value} /> },
    { title: '自动 / 手工 / 合计模数', key: 'molds', width: 190, render: (_, row) => `${numberText(row.automatic_mold_count)} / ${numberText(row.manual_mold_count)} / ${numberText(row.total_mold_count)}` },
    { title: '生产天数', dataIndex: 'production_days', width: 105, render: (value) => `${numberText(value)} 天` },
    { title: '折算工时', dataIndex: 'automatic_equivalent_hours', width: 110, render: (value) => `${numberText(value, 2)} h` },
    { title: '填报工时', dataIndex: 'manual_reported_hours', width: 110, render: (_, row) => manualHoursText(row) },
    { title: '折算模数/小时（非实测）', dataIndex: 'automatic_molds_per_equivalent_hour', width: 180, render: (value) => value === null ? '—' : `${numberText(value, 2)} 模/h` },
    { title: '手工效率', dataIndex: 'manual_molds_per_reported_hour', width: 110, render: (value) => value === null ? '—' : `${numberText(value, 2)} 模/h` },
  ]
  const stationColumns: TableColumnsType<AnalyticsStationPerformance> = [
    { title: '机台', key: 'machine', fixed: 'left', width: 150, render: (_, row) => <span><strong>{row.machine_code || row.station_code || '未关联'}</strong><br /><Typography.Text type="secondary">{row.machine_name || (row.group ? `${row.group}组` : '-')}</Typography.Text></span> },
    { title: '来源', dataIndex: 'source', width: 125, render: (value) => <SourceTag source={value} /> },
    { title: '自动 / 手工 / 合计模数', key: 'molds', width: 190, render: (_, row) => `${numberText(row.automatic_mold_count)} / ${numberText(row.manual_mold_count)} / ${numberText(row.total_mold_count)}` },
    { title: '折算 / 上下机间隔 / 填报工时', key: 'hours', width: 245, render: (_, row) => `${numberText(row.automatic_equivalent_hours, 2)} / ${row.actual_machine_time_record_count ? numberText(row.automatic_actual_machine_hours, 2) : '未登记'} / ${manualHoursText(row)}` },
    { title: '自动机台效率', dataIndex: 'automatic_efficiency_percent', width: 125, render: rateText },
    { title: '订单数', dataIndex: 'run_count', width: 90, render: (value) => numberText(value) },
    { title: '收入', dataIndex: 'revenue', width: 120, render: (value, row) => row.finance_record_count ? moneyText(value) : '未录入' },
    { title: '成本', dataIndex: 'total_cost', width: 120, render: (value, row) => row.finance_record_count ? moneyText(value) : '未录入' },
    { title: '利润', dataIndex: 'profit', width: 125, render: (value, row) => row.finance_record_count ? moneyText(value) : '未核算' },
  ]
  const qualityEmployeeColumns: TableColumnsType<AnalyticsQualityEmployeePerformance> = [
    { title: '员工', key: 'employee', fixed: 'left', width: 150, render: (_, row) => <span><strong>{row.name || '未填写'}</strong><br /><Typography.Text type="secondary">{row.employee_no || row.team || '-'}</Typography.Text></span> },
    { title: '来源', dataIndex: 'source', width: 125, render: (value) => <SourceTag source={value} /> },
    { title: '质检 / 合格 / 不良', key: 'inspection', width: 175, render: (_, row) => row.inspection_record_count ? `${numberText(row.inspection_quantity)} / ${numberText(row.qualified_quantity)} / ${numberText(row.defective_quantity)}` : <Typography.Text type="secondary">未登记</Typography.Text> },
    { title: '归属出货', dataIndex: 'shipped_quantity', width: 105, render: (value) => numberText(value) },
    { title: '参与出货', dataIndex: 'participated_shipped_quantity', width: 105, render: (value, row) => <span>{numberText(value)}{row.collaborative_shipped_quantity ? <><br /><Typography.Text type="warning">协作未分摊 {numberText(row.collaborative_shipped_quantity)}</Typography.Text></> : null}</span> },
    { title: '责任退回', dataIndex: 'responsible_return_quantity', width: 110, render: (value) => <span className={value ? 'quality-danger-text' : ''}>{numberText(value)}</span> },
    { title: '返工 / 合格 / 报废', key: 'rework', width: 175, render: (_, row) => row.rework_record_count ? `${numberText(row.reworked_quantity)} / ${numberText(row.recovered_quantity)} / ${numberText(row.scrap_quantity)}` : '返工未登记' },
    { title: '一次合格率', dataIndex: 'first_pass_rate', width: 115, render: rateText },
    { title: '返工通过率', dataIndex: 'rework_pass_rate', width: 115, render: rateText },
    { title: '明细', key: 'detail', fixed: 'right', width: 120, render: (_, row) => <Button type="link" icon={<EyeOutlined />} disabled={!row.employee_id} onClick={() => { setQualityDetailEmployeeId(row.employee_id!); setQualityProductFilter(undefined) }}>查看工作明细</Button> },
  ]
  const qualityProductColumns: TableColumnsType<AnalyticsQualityProductPerformance> = [
    { title: '产品 / 规格 / 材质', key: 'product', fixed: 'left', width: 250, render: (_, row) => <span><strong>{productLabel(row)}</strong><br /><Typography.Text type="secondary">{row.specification || '未关联规格'} · {row.material || '未关联材质'}</Typography.Text></span> },
    { title: '归属出货', dataIndex: 'attributed_shipped_quantity', width: 110, render: (value) => numberText(value) },
    { title: '参与出货', dataIndex: 'participated_shipped_quantity', width: 110, render: (value) => numberText(value) },
    { title: '责任退回', dataIndex: 'responsible_return_quantity', width: 110, render: (value) => numberText(value) },
    { title: '返工处理', dataIndex: 'reworked_quantity', width: 110, render: (value, row) => row.rework_record_count ? numberText(value) : '未登记' },
    { title: '原单 / 分摊明细', dataIndex: 'record_count', width: 100, render: (value, row) => `${numberText(row.source_document_count)} 张 / ${numberText(value)} 条` },
  ]
  const qualityWorkColumns: TableColumnsType<AnalyticsQualityWorkRecord> = [
    { title: '业务日期', dataIndex: 'business_date', fixed: 'left', width: 110 },
    { title: '操作', dataIndex: 'event_type_display', width: 120, render: (value, row) => <span><Tag color={row.event_type === 'SHIPMENT' ? 'blue' : row.event_type === 'REWORK' || row.event_type === 'MANUAL_REWORK' ? 'orange' : 'red'}>{value}</Tag>{row.return_round ? <Typography.Text type="secondary">第{row.return_round}轮</Typography.Text> : null}</span> },
    { title: '产品 / 规格 / 材质', key: 'product', width: 240, render: (_, row) => <span>{productLabel(row)}<br /><Typography.Text type="secondary">{row.specification || '未关联规格'} · {row.material || '未关联材质'}</Typography.Text></span> },
    { title: '订单 / 项次', key: 'order', width: 200, render: (_, row) => <span>{row.order_id ? <a href={orderLink(row.order_id)}>{row.order_no} / {row.item_no || '未记录'}</a> : row.order_no || '未关联订单'}</span> },
    { title: '流程卡 / 原始单据', key: 'source', width: 230, render: (_, row) => <span>{cardReferences(row)}<br /><a href={sourceLink(row)}>{row.source_no}</a></span> },
    { title: '重量 / 状态', key: 'status', width: 180, render: (_, row) => <span>{workWeightText(row)}<br />{WORK_STATUS[row.status || ''] || '状态待核对'}</span> },
    { title: '归属 / 参与件数', key: 'quantity', width: 150, render: (_, row) => <span><strong>{numberText(row.attributed_quantity)}</strong> / {numberText(row.participated_quantity)}{row.attribution_status === 'COLLABORATIVE_UNALLOCATED' ? <><br /><Tag color="warning">协作未分摊</Tag></> : null}</span> },
    { title: '质检 / 合格 / 不良', key: 'inspection', width: 170, render: (_, row) => workInspectionText(row) },
    { title: '退回 / 返工 / 合格 / 报废', key: 'rework', width: 230, render: (_, row) => workReworkText(row) },
  ]
  const qualityEmployeeSection = mobile ? (
    <div className="analytics-quality-mobile-list">
      {(data?.quality_employee_performance || []).map((row) => <Card key={row.employee_id || row.name} size="small" className="analytics-quality-mobile-card">
        <div className="record-card-heading"><div><Typography.Title level={4}>{row.name || '未填写'}</Typography.Title><Typography.Text type="secondary">{row.employee_no || row.team || '未记录员工编号'}</Typography.Text></div><SourceTag source={row.source} /></div>
        <div className="analytics-quality-mobile-grid">
          <span><small>质检 / 合格 / 不良</small><b>{row.inspection_record_count ? `${numberText(row.inspection_quantity)} / ${numberText(row.qualified_quantity)} / ${numberText(row.defective_quantity)}` : '未登记'}</b></span>
          <span><small>归属出货</small><b>{numberText(row.shipped_quantity)} 件</b></span>
          <span><small>参与出货</small><b>{numberText(row.participated_shipped_quantity)} 件</b></span>
          <span><small>责任退回</small><b className={row.responsible_return_quantity ? 'quality-danger-text' : ''}>{numberText(row.responsible_return_quantity)} 件</b></span>
          <span><small>返工 / 合格 / 报废</small><b>{row.rework_record_count ? `${numberText(row.reworked_quantity)} / ${numberText(row.recovered_quantity)} / ${numberText(row.scrap_quantity)}` : '返工未登记'}</b></span>
          <span><small>一次 / 返工通过率</small><b>{rateText(row.first_pass_rate)} / {rateText(row.rework_pass_rate)}</b></span>
        </div>
        {!!row.collaborative_shipped_quantity && <Typography.Text type="warning">协作未分摊 {numberText(row.collaborative_shipped_quantity)} 件</Typography.Text>}
        <Button block icon={<EyeOutlined />} disabled={!row.employee_id} onClick={() => { setQualityDetailEmployeeId(row.employee_id!); setQualityProductFilter(undefined) }}>查看产品和原始单据</Button>
      </Card>)}
      {!data?.quality_employee_performance?.length && <Empty description="所选期间暂无员工品检、出货或返工记录" />}
    </div>
  ) : <Table rowKey={(row) => row.employee_id || row.name} dataSource={data?.quality_employee_performance || []} columns={qualityEmployeeColumns} scroll={{ x: 1260 }} pagination={{ pageSize: 15 }} />
  const qualityProductSection = mobile ? (
    <div className="analytics-quality-mobile-list">
      {(qualityDetailQuery.data?.product_stats || []).map((row) => <Card key={productFilterKey(row)} size="small" className="analytics-quality-mobile-card">
        <div><strong>{productLabel(row)}</strong><br /><Typography.Text type="secondary">{row.specification || '未关联规格'} · {row.material || '未关联材质'}</Typography.Text></div>
        <div className="analytics-quality-mobile-grid">
          <span><small>归属 / 参与出货</small><b>{numberText(row.attributed_shipped_quantity)} / {numberText(row.participated_shipped_quantity)}</b></span>
          <span><small>责任退回 / 返工</small><b>{numberText(row.responsible_return_quantity)} / {row.rework_record_count ? numberText(row.reworked_quantity) : '未登记'}</b></span>
          <span><small>原单 / 分摊明细</small><b>{numberText(row.source_document_count)} 张 / {numberText(row.record_count)} 条</b></span>
        </div>
      </Card>)}
      {qualityDetailQuery.isFetching && <Spin description="正在读取员工产品记录…" />}
      {!qualityDetailQuery.isFetching && !qualityDetailQuery.isError && !qualityDetailQuery.data?.product_stats.length && <Empty description="所选期间没有该员工的产品记录" />}
    </div>
  ) : <Table rowKey={productFilterKey} dataSource={qualityDetailQuery.data?.product_stats || []} columns={qualityProductColumns} loading={qualityDetailQuery.isLoading} pagination={{ pageSize: 10 }} scroll={{ x: 790 }} locale={{ emptyText: qualityDetailQuery.isFetching ? '正在读取员工产品记录…' : qualityDetailQuery.isError ? '读取失败，请重试' : '当前区间无匹配产品记录' }} />
  const qualityWorkSection = mobile ? (
    <div className="analytics-quality-mobile-list">
      {workRecords.map((row) => <Card key={row.record_key} size="small" className="analytics-quality-mobile-card">
        <div className="record-card-heading"><div><strong>{row.business_date}</strong><br /><Typography.Text type="secondary">{productLabel(row)} · {row.specification || '未关联规格'} · {row.material || '未关联材质'}</Typography.Text></div><Tag color={row.event_type === 'SHIPMENT' ? 'blue' : row.event_type === 'REWORK' || row.event_type === 'MANUAL_REWORK' ? 'orange' : 'red'}>{row.event_type_display}</Tag></div>
        <Typography.Text>{row.order_no || '未关联订单'} · 项次 {row.item_no || '未记录'}</Typography.Text>
        <Typography.Text type="secondary">{cardReferences(row)} · {row.source_no}</Typography.Text>
        <div className="analytics-quality-mobile-grid">
          <span><small>归属 / 参与件数</small><b>{numberText(row.attributed_quantity)} / {numberText(row.participated_quantity)}</b></span>
          <span><small>质检 / 合格 / 不良</small><b>{workInspectionText(row)}</b></span>
          <span><small>退回 / 返工 / 合格 / 报废</small><b>{workReworkText(row)}</b></span>
          <span><small>重量 / 状态</small><b>{workWeightText(row)} · {WORK_STATUS[row.status || ''] || '状态待核对'}</b></span>
        </div>
        {row.return_round ? <Typography.Text type="secondary">第 {row.return_round} 轮</Typography.Text> : null}
        {row.attribution_status === 'COLLABORATIVE_UNALLOCATED' ? <Tag color="warning">协作未分摊</Tag> : null}
        <Space wrap><Button href={sourceLink(row)}>查看原单</Button>{row.order_id && <Button href={orderLink(row.order_id)}>查看订单项次</Button>}</Space>
      </Card>)}
      {qualityDetailQuery.isFetching ? <Typography.Text>正在读取原始操作记录…</Typography.Text> : !qualityDetailQuery.isError && !workRecords.length && <Empty description="所选条件没有原始操作记录" />}
    </div>
  ) : <Table rowKey="record_key" dataSource={workRecords} columns={qualityWorkColumns} loading={qualityDetailQuery.isLoading} pagination={{ pageSize: 15, showSizeChanger: true }} scroll={{ x: 1555 }} locale={{ emptyText: qualityDetailQuery.isFetching ? '正在读取原始操作记录…' : qualityDetailQuery.isError ? '读取失败，请重试' : '所选条件没有原始操作记录' }} />
  const orderColumns: TableColumnsType<AnalyticsOrderPerformance> = [
    { title: '订单号 / 项次', key: 'order', fixed: 'left', width: 190, render: (_, row) => <span>{row.order_id ? <a href={orderLink(row.order_id)}>{row.order_no} / {row.item_no || '未记录'}</a> : row.order_no}</span> },
    { title: '产品 / 规格', key: 'product', width: 190, render: (_, row) => <span>{row.product_name || '-'}<br /><Typography.Text type="secondary">{row.specification || '-'} · {row.material || '-'}</Typography.Text></span> },
    { title: '关联方式', dataIndex: 'link_type', width: 120, render: (value) => <Tag color={value === 'ORDER' ? 'blue' : 'default'}>{value === 'ORDER' ? '订单主档关联' : '历史订单号匹配'}</Tag> },
    { title: '来源', dataIndex: 'source', width: 125, render: (value) => <SourceTag source={value} /> },
    { title: '生产模数', dataIndex: 'produced_mold_count', width: 110, render: (value) => numberText(value) },
    { title: '质检 / 出货', key: 'quality', width: 160, render: (_, row) => `${row.inspection_record_count ? numberText(row.inspection_quantity) : '未登记'} / ${numberText(row.shipped_quantity)}` },
    { title: '全生命周期净交付 / 剩余', key: 'delivery', width: 175, render: (_, row) => `${numberText(row.net_delivered_quantity)} / ${numberText(row.remaining_quantity)}` },
    { title: '退回 / 报废', key: 'returns', width: 130, render: (_, row) => `${numberText(row.returned_quantity)} / ${numberText(row.scrap_quantity)}` },
    { title: '一次合格率', dataIndex: 'first_pass_rate', width: 115, render: rateText },
    { title: '收入 / 利润', key: 'finance', width: 170, render: (_, row) => row.finance_record_count ? <span>{moneyText(row.revenue)}<br />{moneyText(row.profit)}</span> : '未录入 / 未核算' },
  ]
  const manualColumns: TableColumnsType<ManualPerformanceEntry> = [
    { title: '日期', dataIndex: 'entry_date', width: 110 },
    { title: '类型', dataIndex: 'entry_type', width: 115, render: (value) => <Tag color={ENTRY_META[value as keyof typeof ENTRY_META].color}>{ENTRY_META[value as keyof typeof ENTRY_META].label}</Tag> },
    { title: '人员 / 订单', key: 'staff', width: 180, render: (_, row) => <span><strong>{row.staff_name}</strong><br /><Typography.Text type="secondary">{row.order_no || '未关联订单'}</Typography.Text></span> },
    { title: '补录内容', key: 'summary', width: 260, render: (_, row) => performanceSummary(row) },
    { title: '状态', dataIndex: 'voided_at', width: 95, render: (value) => <Tag color={value ? 'default' : 'orange'}>{value ? '已作废' : '手工补录'}</Tag> },
    { title: '操作', key: 'action', fixed: 'right', width: 135, render: (_, row) => row.voided_at ? <Popconfirm title="确认恢复这条绩效补录？" onConfirm={() => restorePerformance.mutate(row)}><Button type="link">恢复</Button></Popconfirm> : <Space size={2}><Button type="link" icon={<EditOutlined />} onClick={() => setPerformanceForm({ entry: row })}>编辑</Button><Popconfirm title="确认作废这条绩效补录？" description="作废后不再计入分析，但历史会保留。" onConfirm={() => voidPerformance.mutate(row)}><Button type="link" danger>作废</Button></Popconfirm></Space> },
  ]
  const financialColumns: TableColumnsType<ManualFinancialEntry> = [
    { title: '日期', dataIndex: 'occurred_on', width: 110 },
    { title: '方向', dataIndex: 'direction', width: 90, render: (value) => <Tag color={value === 'INCOME' ? 'green' : 'red'}>{value === 'INCOME' ? '收入' : '支出'}</Tag> },
    { title: '分类', dataIndex: 'category', width: 100, render: (value) => FINANCE_CATEGORY[value] || value },
    { title: '金额', dataIndex: 'amount', width: 130, render: (value, row) => <strong className={row.direction === 'EXPENSE' ? 'negative-value' : 'profit-value'}>{moneyText(value)}</strong> },
    { title: '说明 / 订单', key: 'description', width: 230, render: (_, row) => <span>{row.description || '-'}<br /><Typography.Text type="secondary">{row.order_no || '未关联订单'}</Typography.Text></span> },
    { title: '状态', dataIndex: 'voided_at', width: 95, render: (value) => <Tag color={value ? 'default' : 'orange'}>{value ? '已作废' : '手工补录'}</Tag> },
    { title: '操作', key: 'action', fixed: 'right', width: 135, render: (_, row) => row.voided_at ? <Popconfirm title="确认恢复这条收支记录？" onConfirm={() => restoreFinance.mutate(row)}><Button type="link">恢复</Button></Popconfirm> : <Space size={2}><Button type="link" icon={<EditOutlined />} onClick={() => setFinancialForm({ entry: row })}>编辑</Button><Popconfirm title="确认作废这条收支记录？" description="作废后不再计入利润，但历史会保留。" onConfirm={() => voidFinance.mutate(row)}><Button type="link" danger>作废</Button></Popconfirm></Space> },
  ]

  const manualSection = mobile ? (
    <div className="analytics-manual-mobile">
      {(manualQuery.data || []).map((entry) => <Card key={`p-${entry.id}`} className={entry.voided_at ? 'voided' : ''}><div className="record-card-heading"><Typography.Title level={4}>{entry.staff_name}</Typography.Title><Tag color={ENTRY_META[entry.entry_type].color}>{ENTRY_META[entry.entry_type].label}</Tag></div><Typography.Text>{performanceSummary(entry)}</Typography.Text><Typography.Text type="secondary">{entry.entry_date} · {entry.order_no || '未关联订单'} · {entry.voided_at ? '已作废' : '手工补录'}</Typography.Text>{entry.voided_at ? <Popconfirm title="确认恢复？" onConfirm={() => restorePerformance.mutate(entry)}><Button>恢复</Button></Popconfirm> : <Space><Button onClick={() => setPerformanceForm({ entry })}>编辑</Button><Popconfirm title="确认作废？" onConfirm={() => voidPerformance.mutate(entry)}><Button danger>作废</Button></Popconfirm></Space>}</Card>)}
      {(financialQuery.data || []).map((entry) => <Card key={`f-${entry.id}`} className={entry.voided_at ? 'voided' : ''}><div className="record-card-heading"><Typography.Title level={4}>{entry.description || FINANCE_CATEGORY[entry.category]}</Typography.Title><Tag color={entry.direction === 'INCOME' ? 'green' : 'red'}>{entry.direction === 'INCOME' ? '收入' : '支出'}</Tag></div><strong className={entry.direction === 'EXPENSE' ? 'negative-value' : 'profit-value'}>{moneyText(entry.amount)}</strong><Typography.Text type="secondary">{entry.occurred_on} · {entry.order_no || '未关联订单'} · {entry.voided_at ? '已作废' : '手工补录'}</Typography.Text>{entry.voided_at ? <Popconfirm title="确认恢复？" onConfirm={() => restoreFinance.mutate(entry)}><Button>恢复</Button></Popconfirm> : <Space><Button onClick={() => setFinancialForm({ entry })}>编辑</Button><Popconfirm title="确认作废？" onConfirm={() => voidFinance.mutate(entry)}><Button danger>作废</Button></Popconfirm></Space>}</Card>)}
      {!manualQuery.data?.length && !financialQuery.data?.length && <Empty description="本月暂无手工补录" />}
    </div>
  ) : (
    <Space direction="vertical" size={18} style={{ width: '100%' }}>
      <Card title="绩效补录明细" styles={{ body: { padding: 0 } }}><Table rowKey="id" dataSource={manualQuery.data || []} columns={manualColumns} loading={manualQuery.isLoading} scroll={{ x: 1000 }} pagination={{ pageSize: 10 }} /></Card>
      <Card title="手工收支明细" styles={{ body: { padding: 0 } }}><Table rowKey="id" dataSource={financialQuery.data || []} columns={financialColumns} loading={financialQuery.isLoading} scroll={{ x: 1000 }} pagination={{ pageSize: 10 }} /></Card>
    </Space>
  )

  return (
    <div className="page-container analytics-page">
      <PageTitle
        title="数据分析与绩效"
        description="统一分析生产、品检、出货、返工和收支；系统自动、手工补录与合计口径始终分开显示。"
        extra={<Space wrap><Button icon={<FormOutlined />} onClick={() => setPerformanceForm({})}>补录绩效</Button><Button type="primary" icon={<DollarOutlined />} onClick={() => setFinancialForm({})}>记录收支</Button></Space>}
      />

      <Card className="filter-card analytics-filter-card">
        <div className="analytics-filter-row">
          <DatePicker picker="month" allowClear={false} value={month} format="YYYY年M月" onChange={(value) => { if (value) { setMonth(value); setCustomRange(undefined) } }} />
          <DatePicker.RangePicker value={customRange} presets={[{ label: '今天', value: [dayjs(), dayjs()] }, { label: '近7天', value: [dayjs().subtract(6, 'day'), dayjs()] }, { label: '本月', value: [dayjs().startOf('month'), dayjs()] }]} onChange={(value) => setCustomRange(value?.[0] && value?.[1] ? [value[0], value[1]] : undefined)} placeholder={['自定义开始日期', '自定义结束日期']} />
          <Select value={group} onChange={(value) => { setGroup(value); setMachineId(undefined) }} options={[{ value: '', label: '全部机台分组' }, ...groups.map((value) => ({ value, label: `${value}组` }))]} />
          <Select allowClear value={machineId} placeholder="全部机台" onChange={setMachineId} options={visibleMachines.map((item) => ({ value: item.id, label: `${item.code} · ${item.name}` }))} />
          <Typography.Text type="secondary">每60秒自动刷新 · {data?.period.date_from || dateFrom} 至 {data?.period.date_to || dateTo}</Typography.Text>
        </div>
      </Card>

      {dashboardQuery.isError && <Alert className="analytics-alert" type="error" showIcon title="综合分析读取失败" description={(dashboardQuery.error as Error).message} />}
      {(manualQuery.isError || financialQuery.isError) && <Alert className="analytics-alert" type="warning" showIcon title="部分手工补录明细读取失败" description={((manualQuery.error || financialQuery.error) as Error).message} />}
      {(group || machineId) && <Alert className="analytics-alert" type="info" showIcon title="机台筛选口径" description="分组和机台筛选只作用于生产及关联收支；品检、出货和返工没有机台字段，仍显示同一日期范围的全厂数据。" />}
      <Alert className="analytics-alert" type="info" showIcon title="数据来源与缺项" description="系统自动指手工业务台账的自动汇总，不是设备传感器采集。生产件数按登记模数×当时孔数折算，不是实测良品；折算工时按模数×硫化时间计算。上下机间隔未扣休息，不等于人员实际工时。无事件显示未登记，无金额依据显示未录入；明确登记的零值保留0。不适用的字段不计入该类事件。" />

      <Row gutter={[14, 14]} className="analytics-finance-kpis">
        <Col xs={12} lg={6}><Card className="analytics-kpi income"><Statistic title="合计收入" value={hasFinanceRecords ? numberValue(financeTotal?.revenue) : '未录入'} precision={2} prefix={hasFinanceRecords ? '¥' : undefined} /></Card></Col>
        <Col xs={12} lg={6}><Card className="analytics-kpi cost"><Statistic title="合计成本" value={hasFinanceRecords ? numberValue(financeTotal?.total_cost) : '未录入'} precision={2} prefix={hasFinanceRecords ? '¥' : undefined} /></Card></Col>
        <Col xs={12} lg={6}><Card className={`analytics-kpi profit ${numberValue(financeTotal?.profit) < 0 ? 'loss' : ''}`}><Statistic title="合计利润" value={hasFinanceRecords ? numberValue(financeTotal?.profit) : '暂无结算记录'} precision={2} prefix={hasFinanceRecords ? '¥' : undefined} /></Card></Col>
        <Col xs={12} lg={6}><Card className="analytics-kpi margin"><Statistic title="利润率" value={profitMargin === null || profitMargin === undefined ? '—' : numberValue(profitMargin)} suffix={profitMargin === null || profitMargin === undefined ? undefined : '%'} precision={2} /></Card></Col>
      </Row>
      {!!data?.production.unsettled_completed_run_count && (
        <Alert
          className="analytics-alert analytics-unsettled-warning"
          type="warning"
          showIcon
          title={`有 ${data.production.unsettled_completed_run_count} 个已完成订单尚未结算`}
          description="这些订单尚未计入系统自动收入、成本和利润；完成结算后，本页数据会自动更新。"
        />
      )}
      <Row gutter={[14, 14]} className="analytics-operation-kpis">
        <Col xs={12} lg={8} xl={4}><Card><Statistic title="生产模数" value={production?.produced_mold_count || 0} suffix="模" prefix={<BarChartOutlined />} /></Card></Col>
        <Col xs={12} lg={8} xl={4}><Card><Statistic title="自动机台效率" value={data?.production.automatic.efficiency_percent === null || data?.production.automatic.efficiency_percent === undefined ? '—' : numberValue(data.production.automatic.efficiency_percent)} suffix={data?.production.automatic.efficiency_percent === null || data?.production.automatic.efficiency_percent === undefined ? undefined : '%'} precision={2} prefix={<ClockCircleOutlined />} /></Card></Col>
        <Col xs={12} lg={8} xl={4}><Card><Statistic title="质检数量" value={quality?.inspection_record_count ? quality.inspection_quantity : '未登记'} suffix={quality?.inspection_record_count ? '件' : undefined} prefix={<AuditOutlined />} /></Card></Col>
        <Col xs={12} lg={8} xl={4}><Card><Statistic title="出货数量" value={quality?.shipped_quantity || 0} suffix="件" prefix={<SendOutlined />} /></Card></Col>
        <Col xs={12} lg={8} xl={4}><Card><Statistic title="一次合格率" value={quality?.first_pass_rate === null || quality?.first_pass_rate === undefined ? '—' : numberValue(quality.first_pass_rate)} suffix={quality?.first_pass_rate === null || quality?.first_pass_rate === undefined ? undefined : '%'} precision={2} prefix={<TeamOutlined />} /></Card></Col>
        <Col xs={12} lg={8} xl={4}><Card><Statistic title="当期退回 / 出货比" value={quality?.return_rate === null || quality?.return_rate === undefined ? '—' : numberValue(quality.return_rate)} suffix={quality?.return_rate === null || quality?.return_rate === undefined ? undefined : '%'} precision={2} /></Card></Col>
      </Row>
      {!!qualityAttribution && (qualityAttribution.unassigned_employee_quantity > 0 || qualityAttribution.collaborative_unallocated_quantity > 0 || qualityAttribution.other_unattributed_quantity > 0) && <Alert
        className="analytics-alert"
        type="warning"
        showIcon
        title="全厂出货与员工归属差额已拆分"
        description={`全厂 ${numberText(qualityAttribution.factory_shipped_quantity)} 件 = 员工已归属 ${numberText(qualityAttribution.employee_attributed_quantity)} 件 + 未填写人员 ${numberText(qualityAttribution.unassigned_employee_quantity)} 件 + 协作未分摊 ${numberText(qualityAttribution.collaborative_unallocated_quantity)} 件${qualityAttribution.other_unattributed_quantity ? ` + 其他待核对 ${numberText(qualityAttribution.other_unattributed_quantity)} 件` : ''}。协作记录没有明确比例时不平均分配。`}
      />}

      <div className="analytics-source-strip">
        {(['production', 'quality', 'rework', 'finance'] as const).map((key) => {
          const counts = data?.sources[key]
          const label = { production: '生产记录', quality: '品检出货', rework: '退回返工', finance: '财务记录' }[key]
          return <Card key={key}><strong>{label}</strong><span><Tag color="blue">自动 {counts?.automatic || 0}</Tag><Tag color="orange">手工 {counts?.manual || 0}</Tag><Tag color="purple">合计 {counts?.total || 0}</Tag></span></Card>
        })}
      </div>

      <Row gutter={[16, 16]} className="analytics-overview-row">
        <Col xs={24} xl={16}><Card className="analytics-chart-card" title="生产 · 品检 · 出货每日联动趋势"><DailyLinkageChart rows={data?.daily_trend || []} /></Card></Col>
        <Col xs={24} xl={8}><Card className="analytics-chart-card" title="利润来源透明度">{finance && numberValue(data?.sources.finance?.total) > 0 ? <FinanceSourceComparison automatic={{ revenue: numberValue(finance.automatic.revenue), cost: numberValue(finance.automatic.total_cost), profit: numberValue(finance.automatic.profit) }} manual={{ revenue: numberValue(finance.manual.revenue), cost: numberValue(finance.manual.total_cost), profit: numberValue(finance.manual.profit) }} total={{ revenue: numberValue(financeTotal?.revenue), cost: numberValue(financeTotal?.total_cost), profit: numberValue(financeTotal?.profit) }} /> : <Empty description="暂无已结算或手工收支数据" />}<Alert className="analytics-basis-alert" type="info" showIcon title="统计口径" description="系统自动利润按结算时间；手工收支按发生日期。折算工时与手工填报工时分开，不视为同一实际工时。" /></Card></Col>
      </Row>
      <Card className="analytics-chart-card analytics-profit-trend-card" title="每日收入 · 成本 · 利润趋势"><DailyProfitChart rows={data?.daily_trend || []} /></Card>

      <Card className="analytics-tabs-card data-card">
        <Tabs activeKey={activeTab} onChange={setActiveTab} items={[
          { key: 'production', label: '生产绩效', children: <div className="analytics-tab-content"><Row gutter={[16, 16]}><Col xs={24} xl={10}><Card title="人员模数排行"><RankBarChart items={(data?.operator_performance || []).map((row) => ({ key: row.operator, label: row.operator, value: row.total_mold_count, detail: `${row.production_days}天 · 日均${numberText(row.average_daily_mold_count, 2)}`, source: row.source }))} valueSuffix=" 模" /></Card></Col><Col xs={24} xl={14}><Card title="机台效率与利润" styles={{ body: { padding: 0 } }}><Table rowKey={(row) => row.machine_id || row.station_id || row.machine_code} dataSource={data?.station_performance || []} columns={stationColumns} scroll={{ x: 1130 }} pagination={{ pageSize: 10 }} /></Card></Col></Row><Card className="analytics-detail-table" title="人员绩效明细" styles={{ body: { padding: 0 } }}><Table rowKey="operator" dataSource={data?.operator_performance || []} columns={operatorColumns} scroll={{ x: 1080 }} pagination={{ pageSize: 15 }} /></Card></div> },
          { key: 'quality', label: '品检与返工绩效', children: <div className="analytics-tab-content"><Row gutter={[16, 16]}><Col xs={24} xl={12}><Card title="归属出货＋已登记返工作业件数排行"><RankBarChart items={(data?.quality_employee_performance || []).filter((row) => row.shipped_quantity + row.reworked_quantity > 0).map((row) => ({ key: row.employee_id || row.name, label: row.name, value: row.shipped_quantity + row.reworked_quantity, detail: `归属出货${numberText(row.shipped_quantity)} · ${reworkMetricText(row)}`, source: row.source }))} valueSuffix=" 件" emptyText="所选期间暂无已归属员工的出货或返工数据" /></Card></Col><Col xs={24} xl={12}><Card title="退回原因排行与占比"><RankBarChart items={(data?.defect_reason_breakdown || []).map((row) => ({ key: row.reason_category, label: row.reason_category_display || row.reason_category, value: row.returned_quantity, detail: `占退回 ${rateText(row.share_of_returns)} · 返工通过 ${rateText(row.rework_pass_rate)}`, source: row.source }))} valueSuffix=" 件" emptyText="所选期间暂无退回原因数据" /></Card></Col></Row><Card className="analytics-detail-table" title="员工绩效明细" styles={{ body: { padding: 0 } }}>{qualityEmployeeSection}</Card></div> },
          { key: 'orders', label: '订单联动', children: <div className="analytics-tab-content"><Alert className="analytics-alert" type="info" showIcon title="优先按订单主档关联" description="新记录通过订单 ID 精确联动；旧历史没有订单关联时继续按订单号匹配，并在表中标明关联方式。" /><Card styles={{ body: { padding: 0 } }}><Table rowKey="row_key" dataSource={data?.order_performance || []} columns={orderColumns} scroll={{ x: 1370 }} pagination={{ pageSize: 15 }} /></Card></div> },
          { key: 'manual', label: '手工补录', children: <div className="analytics-tab-content"><div className="section-heading"><div><Typography.Title level={3}>补录与作废记录</Typography.Title><Typography.Text type="secondary">补录可编辑；删除采用作废方式保留审计历史。</Typography.Text></div><Space wrap><Button icon={<FormOutlined />} onClick={() => setPerformanceForm({})}>补录绩效</Button><Button type="primary" icon={<DollarOutlined />} onClick={() => setFinancialForm({})}>记录收支</Button></Space></div>{manualSection}</div> },
        ]} />
      </Card>

      <ManualPerformanceDrawer open={!!performanceForm} entry={performanceForm?.entry} machines={machines} employees={employees} onClose={() => setPerformanceForm(undefined)} />
      <ManualFinancialDrawer open={!!financialForm} entry={financialForm?.entry} machines={machines} onClose={() => setFinancialForm(undefined)} />
      <Drawer
        open={!!qualityDetailEmployee}
        onClose={() => setQualityDetailEmployeeId(undefined)}
        width="min(1180px, 100vw)"
        title={qualityDetailEmployee ? `${qualityDetailEmployee.name} · 产品与原始单据明细` : '员工工作明细'}
      >
        <Alert
          type="info"
          showIcon
          title={`统计范围：${dateFrom} 至 ${dateTo}`}
          description="归属出货可以与全厂合计相加；参与出货用于说明员工参与过该批次，协作但没有明确比例时不会平均分配。重量出货没有独立首次检验记录时显示“未登记”。"
          style={{ marginBottom: 16 }}
        />
        {qualityDetailQuery.isError && <Alert type="error" showIcon title="员工工作明细读取失败" description={(qualityDetailQuery.error as Error).message} style={{ marginBottom: 16 }} />}
        {qualityDetailQuery.isError && <Button onClick={() => void qualityDetailQuery.refetch()}>重试读取员工明细</Button>}
        <Card title="按产品规格汇总" styles={{ body: { padding: 0 } }}>
          {qualityProductSection}
        </Card>
        <Card title="原始操作单据" styles={{ body: { padding: 0 } }} style={{ marginTop: 16 }}>
          <Select allowClear value={qualityProductFilter} onChange={setQualityProductFilter} placeholder="全部产品规格 / 材质" style={{ width: '100%', marginBottom: 12 }} options={(qualityDetailQuery.data?.product_stats || []).map((row) => ({ value: productFilterKey(row), label: `${productLabel(row)} · ${row.specification || '未关联规格'} · ${row.material || '未关联材质'}` }))} />
          {qualityWorkSection}
        </Card>
      </Drawer>
    </div>
  )
}
