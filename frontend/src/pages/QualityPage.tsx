import {
  AuditOutlined,
  CheckCircleOutlined,
  EditOutlined,
  EyeOutlined,
  PlusOutlined,
  QrcodeOutlined,
  ReloadOutlined,
  SearchOutlined,
  SendOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import { Alert, App, Button, Card, Col, DatePicker, Empty, Grid, Input, Progress, Row, Select, Skeleton, Space, Statistic, Table, Tabs, Tag, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import dayjs, { type Dayjs } from 'dayjs'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { orderApi, qualityApi, qualityWorkflowApi, toList } from '../api/client'
import {
  QualityEmployeeDrawer,
  QualityShipmentDrawer,
} from '../components/QualityFormDrawers'
import { PageTitle } from '../components/PageTitle'
import { QualityShippingWorkflow } from '../components/QualityShippingWorkflow'
import { QualityWorkflowManagement, ShipmentBatchReviewDrawer } from '../components/QualityWorkflowManagement'
import {
  QualityReturnReworkAttemptDrawer,
  QualityReturnReworkDrawer,
  QualityReworkCaseDetailDrawer,
} from '../components/QualityReturnReworkDrawer'
import { QualityReworkCaseMobileList } from '../components/QualityReworkCaseMobileList'
import { QualityFlowCardReturnDrawer, QualityProcessCardReplacementDrawer } from '../components/QualityFlowCardReturnDrawer'
import { formatQualityDate, isHighReworkCount, qualityNumber, reworkCaseSourceTitle } from '../quality'
import type {
  QualityDailyTrend,
  QualityEmployee,
  QualityEmployeeRole,
  QualityOrder,
  QualityOrderStatistics,
  QualityReworkAttempt,
  QualityReworkCase,
  QualityShipment,
  QualityShipmentBatch,
  QualityShipmentLedgerRow,
  ReturnRework,
  ReturnReworkStatus,
} from '../types'

const { RangePicker } = DatePicker

const ORDER_STATUS_META = {
  OPEN: { text: '进行中', color: 'processing' },
  COMPLETED: { text: '已完成', color: 'success' },
  CANCELLED: { text: '已取消', color: 'default' },
} as const

const REWORK_STATUS_META: Record<ReturnReworkStatus, { text: string; color: string }> = {
  PENDING: { text: '待处理', color: 'warning' },
  PROCESSING: { text: '处理中', color: 'processing' },
  COMPLETED: { text: '已完成', color: 'success' },
}

const REASON_META: Record<string, string> = {
  APPEARANCE: '外观',
  STICKING: '粘皮',
  DIMENSION: '尺寸',
  MATERIAL: '材质',
  MIXED: '混料 / 混装',
  PACKAGING: '包装',
  OTHER: '其他',
}

const ROLE_META: Record<QualityEmployeeRole, { text: string; color: string }> = {
  INSPECTOR: { text: '品检员', color: 'blue' },
  REWORKER: { text: '返工员', color: 'orange' },
  BOTH: { text: '品检兼返工', color: 'purple' },
}

function rateText(value: number | string | null | undefined) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? `${parsed.toFixed(2)}%` : '-'
}

function reworkCountTag(value: number) {
  return isHighReworkCount(value)
    ? <Tag color="error" icon={<WarningOutlined />}>{value} 次</Tag>
    : <Tag>{value || 0} 次</Tag>
}

function DailyTrend({ rows, loading }: { rows: QualityDailyTrend[]; loading: boolean }) {
  const activityRows = rows.filter((item) => [
    item.inspection_quantity,
    item.shipped_quantity,
    item.returned_quantity,
    item.reworked_quantity,
  ].some((value) => Number(value || 0) > 0))
  const maxInspection = Math.max(1, ...activityRows.map((item) => Number(item.inspection_quantity || 0)))
  return (
    <Card className="quality-trend-card" title={<span><AuditOutlined /> 每日质检与出货趋势</span>}>
      {loading ? <Skeleton active paragraph={{ rows: 5 }} /> : activityRows.length ? (
        <div className="quality-trend-list">
          {activityRows.map((item) => (
            <div className="quality-trend-row" key={item.date}>
              <strong>{formatQualityDate(item.date, 'MM-DD')}</strong>
              <div className="quality-trend-main">
                <Progress percent={Math.round((Number(item.inspection_quantity || 0) / maxInspection) * 100)} showInfo={false} strokeColor="#2f6f9f" />
                <div className="quality-trend-values">
                  <span>质检 {qualityNumber(item.inspection_quantity)}</span>
                  <span>出货 {qualityNumber(item.shipped_quantity)}</span>
                  <span className={Number(item.returned_quantity) > 0 ? 'quality-danger-text' : ''}>退货 {qualityNumber(item.returned_quantity)}</span>
                  <span>返工 {qualityNumber(item.reworked_quantity)}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : <Empty description="所选日期暂无质检、出货或返工记录" />}
    </Card>
  )
}

type OrderRow = { order: QualityOrder; stats?: QualityOrderStatistics }

export function QualityPage() {
  const navigate = useNavigate()
  const screens = Grid.useBreakpoint()
  const mobile = screens.md === false
  const queryClient = useQueryClient()
  const { message } = App.useApp()
  const [range, setRange] = useState<[Dayjs, Dayjs]>([dayjs().startOf('month'), dayjs().endOf('month')])
  const [dueRange, setDueRange] = useState<[Dayjs, Dayjs] | null>(null)
  const [query, setQuery] = useState('')
  const [shipmentStatus, setShipmentStatus] = useState('CONFIRMED')
  const [orderStatus, setOrderStatus] = useState('')
  const [deliveryStatus, setDeliveryStatus] = useState('')
  const [inspectorFilter, setInspectorFilter] = useState<number>()
  const [ordering, setOrdering] = useState('-shipment_date')
  const [activeTab, setActiveTab] = useState('workflow')
  const [shipmentForm, setShipmentForm] = useState<{ shipment?: QualityShipment }>()
  const [shipmentSessionKey, setShipmentSessionKey] = useState(0)
  const [batchReviewItem, setBatchReviewItem] = useState<QualityShipmentBatch>()
  const [returnReworkOpen, setReturnReworkOpen] = useState(false)
  const [flowCardReturnOpen, setFlowCardReturnOpen] = useState(false)
  const [flowCardReturnSessionKey, setFlowCardReturnSessionKey] = useState(0)
  const [replacementOpen, setReplacementOpen] = useState(false)
  const [returnReworkDetail, setReturnReworkDetail] = useState<QualityReworkCase>()
  const [returnReworkAttempt, setReturnReworkAttempt] = useState<QualityReworkCase>()
  const [resumeReturnAfterShipment, setResumeReturnAfterShipment] = useState(false)
  const [employeeForm, setEmployeeForm] = useState<{ employee?: QualityEmployee }>()
  const [reworkQuickFilter, setReworkQuickFilter] = useState('ALL')
  const [reworkMoreOpen, setReworkMoreOpen] = useState(false)
  const [reworkRange, setReworkRange] = useState<[Dayjs, Dayjs] | null>(null)
  const [reworkReasonFilter, setReworkReasonFilter] = useState('')
  const [reworkCustomerFilter, setReworkCustomerFilter] = useState('')
  const dateFrom = range[0].format('YYYY-MM-DD')
  const dateTo = range[1].format('YYYY-MM-DD')
  const dueDateFrom = dueRange?.[0].format('YYYY-MM-DD')
  const dueDateTo = dueRange?.[1].format('YYYY-MM-DD')

  const openShipmentForm = (shipment?: QualityShipment) => {
    setShipmentSessionKey((value) => value + 1)
    setShipmentForm({ shipment })
  }

  const openFlowCardReturn = () => {
    setFlowCardReturnSessionKey((value) => value + 1)
    setFlowCardReturnOpen(true)
  }

  const summaryQuery = useQuery({
    queryKey: ['quality', 'summary', dateFrom, dateTo],
    queryFn: () => qualityApi.summary({ date_from: dateFrom, date_to: dateTo }),
  })
  const employeesQuery = useQuery({
    queryKey: ['quality', 'employees'],
    queryFn: async () => toList(await qualityApi.listEmployees({ page_size: 1000 })),
  })
  const ordersQuery = useQuery({
    queryKey: ['orders', 'quality-options'],
    queryFn: async () => toList(await orderApi.list({ page_size: 1000 })),
  })
  const shipmentLedgerQuery = useQuery({
    queryKey: ['quality', 'shipment-ledger', { dateFrom, dateTo, dueDateFrom, dueDateTo, query, shipmentStatus, orderStatus, deliveryStatus, inspectorFilter, ordering }],
    queryFn: async () => toList(await qualityApi.listShipmentLedger({
      q: query,
      shipment_status: shipmentStatus,
      order_status: orderStatus || undefined,
      delivery_status: deliveryStatus || undefined,
      inspector: inspectorFilter,
      date_from: dateFrom,
      date_to: dateTo,
      due_date_from: dueDateFrom,
      due_date_to: dueDateTo,
      ordering,
      page_size: 1000,
    })),
  })
  const shipmentOptionsQuery = useQuery({
    queryKey: ['quality', 'shipments', 'options'],
    queryFn: async () => toList(await qualityApi.listShipments({ page_size: 1000 })),
  })
  const reworksQuery = useQuery({
    queryKey: ['quality', 'reworks', { dateFrom, dateTo, query }],
    queryFn: async () => toList(await qualityApi.listReworks({ q: query, date_from: dateFrom, date_to: dateTo, page_size: 1000 })),
  })
  const processCardsQuery = useQuery({
    queryKey: ['quality', 'process-cards', query],
    queryFn: async () => toList(await qualityWorkflowApi.listProcessCards({ q: query, page_size: 1000 })),
    retry: false,
  })
  const unitWeightsQuery = useQuery({
    queryKey: ['quality', 'unit-weights', query],
    queryFn: async () => toList(await qualityWorkflowApi.listUnitWeights({ q: query, page_size: 1000 })),
    retry: false,
  })
  const batchesQuery = useQuery({
    queryKey: ['quality', 'shipment-batches', { dateFrom, dateTo, dueDateFrom, dueDateTo, query, shipmentStatus, orderStatus, deliveryStatus, inspectorFilter, ordering }],
    queryFn: async () => toList(await qualityWorkflowApi.listShipmentBatches({
      q: query,
      status: shipmentStatus,
      order_status: orderStatus || undefined,
      delivery_status: deliveryStatus || undefined,
      inspector: inspectorFilter,
      date_from: dateFrom,
      date_to: dateTo,
      due_date_from: dueDateFrom,
      due_date_to: dueDateTo,
      ordering,
      page_size: 1000,
    })),
    retry: false,
  })
  const workflowBatchesQuery = useQuery({
    queryKey: ['quality', 'shipment-batches', 'workflow-all'],
    queryFn: async () => toList(await qualityWorkflowApi.listShipmentBatches({ ordering: '-shipment_date', page_size: 1000 })),
    retry: false,
  })
  const shipmentBatchOptionsQuery = useQuery({
    queryKey: ['quality', 'shipment-batches', 'confirmed-options'],
    queryFn: async () => toList(await qualityWorkflowApi.listShipmentBatches({ status: 'CONFIRMED', page_size: 1000 })),
    retry: false,
  })
  const reworkCasesQuery = useQuery({
    queryKey: ['quality', 'rework-cases'],
    queryFn: async () => toList(await qualityWorkflowApi.listReworkCases({ page_size: 1000 })),
    retry: false,
  })

  const employees = useMemo(() => employeesQuery.data || [], [employeesQuery.data])
  const orders = useMemo(() => ordersQuery.data || [], [ordersQuery.data])
  const shipmentLedger = shipmentLedgerQuery.data || []
  const shipmentOptions = shipmentOptionsQuery.data || []
  const reworks = reworksQuery.data || []
  const processCards = processCardsQuery.data || []
  const unitWeights = unitWeightsQuery.data || []
  const shipmentBatches = batchesQuery.data || []
  const workflowBatches = workflowBatchesQuery.data || []
  const shipmentBatchOptions = shipmentBatchOptionsQuery.data || []
  const reworkCases = useMemo(() => reworkCasesQuery.data || [], [reworkCasesQuery.data])
  const summary = summaryQuery.data
  const totals = summary?.totals
  const keyword = query.trim().toLowerCase()
  const filteredReworkCases = useMemo(() => reworkCases.filter((item) => {
    const source = item.source
    const searchMatch = !keyword || [item.case_no, item.active_process_card_no, item.process_card_no, item.process_card?.card_no, item.reason, item.notes, source?.shipment_no, source?.order_no, source?.item_no, source?.product_name, source?.specification, source?.material]
      .some((value) => String(value || '').toLowerCase().includes(keyword))
    const round = Number(item.return_round || item.attempt_count || item.attempts?.length || 0)
    const quickMatch = reworkQuickFilter === 'ALL'
      || (reworkQuickFilter === 'WAITING_REWORK' && ['WAITING_REWORK', 'OPEN', 'PROCESSING'].includes(item.status))
      || (reworkQuickFilter === 'RESHIPPED' && ['RESHIPPED', 'COMPLETED'].includes(item.status))
      || (reworkQuickFilter === 'ROUND_1' && round === 1)
      || (reworkQuickFilter === 'ROUND_2' && round === 2)
      || (reworkQuickFilter === 'ROUND_3' && round === 3)
      || (reworkQuickFilter === 'ROUND_4_PLUS' && round >= 4)
    const opened = dayjs(item.opened_on)
    const dateMatch = !reworkRange || (opened.isValid() && !opened.isBefore(reworkRange[0].startOf('day')) && !opened.isAfter(reworkRange[1].endOf('day')))
    const reasonText = [item.primary_reason_detail?.name, item.primary_reason?.name, ...(item.secondary_reason_details || item.secondary_reasons || []).map((value) => value.name), item.reason_category_display, item.reason].join(' ').toLowerCase()
    const reasonMatch = !reworkReasonFilter || reasonText.includes(reworkReasonFilter.trim().toLowerCase())
    const sourceCustomer = String(source?.customer || '').toLowerCase()
    const customerMatch = !reworkCustomerFilter || sourceCustomer.includes(reworkCustomerFilter.trim().toLowerCase())
    return searchMatch && quickMatch && dateMatch && reasonMatch && customerMatch
  }), [keyword, reworkCases, reworkCustomerFilter, reworkQuickFilter, reworkRange, reworkReasonFilter])

  const refreshAfterShipment = async () => {
    await Promise.all([
      ordersQuery.refetch(),
      shipmentLedgerQuery.refetch(),
      shipmentOptionsQuery.refetch(),
      processCardsQuery.refetch(),
      unitWeightsQuery.refetch(),
      batchesQuery.refetch(),
      workflowBatchesQuery.refetch(),
      shipmentBatchOptionsQuery.refetch(),
      reworksQuery.refetch(),
      reworkCasesQuery.refetch(),
      summaryQuery.refetch(),
      queryClient.invalidateQueries({ queryKey: ['quality', 'returnable-batches'] }),
      queryClient.invalidateQueries({ queryKey: ['analytics'] }),
      queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
      queryClient.invalidateQueries({ queryKey: ['product-specifications'] }),
    ])
  }

  const refreshAfterShipmentInBackground = () => {
    void refreshAfterShipment().catch(() => {
      message.warning('业务数据已经保存，但部分看板刷新失败，请稍后手动刷新。')
    })
  }

  const upsertReworkCaseCache = (saved: QualityReworkCase | QualityReworkCase[]) => {
    const incoming = Array.isArray(saved) ? saved : [saved]
    if (!incoming.length) return
    queryClient.setQueryData<QualityReworkCase[]>(['quality', 'rework-cases'], (current = []) => {
      const merged = new Map(current.map((item) => [String(item.id), item]))
      incoming.forEach((item) => merged.set(String(item.id), { ...merged.get(String(item.id)), ...item }))
      return [...merged.values()].sort((left, right) =>
        String(right.opened_on || '').localeCompare(String(left.opened_on || ''))
        || String(right.id).localeCompare(String(left.id), undefined, { numeric: true }),
      )
    })
  }

  const markRelatedCachesStale = (keys: string[][]) => {
    keys.forEach((queryKey) => {
      void queryClient.invalidateQueries({ queryKey, refetchType: 'none' })
    })
  }

  const refreshReturnDataInBackground = (tasks: Array<() => Promise<unknown>>) => {
    void Promise.allSettled(tasks.map((task) => task()))
  }

  const refreshAfterReturn = (saved: QualityReworkCase | QualityReworkCase[]) => {
    upsertReworkCaseCache(saved)
    markRelatedCachesStale([
      ['quality', 'returnable-batches'],
      ['analytics'],
      ['dashboard'],
    ])
    refreshReturnDataInBackground([
      () => reworkCasesQuery.refetch(),
      () => processCardsQuery.refetch(),
      () => shipmentLedgerQuery.refetch(),
      () => summaryQuery.refetch(),
      () => ordersQuery.refetch(),
    ])
  }

  const refreshAfterReworkAttempt = (saved: QualityReworkAttempt, caseId: number | string) => {
    queryClient.setQueryData<QualityReworkCase[]>(['quality', 'rework-cases'], (current = []) => current.map((item) => {
      if (String(item.id) !== String(caseId)) return item
      const attempts = [...(item.attempts || []).filter((attempt) => String(attempt.id) !== String(saved.id)), saved]
        .sort((left, right) => Number(left.attempt_no || 0) - Number(right.attempt_no || 0))
      const status = saved.status === 'COMPLETED'
        ? 'COMPLETED'
        : saved.status === 'SCRAPPED'
          ? 'SCRAPPED'
          : saved.status === 'WAITING_REINSPECTION'
            ? 'WAITING_REINSPECTION'
            : 'PROCESSING'
      return {
        ...item,
        attempts,
        attempt_count: Math.max(Number(item.attempt_count || 0), attempts.length, Number(saved.attempt_no || 0)),
        status,
      }
    }))
    markRelatedCachesStale([['analytics'], ['dashboard']])
    refreshReturnDataInBackground([
      () => reworkCasesQuery.refetch(),
      () => shipmentLedgerQuery.refetch(),
      () => summaryQuery.refetch(),
    ])
  }

  const refreshAfterReturnChange = (saved: QualityReworkCase) => {
    upsertReworkCaseCache(saved)
    markRelatedCachesStale([
      ['quality', 'returnable-batches'],
      ['analytics'],
      ['dashboard'],
    ])
    refreshReturnDataInBackground([
      () => reworkCasesQuery.refetch(),
      () => processCardsQuery.refetch(),
      () => shipmentLedgerQuery.refetch(),
      () => summaryQuery.refetch(),
      () => ordersQuery.refetch(),
    ])
  }

  const refreshAfterCardReplacement = () => {
    markRelatedCachesStale([['quality', 'process-card-timeline']])
    refreshReturnDataInBackground([
      () => processCardsQuery.refetch(),
      () => reworkCasesQuery.refetch(),
    ])
  }

  const filteredEmployees = useMemo(() => employees.filter((item) => !keyword || [item.employee_no, item.name, item.team, item.role_display].some((value) => String(value || '').toLowerCase().includes(keyword))), [employees, keyword])
  const orderRows = useMemo<OrderRow[]>(() => {
    const stats = new Map((summary?.order_stats || []).map((item) => [item.order_id, item]))
    return orders
      .filter((item) => !keyword || [item.order_no, item.batch_no, item.product_code, item.product_name, item.specification, item.material].some((value) => value.toLowerCase().includes(keyword)))
      .map((order) => ({ order, stats: stats.get(order.id) }))
  }, [keyword, orders, summary?.order_stats])

  const ledgerValues = (values?: Array<string | null | undefined>) => [...new Set((values || []).map((value) => String(value || '').trim()).filter(Boolean))]
  const openLedgerDetail = (row: QualityShipmentLedgerRow) => {
    if (row.source_type === 'WEIGHTED') {
      const batch = row.batch || shipmentBatches.find((item) => String(item.id) === String(row.source_id))
      if (batch) setBatchReviewItem(batch)
      return
    }
    if (row.shipment) openShipmentForm(row.shipment)
  }
  const ledgerColumns: TableColumnsType<QualityShipmentLedgerRow> = [
    { title: '出货日期', dataIndex: 'shipment_date', fixed: 'left', width: 110, render: (value) => value ? formatQualityDate(value) : <Tag color="warning">待补日期</Tag> },
    { title: '出货单号', dataIndex: 'shipment_no', fixed: 'left', width: 190, render: (value, row) => <span><Button type="link" className="table-primary-link" onClick={() => openLedgerDetail(row)}>{value}</Button><br /><Tag color={row.source_type === 'WEIGHTED' ? 'blue' : 'default'}>{row.source_type === 'WEIGHTED' ? '重量出货' : '历史出货'}</Tag></span> },
    { title: '订单 / 项次', key: 'orders', width: 200, render: (_, row) => {
      const orders = ledgerValues(row.order_nos)
      const items = ledgerValues(row.item_nos)
      return <span><strong>{orders.join('、') || '-'}</strong><br /><Typography.Text type="secondary">{items.length ? `项次 ${items.join('、')}` : '-'}</Typography.Text></span>
    } },
    { title: '产品 / 规格 / 材质', key: 'product', width: 240, render: (_, row) => {
      const products = ledgerValues(row.product_names)
      const specifications = ledgerValues(row.specifications)
      const materials = ledgerValues(row.materials)
      return <span><strong>{products.join('、') || '-'}</strong><br /><Typography.Text type="secondary">{[specifications.join('、'), materials.join('、')].filter(Boolean).join(' · ') || '-'}</Typography.Text></span>
    } },
    { title: '交期', dataIndex: 'due_dates', width: 125, render: (values) => ledgerValues(values).map((value) => formatQualityDate(value)).join('、') || '-' },
    { title: '责任品检员', key: 'inspectors', width: 145, render: (_, row) => {
      const names = ledgerValues((row.inspectors || []).map((item) => item.name))
      return names.length ? names.join('、') : <Tag color="warning">待补录</Tag>
    } },
    { title: '质检 / 合格 / 不良', key: 'quality', width: 165, render: (_, row) => row.inspection_quantity == null
      ? '-'
      : <span>{qualityNumber(row.inspection_quantity)} / <span className="quality-good-text">{qualityNumber(row.qualified_quantity)}</span> / <span className={row.defective_quantity ? 'quality-danger-text' : ''}>{qualityNumber(row.defective_quantity)}</span></span> },
    { title: '实际出货', dataIndex: 'shipped_quantity', width: 110, render: (value) => <strong>{qualityNumber(value)}</strong> },
    { title: '累计净重', dataIndex: 'net_weight_kg', width: 115, render: (value) => value == null ? '-' : `${qualityNumber(value, 3)} kg` },
    { title: '累计退货', dataIndex: 'returned_quantity', width: 105, render: (value) => value == null ? '-' : <span className={value ? 'quality-danger-text' : ''}>{qualityNumber(value)}</span> },
    { title: '返工次数', dataIndex: 'rework_count', width: 105, render: (value) => value == null ? '-' : reworkCountTag(value) },
    { title: '状态', dataIndex: 'status', width: 100, render: (value) => <Tag color={value === 'CONFIRMED' ? 'success' : value === 'VOID' ? 'default' : 'warning'}>{value === 'CONFIRMED' ? '已确认' : value === 'VOID' ? '已作废' : '草稿'}</Tag> },
    { title: '操作', key: 'action', fixed: 'right', width: 100, render: (_, row) => <Button type="link" icon={<EyeOutlined />} onClick={() => openLedgerDetail(row)}>查看明细</Button> },
  ]

  const reworkColumns: TableColumnsType<ReturnRework> = [
    { title: '日期', dataIndex: 'rework_date', fixed: 'left', width: 110, render: (value) => formatQualityDate(value) },
    { title: '出货单 / 订单', key: 'shipment', fixed: 'left', width: 190, render: (_, record) => <span><strong>{record.shipment?.shipment_no || '-'}</strong><br /><Typography.Text type="secondary">{record.shipment?.order?.order_no || '-'}</Typography.Text></span> },
    { title: '原因', key: 'reason', width: 210, render: (_, record) => <span><Tag>{record.reason_category_display || REASON_META[record.reason_category] || record.reason_category}</Tag><br />{record.reason || '-'}</span> },
    { title: '责任品检员', key: 'responsible', width: 135, render: (_, record) => <Tag color="red">{record.responsible_inspector?.name || '-'}</Tag> },
    { title: '返工处理人', key: 'worker', width: 135, render: (_, record) => <Tag color="orange">{record.rework_employee?.name || '-'}</Tag> },
    { title: '退货 / 返工', key: 'quantities', width: 120, render: (_, record) => `${qualityNumber(record.returned_quantity)} / ${qualityNumber(record.reworked_quantity)}` },
    { title: '返工合格 / 报废', key: 'result', width: 145, render: (_, record) => <span><span className="quality-good-text">{qualityNumber(record.recovered_quantity)}</span> / <span className={record.scrap_quantity ? 'quality-danger-text' : ''}>{qualityNumber(record.scrap_quantity)}</span></span> },
    { title: '返工工时', dataIndex: 'work_hours', width: 105, render: (value) => `${qualityNumber(value, 2)} h` },
    { title: '状态', dataIndex: 'status', width: 100, render: (value: ReturnReworkStatus) => <Tag color={REWORK_STATUS_META[value].color}>{REWORK_STATUS_META[value].text}</Tag> },
    { title: '数据来源', key: 'readonly', fixed: 'right', width: 90, render: () => <Tag>历史只读</Tag> },
  ]

  const reworkCaseColumns: TableColumnsType<QualityReworkCase> = [
    { title: '退货返工记录', dataIndex: 'case_no', fixed: 'left', width: 180, render: (value, row) => <span><Tag color="orange">{row.return_label || `第 ${row.return_round || row.attempt_count || 1} 次退货返工`}</Tag><br /><Button type="link" className="table-primary-link" onClick={() => setReturnReworkDetail(row)}><strong>{value}</strong></Button><br /><Typography.Text type="secondary">{formatQualityDate(row.opened_on)}</Typography.Text></span> },
    { title: '流程卡', key: 'processCard', width: 190, render: (_, row) => <strong>{row.active_process_card_no || row.process_card_no || row.process_card?.card_no || row.source?.lines?.find((line) => line.card_no)?.card_no || '待绑定流程卡'}</strong> },
    { title: '原出货 / 订单', key: 'source', width: 230, render: (_, row) => <span><strong>{reworkCaseSourceTitle(row)}</strong><br /><Typography.Text type="secondary">{row.source ? `物理批号 ${row.source.shipment_unit_no} · 本组共${row.source.total_batches}批` : '历史来源摘要不完整'}</Typography.Text></span> },
    { title: '产品 / 规格 / 材质', key: 'product', width: 245, render: (_, row) => <span><strong>{row.source?.product_name || '-'}</strong><br /><Typography.Text type="secondary">{[row.source?.specification, row.source?.material].filter(Boolean).join(' · ') || '-'}</Typography.Text></span> },
    { title: '退回整批', key: 'wholeBatch', width: 150, render: (_, row) => row.source ? <span>{qualityNumber(row.source.pieces_per_batch)} 件<br /><Typography.Text type="secondary">{qualityNumber(row.source.single_batch_net_weight_kg, 3)} kg</Typography.Text></span> : `${qualityNumber(row.affected_quantity)} 件` },
    { title: '当前返工次数', key: 'attempts', width: 125, render: (_, row) => reworkCountTag(row.return_round || row.attempt_count || row.attempts?.length || 0) },
    { title: '状态', dataIndex: 'status', width: 120, render: (value) => <Tag color={value === 'RESHIPPED' || value === 'COMPLETED' ? 'success' : value === 'SCRAPPED' || value === 'CANCELLED' ? 'default' : 'warning'}>{value === 'WAITING_REWORK' || value === 'OPEN' || value === 'PROCESSING' ? '待返工' : value === 'RESHIPPED' || value === 'COMPLETED' ? '已重新出货' : value === 'WAITING_REINSPECTION' ? '待复检' : value === 'SCRAPPED' ? '已报废' : '已取消'}</Tag> },
    { title: '操作', key: 'action', fixed: 'right', width: 180, render: (_, row) => <Space size={2}><Button type="link" onClick={() => setReturnReworkDetail(row)}>查看</Button>{row.origin === 'CUSTOMER_RETURN' && !['CANCELLED', 'SCRAPPED'].includes(row.status) && <Button type="link" onClick={() => setReturnReworkAttempt(row)}>登记下一轮</Button>}</Space> },
  ]

  const orderColumns: TableColumnsType<OrderRow> = [
    { title: '订单 / 批次', key: 'order', fixed: 'left', width: 190, render: (_, row) => <span><strong>{row.order.order_no}</strong><br /><Typography.Text type="secondary">{row.order.batch_no}</Typography.Text></span> },
    { title: '产品 / 规格', key: 'product', width: 200, render: (_, row) => <span>{row.order.product_code} · {row.order.product_name}<br /><Typography.Text type="secondary">{row.order.specification} · {row.order.material}</Typography.Text></span> },
    { title: '订单数量', key: 'order_quantity', width: 105, render: (_, row) => qualityNumber(row.order.order_quantity) },
    { title: '状态', key: 'status', width: 95, render: (_, row) => <Tag color={ORDER_STATUS_META[row.order.status].color}>{row.order.status_display || ORDER_STATUS_META[row.order.status].text}</Tag> },
    { title: '质检数量', key: 'inspection', width: 105, render: (_, row) => qualityNumber(row.stats?.inspection_quantity) },
    { title: '出货数量', key: 'shipped', width: 105, render: (_, row) => qualityNumber(row.stats?.shipped_quantity) },
    { title: '退货数量', key: 'returned', width: 105, render: (_, row) => <span className={row.stats?.returned_quantity ? 'quality-danger-text' : ''}>{qualityNumber(row.stats?.returned_quantity)}</span> },
    { title: '返工数量', key: 'reworked', width: 105, render: (_, row) => qualityNumber(row.stats?.reworked_quantity) },
    { title: '一次合格率', key: 'first_pass', width: 120, render: (_, row) => rateText(row.stats?.first_pass_rate) },
    { title: '退货率', key: 'return_rate', width: 100, render: (_, row) => <span className={Number(row.stats?.return_rate || 0) > 0 ? 'quality-danger-text' : ''}>{rateText(row.stats?.return_rate)}</span> },
    { title: '返工通过率', key: 'rework_pass', width: 120, render: (_, row) => rateText(row.stats?.rework_pass_rate) },
    { title: '返工次数', key: 'rework_count', width: 110, render: (_, row) => reworkCountTag(row.stats?.rework_count || 0) },
    { title: '操作', key: 'action', fixed: 'right', width: 100, render: () => <Button type="link" onClick={() => navigate('/orders')}>订单管理</Button> },
  ]

  const employeeColumns: TableColumnsType<QualityEmployee> = [
    { title: '工号', dataIndex: 'employee_no', fixed: 'left', width: 130, render: (value) => <strong>{value}</strong> },
    { title: '姓名', dataIndex: 'name', width: 130 },
    { title: '班组', dataIndex: 'team', width: 150, render: (value) => value || '-' },
    { title: '岗位角色', dataIndex: 'role', width: 140, render: (value: QualityEmployeeRole, row) => <Tag color={ROLE_META[value]?.color}>{row.role_display || ROLE_META[value]?.text || value}</Tag> },
    { title: '状态', dataIndex: 'is_active', width: 100, render: (value) => <Tag color={value ? 'success' : 'default'}>{value ? '启用' : '停用'}</Tag> },
    { title: '备注', dataIndex: 'notes', ellipsis: true, render: (value) => value || '-' },
    { title: '操作', key: 'action', fixed: 'right', width: 76, render: (_, row) => <Button type="link" icon={<EditOutlined />} onClick={() => setEmployeeForm({ employee: row })}>编辑</Button> },
  ]

  const tableCard = <T,>(rows: T[], columns: TableColumnsType<T>, loading: boolean, rowKey: string | ((record: T) => string | number), scrollX: number, emptyText: string) => (
    <Card className="data-card" styles={{ body: { padding: 0 } }}>
      <Table<T> rowKey={rowKey} loading={loading} dataSource={rows} columns={columns} scroll={{ x: scrollX }} pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (total) => `共 ${total} 条` }} locale={{ emptyText }} />
    </Card>
  )

  const tabItems = [
    {
      key: 'workflow',
      label: '流程卡出货',
      children: <div className="quality-tab-content">
        {(processCardsQuery.error || unitWeightsQuery.error || batchesQuery.error || workflowBatchesQuery.error || shipmentBatchOptionsQuery.error || reworkCasesQuery.error) && <Alert type="warning" showIcon style={{ marginBottom: 16 }} title="流程卡重量出货模块暂不可用" description="当前服务器未返回一期流程卡接口，页面已保留原有件数出货功能；完成后端迁移后刷新即可启用。" />}
        <QualityShippingWorkflow orders={orders} employees={employees} processCards={processCards} shipments={shipmentOptions} batches={workflowBatches} reworks={reworks} reworkCases={filteredReworkCases} searchText={query} loading={ordersQuery.isLoading || processCardsQuery.isLoading || workflowBatchesQuery.isLoading} onOpenShipment={() => openShipmentForm()} onOpenRework={openFlowCardReturn} onOpenTimeline={() => undefined} onSubmitBatch={async (payload) => { await qualityWorkflowApi.createAndConfirmShipmentBatch(payload); refreshAfterShipmentInBackground() }} onSaveProcessCard={async (body, card) => { await (card ? qualityWorkflowApi.updateProcessCard(card.id, body) : qualityWorkflowApi.createProcessCard(body)); await processCardsQuery.refetch() }} /><QualityWorkflowManagement orders={orders} employees={employees} cards={processCards} unitWeights={unitWeights} batches={shipmentBatches} shipmentOptions={shipmentBatchOptions} reworkCases={filteredReworkCases} onOpenReturnRework={openFlowCardReturn} onOpenReturnReworkDetail={setReturnReworkDetail} onOpenReturnReworkAttempt={setReturnReworkAttempt} onRefresh={refreshAfterShipment} /></div>,
    },
    {
      key: 'daily',
      label: '每日出货',
      children: <div className="quality-tab-content">
        <DailyTrend rows={summary?.daily_trend || []} loading={summaryQuery.isLoading} />
        <div className="section-heading"><div><Typography.Title level={3}>每日出货台账</Typography.Title><Typography.Text type="secondary">统一显示重量出货与历史出货；点击出货单号即可查看产品、材质、称重和批数明细。</Typography.Text></div><Button type="primary" icon={<PlusOutlined />} onClick={() => openShipmentForm()}>新增出货</Button></div>
        {tableCard(shipmentLedger, ledgerColumns, shipmentLedgerQuery.isLoading, 'key', 1765, '当前筛选条件下暂无出货记录')}
      </div>,
    },
    {
      key: 'reworks',
      label: '退货返工',
      children: <div className="quality-tab-content">
        <Alert className="quality-responsibility-alert" type="info" showIcon title="绩效口径分开统计" description="责任品检员记录退货责任；返工处理人记录实际返工工作量，两项不会混为同一指标。" />
        {mobile && <div className="quality-return-scan-toolbar"><Button type="primary" icon={<QrcodeOutlined />} onClick={openFlowCardReturn}>扫码登记退货</Button><Button onClick={() => setReplacementOpen(true)}>补卡换号</Button></div>}
        <div className="section-heading"><div><Typography.Title level={3}>流程卡退货返工追踪</Typography.Title><Typography.Text type="secondary">每批产品按流程卡独立显示“第N次退货返工”；同卡再次退回会自动接续次数，点开可看完整时间线。</Typography.Text></div><Space wrap><Button onClick={() => setReplacementOpen(true)}>补卡换号</Button><Button type="primary" icon={<QrcodeOutlined />} onClick={openFlowCardReturn}>扫描登记退货</Button></Space></div>
        <div className="quality-return-filter-chips" aria-label="退货返工快捷筛选">
          {[
            ['ALL', '全部'],
            ['WAITING_REWORK', '待返工'],
            ['ROUND_1', '第1次'],
            ['ROUND_2', '第2次'],
            ['ROUND_3', '第3次'],
            ['RESHIPPED', '已重新出货'],
            ['ROUND_4_PLUS', '第4次+'],
          ].map(([value, label]) => <Button key={value} type={reworkQuickFilter === value ? 'primary' : 'default'} onClick={() => setReworkQuickFilter(value)}>{label}</Button>)}
          <Button onClick={() => setReworkMoreOpen((value) => !value)}>{reworkMoreOpen ? '收起筛选' : '更多筛选'}</Button>
        </div>
        {reworkMoreOpen && <Card size="small" className="quality-return-more-filters">
          <Row gutter={[10, 10]}>
            <Col xs={24} md={8}><RangePicker style={{ width: '100%' }} value={reworkRange} onChange={(value) => setReworkRange(value?.[0] && value?.[1] ? [value[0], value[1]] : null)} placeholder={['退货开始日期', '退货结束日期']} /></Col>
            <Col xs={24} md={8}><Input allowClear value={reworkReasonFilter} onChange={(event) => setReworkReasonFilter(event.target.value)} placeholder="按主要/次要退货原因" /></Col>
            <Col xs={24} md={8}><Input allowClear value={reworkCustomerFilter} onChange={(event) => setReworkCustomerFilter(event.target.value)} placeholder="按客户筛选" /></Col>
          </Row>
        </Card>}
        {mobile
          ? <QualityReworkCaseMobileList items={filteredReworkCases} loading={reworkCasesQuery.isLoading} emptyText={keyword ? '没有符合当前搜索条件的退货返工记录' : '暂无整批退货返工记录'} onOpen={setReturnReworkDetail} onAddAttempt={setReturnReworkAttempt} />
          : tableCard(filteredReworkCases, reworkCaseColumns, reworkCasesQuery.isLoading, 'id', 1420, keyword ? '没有符合当前搜索条件的退货返工记录' : '暂无整批退货返工记录')}
        <div className="quality-return-manual-fallback"><Typography.Text type="secondary">旧数据没有流程卡或首次扫码无法找到原出货？</Typography.Text><Button type="link" onClick={() => setReturnReworkOpen(true)}>登记整批退货返工（手工选择原出货）</Button></div>
        {!!reworks.length && <div className="quality-legacy-reworks"><div className="section-heading"><div><Typography.Title level={4}>历史旧版退货返工</Typography.Title><Typography.Text type="secondary">旧数据保留用于追溯，只读展示，不再从这里新增或修改。</Typography.Text></div><Tag>只读</Tag></div>{tableCard(reworks, reworkColumns, reworksQuery.isLoading, 'id', 1350, '暂无历史旧版记录')}</div>}
      </div>,
    },
    {
      key: 'orders',
      label: '订单统计',
      children: <div className="quality-tab-content">
        <div className="section-heading"><div><Typography.Title level={3}>订单质量表现</Typography.Title><Typography.Text type="secondary">订单基础资料统一在“订单管理”维护，本页只汇总质检、出货、退货和返工表现。</Typography.Text></div><Button type="primary" onClick={() => navigate('/orders')}>前往订单管理</Button></div>
        {tableCard(orderRows, orderColumns, ordersQuery.isLoading || summaryQuery.isLoading, (row) => row.order.id, 1510, '暂无订单批次')}
      </div>,
    },
    {
      key: 'employees',
      label: '员工档案',
      children: <div className="quality-tab-content">
        <div className="section-heading"><div><Typography.Title level={3}>品检与返工员工档案</Typography.Title><Typography.Text type="secondary">使用唯一工号维护员工，确保跨月份绩效汇总稳定。</Typography.Text></div><Button type="primary" icon={<PlusOutlined />} onClick={() => setEmployeeForm({})}>新增员工</Button></div>
        {tableCard(filteredEmployees, employeeColumns, employeesQuery.isLoading, 'id', 850, '暂无员工档案')}
      </div>,
    },
  ]

  // The additive一期 endpoints may be absent during a rolling deployment.
  // Their errors are shown inside the workflow tab, while the legacy quality
  // dashboard remains usable.
  const resetFilters = () => {
    setRange([dayjs().startOf('month'), dayjs().endOf('month')])
    setDueRange(null)
    setQuery('')
    setShipmentStatus('CONFIRMED')
    setOrderStatus('')
    setDeliveryStatus('')
    setInspectorFilter(undefined)
    setOrdering('-shipment_date')
  }

  const anyError = summaryQuery.error || employeesQuery.error || ordersQuery.error || shipmentLedgerQuery.error || reworksQuery.error

  return (
    <div className="page-container quality-page">
      <PageTitle
        title="品检出货与退货返工"
        description="记录每日质检与出货、每次退货返工和订单批次；员工绩效与跨模块趋势统一在“数据分析”查看。"
        extra={<Space wrap><Button icon={<QrcodeOutlined />} onClick={openFlowCardReturn}>扫码登记退货</Button><Button onClick={() => setReturnReworkOpen(true)}>登记整批退货返工（无扫码）</Button><Button type="primary" icon={<PlusOutlined />} onClick={() => openShipmentForm()}>新增出货</Button></Space>}
      />

      <Card className="filter-card quality-filter-card">
        <div className="quality-filter-row">
          <RangePicker allowClear={false} value={range} onChange={(value) => value?.[0] && value?.[1] && setRange([value[0], value[1]])} placeholder={['出货开始日期', '出货结束日期']} />
          <Input allowClear prefix={<SearchOutlined />} placeholder="搜索出货单、订单、产品、规格、材质或品检员" value={query} onChange={(event) => setQuery(event.target.value)} />
          <Select value={shipmentStatus} onChange={setShipmentStatus} options={[{ value: 'CONFIRMED', label: '已确认出货' }, { value: 'DRAFT', label: '草稿 / 待确认' }, { value: 'VOID', label: '已作废' }]} />
        </div>
        <div className="quality-filter-row">
          <RangePicker allowClear value={dueRange} onChange={(value) => setDueRange(value?.[0] && value?.[1] ? [value[0], value[1]] : null)} placeholder={['交期开始', '交期结束']} />
          <Select allowClear value={inspectorFilter} onChange={setInspectorFilter} showSearch optionFilterProp="label" placeholder="按品检员筛选（可清空）" options={employees.filter((employee) => ['INSPECTOR', 'BOTH'].includes(employee.role)).map((employee) => ({ value: employee.id, label: `${employee.employee_no} · ${employee.name}` }))} />
          <Select allowClear value={orderStatus || undefined} onChange={(value) => setOrderStatus(value || '')} placeholder="按订单状态筛选" options={[{ value: 'OPEN', label: '进行中订单' }, { value: 'COMPLETED', label: '已完成订单' }, { value: 'CANCELLED', label: '已取消订单' }]} />
        </div>
        <div className="quality-filter-row">
          <Select allowClear value={deliveryStatus || undefined} onChange={(value) => setDeliveryStatus(value || '')} placeholder="按关联订单出货进度筛选" options={[{ value: 'PARTIAL', label: '部分出货' }, { value: 'SHIPPED', label: '已完成出货' }, { value: 'CANCELLED', label: '订单已取消' }]} />
          <Select value={ordering} onChange={setOrdering} options={[{ value: '-shipment_date', label: '出货日期：新到旧' }, { value: 'shipment_date', label: '出货日期：旧到新' }, { value: 'due_date', label: '交期：早到晚' }, { value: '-due_date', label: '交期：晚到早' }]} />
          <Space className="quality-filter-actions"><Typography.Text type="secondary">出货区间：{dateFrom} 至 {dateTo}</Typography.Text><Button icon={<ReloadOutlined />} onClick={resetFilters}>重置筛选</Button></Space>
        </div>
      </Card>

      {anyError && <Alert className="quality-page-alert" type="error" showIcon title="部分品检数据读取失败" description={(anyError as Error).message} />}

      <Row gutter={[14, 14]} className="quality-kpis">
        <Col xs={12} md={6}><Card className="quality-kpi inspection"><Statistic title="质检数量" value={totals?.inspection_quantity || 0} suffix="件" prefix={<AuditOutlined />} /><span>一次合格率 {rateText(totals?.first_pass_rate)}</span></Card></Col>
        <Col xs={12} md={6}><Card className="quality-kpi shipment"><Statistic title="出货数量" value={totals?.shipped_quantity || 0} suffix="件" prefix={<SendOutlined />} /><span>共 {qualityNumber(totals?.shipment_count)} 批出货</span></Card></Col>
        <Col xs={12} md={6}><Card className="quality-kpi return"><Statistic title="退货数量" value={totals?.returned_quantity || 0} suffix="件" prefix={<WarningOutlined />} /><span>退货率 {rateText(totals?.return_rate)}</span></Card></Col>
        <Col xs={12} md={6}><Card className="quality-kpi rework"><Statistic title="返工处理数量" value={totals?.reworked_quantity || 0} suffix="件" prefix={<CheckCircleOutlined />} /><span>返工通过率 {rateText(totals?.rework_pass_rate)}</span></Card></Col>
      </Row>

      <Card className="quality-tabs-card data-card">
        <Tabs activeKey={activeTab} onChange={setActiveTab} items={tabItems} />
      </Card>

      {/* Existing ledger rows keep the legacy edit fields; every new shipment
          entry is routed through the weighted workflow drawer by the
          compatibility component. */}
      <QualityShipmentDrawer
        key={`shipment-session-${shipmentSessionKey}`}
        open={!!shipmentForm}
        shipment={shipmentForm?.shipment}
        orders={orders}
        employees={employees}
        existingShipments={shipmentOptions}
        existingBatches={shipmentBatchOptions}
        resetKey={shipmentSessionKey}
        onClose={() => {
          setShipmentForm(undefined)
          setResumeReturnAfterShipment(false)
        }}
        onSubmit={(payload) => qualityWorkflowApi.createAndConfirmShipmentBatch(payload)}
        onSaved={async (result) => {
          refreshAfterShipmentInBackground()
          if (resumeReturnAfterShipment) {
            const status = result && typeof result === 'object' && 'status' in result ? String(result.status || '').toUpperCase() : ''
            if (status === 'CONFIRMED') {
              setResumeReturnAfterShipment(false)
              setReturnReworkOpen(true)
            } else {
              message.info('原出货目前仍是草稿；请先确认出货，确认成功后才能选择整批退货。')
            }
          }
        }}
      />
      <ShipmentBatchReviewDrawer
        open={!!batchReviewItem}
        item={batchReviewItem}
        employees={employees}
        onClose={() => setBatchReviewItem(undefined)}
        onSaved={async () => refreshAfterShipmentInBackground()}
      />
      <QualityReturnReworkDrawer
        open={returnReworkOpen}
        onClose={() => setReturnReworkOpen(false)}
        onBackfillShipment={() => {
          setReturnReworkOpen(false)
          setResumeReturnAfterShipment(true)
          openShipmentForm()
        }}
        onSaved={refreshAfterReturn}
      />
      <QualityFlowCardReturnDrawer
        key={`flow-card-return-session-${flowCardReturnSessionKey}`}
        open={flowCardReturnOpen}
        employees={employees}
        onClose={() => setFlowCardReturnOpen(false)}
        onBackfillShipment={() => {
          setFlowCardReturnOpen(false)
          setResumeReturnAfterShipment(true)
          openShipmentForm()
        }}
        onSaved={refreshAfterReturn}
      />
      <QualityProcessCardReplacementDrawer open={replacementOpen} onClose={() => setReplacementOpen(false)} onSaved={refreshAfterCardReplacement} />
      <QualityReturnReworkAttemptDrawer open={!!returnReworkAttempt} item={returnReworkAttempt} employees={employees} onClose={() => setReturnReworkAttempt(undefined)} onSaved={refreshAfterReworkAttempt} />
      <QualityReworkCaseDetailDrawer open={!!returnReworkDetail} item={returnReworkDetail} onClose={() => setReturnReworkDetail(undefined)} onAddAttempt={setReturnReworkAttempt} onSaved={refreshAfterReturnChange} />
      <QualityEmployeeDrawer open={!!employeeForm} employee={employeeForm?.employee} onClose={() => setEmployeeForm(undefined)} />
    </div>
  )
}
