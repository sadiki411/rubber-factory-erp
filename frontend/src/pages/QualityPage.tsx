import {
  AuditOutlined,
  CheckCircleOutlined,
  EditOutlined,
  EyeOutlined,
  PlusOutlined,
  ReloadOutlined,
  SearchOutlined,
  SendOutlined,
  ToolOutlined,
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
import { formatQualityDate, isHighReworkCount, qualityNumber, reworkCaseSourceTitle } from '../quality'
import type {
  QualityDailyTrend,
  QualityEmployee,
  QualityEmployeeRole,
  QualityOrder,
  QualityOrderStatistics,
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
  const [batchReviewItem, setBatchReviewItem] = useState<QualityShipmentBatch>()
  const [returnReworkOpen, setReturnReworkOpen] = useState(false)
  const [returnReworkDetail, setReturnReworkDetail] = useState<QualityReworkCase>()
  const [returnReworkAttempt, setReturnReworkAttempt] = useState<QualityReworkCase>()
  const [resumeReturnAfterShipment, setResumeReturnAfterShipment] = useState(false)
  const [employeeForm, setEmployeeForm] = useState<{ employee?: QualityEmployee }>()
  const dateFrom = range[0].format('YYYY-MM-DD')
  const dateTo = range[1].format('YYYY-MM-DD')
  const dueDateFrom = dueRange?.[0].format('YYYY-MM-DD')
  const dueDateTo = dueRange?.[1].format('YYYY-MM-DD')

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
    if (!keyword) return true
    const source = item.source
    return [item.case_no, item.reason, item.notes, source?.shipment_no, source?.order_no, source?.item_no, source?.product_name, source?.specification, source?.material]
      .some((value) => String(value || '').toLowerCase().includes(keyword))
  }), [keyword, reworkCases])

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
      queryClient.invalidateQueries({ queryKey: ['orders'] }),
      queryClient.invalidateQueries({ queryKey: ['analytics'] }),
      queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
      queryClient.invalidateQueries({ queryKey: ['product-specifications'] }),
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
    if (row.shipment) setShipmentForm({ shipment: row.shipment })
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
    { title: '退货返工记录', dataIndex: 'case_no', fixed: 'left', width: 150, render: (value, row) => <span><Button type="link" className="table-primary-link" onClick={() => setReturnReworkDetail(row)}><strong>{value}</strong></Button><br /><Typography.Text type="secondary">{formatQualityDate(row.opened_on)}</Typography.Text></span> },
    { title: '原出货 / 订单', key: 'source', width: 230, render: (_, row) => <span><strong>{reworkCaseSourceTitle(row)}</strong><br /><Typography.Text type="secondary">{row.source ? `物理批号 ${row.source.shipment_unit_no} · 本组共${row.source.total_batches}批` : '历史来源摘要不完整'}</Typography.Text></span> },
    { title: '产品 / 规格 / 材质', key: 'product', width: 245, render: (_, row) => <span><strong>{row.source?.product_name || '-'}</strong><br /><Typography.Text type="secondary">{[row.source?.specification, row.source?.material].filter(Boolean).join(' · ') || '-'}</Typography.Text></span> },
    { title: '退回整批', key: 'wholeBatch', width: 150, render: (_, row) => row.source ? <span>{qualityNumber(row.source.pieces_per_batch)} 件<br /><Typography.Text type="secondary">{qualityNumber(row.source.single_batch_net_weight_kg, 3)} kg</Typography.Text></span> : `${qualityNumber(row.affected_quantity)} 件` },
    { title: '返工轮次', key: 'attempts', width: 115, render: (_, row) => reworkCountTag(row.attempt_count || row.attempts?.length || 0) },
    { title: '状态', dataIndex: 'status', width: 110, render: (value) => <Tag color={value === 'COMPLETED' ? 'success' : value === 'SCRAPPED' || value === 'CANCELLED' ? 'default' : 'warning'}>{value === 'OPEN' ? '待返工' : value === 'PROCESSING' ? '返工中' : value === 'WAITING_REINSPECTION' ? '待复检' : value === 'COMPLETED' ? '已完成' : value === 'SCRAPPED' ? '已报废' : '已取消'}</Tag> },
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
        <QualityShippingWorkflow orders={orders} employees={employees} processCards={processCards} shipments={shipmentOptions} batches={workflowBatches} reworks={reworks} reworkCases={filteredReworkCases} searchText={query} loading={ordersQuery.isLoading || processCardsQuery.isLoading || workflowBatchesQuery.isLoading} onOpenShipment={() => setShipmentForm({})} onOpenRework={() => setReturnReworkOpen(true)} onOpenTimeline={() => undefined} onSubmitBatch={async (payload) => { await qualityWorkflowApi.createAndConfirmShipmentBatch(payload); await refreshAfterShipment() }} onSaveProcessCard={async (body, card) => { await (card ? qualityWorkflowApi.updateProcessCard(card.id, body) : qualityWorkflowApi.createProcessCard(body)); await processCardsQuery.refetch() }} /><QualityWorkflowManagement orders={orders} employees={employees} cards={processCards} unitWeights={unitWeights} batches={shipmentBatches} shipmentOptions={shipmentBatchOptions} reworkCases={filteredReworkCases} onOpenReturnRework={() => setReturnReworkOpen(true)} onOpenReturnReworkDetail={setReturnReworkDetail} onOpenReturnReworkAttempt={setReturnReworkAttempt} onRefresh={async () => { await refreshAfterShipment(); await reworkCasesQuery.refetch() }} /></div>,
    },
    {
      key: 'daily',
      label: '每日出货',
      children: <div className="quality-tab-content">
        <DailyTrend rows={summary?.daily_trend || []} loading={summaryQuery.isLoading} />
        <div className="section-heading"><div><Typography.Title level={3}>每日出货台账</Typography.Title><Typography.Text type="secondary">统一显示重量出货与历史出货；点击出货单号即可查看产品、材质、称重和批数明细。</Typography.Text></div><Button type="primary" icon={<PlusOutlined />} onClick={() => setShipmentForm({})}>新增出货</Button></div>
        {tableCard(shipmentLedger, ledgerColumns, shipmentLedgerQuery.isLoading, 'key', 1765, '当前筛选条件下暂无出货记录')}
      </div>,
    },
    {
      key: 'reworks',
      label: '退货返工',
      children: <div className="quality-tab-content">
        <Alert className="quality-responsibility-alert" type="info" showIcon title="绩效口径分开统计" description="责任品检员记录退货责任；返工处理人记录实际返工工作量，两项不会混为同一指标。" />
        <div className="section-heading"><div><Typography.Title level={3}>整批退货返工记录</Typography.Title><Typography.Text type="secondary">从已确认出货中选择一整批，系统自动带入件数、重量、订单和品检员；后续按 R1、R2、R3 追加返工轮次。</Typography.Text></div><Button type="primary" icon={<PlusOutlined />} onClick={() => setReturnReworkOpen(true)}>登记整批退货返工</Button></div>
        {mobile
          ? <QualityReworkCaseMobileList items={filteredReworkCases} loading={reworkCasesQuery.isLoading} emptyText={keyword ? '没有符合当前搜索条件的退货返工记录' : '暂无整批退货返工记录'} onOpen={setReturnReworkDetail} onAddAttempt={setReturnReworkAttempt} />
          : tableCard(filteredReworkCases, reworkCaseColumns, reworkCasesQuery.isLoading, 'id', 1230, keyword ? '没有符合当前搜索条件的退货返工记录' : '暂无整批退货返工记录')}
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
        extra={<Space wrap><Button icon={<ToolOutlined />} onClick={() => setReturnReworkOpen(true)}>登记整批退货返工</Button><Button type="primary" icon={<PlusOutlined />} onClick={() => setShipmentForm({})}>新增出货</Button></Space>}
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
        open={!!shipmentForm}
        shipment={shipmentForm?.shipment}
        orders={orders}
        employees={employees}
        processCards={processCards}
        existingShipments={shipmentOptions}
        existingBatches={shipmentBatchOptions}
        onClose={() => {
          setShipmentForm(undefined)
          setResumeReturnAfterShipment(false)
        }}
        onSubmit={(payload) => qualityWorkflowApi.createAndConfirmShipmentBatch(payload)}
        onSaved={async (result) => {
          await refreshAfterShipment()
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
        onSaved={refreshAfterShipment}
      />
      <QualityReturnReworkDrawer
        open={returnReworkOpen}
        onClose={() => setReturnReworkOpen(false)}
        onBackfillShipment={() => {
          setReturnReworkOpen(false)
          setResumeReturnAfterShipment(true)
          setShipmentForm({})
        }}
        onSaved={refreshAfterShipment}
      />
      <QualityReturnReworkAttemptDrawer open={!!returnReworkAttempt} item={returnReworkAttempt} employees={employees} onClose={() => setReturnReworkAttempt(undefined)} onSaved={refreshAfterShipment} />
      <QualityReworkCaseDetailDrawer open={!!returnReworkDetail} item={returnReworkDetail} onClose={() => setReturnReworkDetail(undefined)} onAddAttempt={setReturnReworkAttempt} onSaved={refreshAfterShipment} />
      <QualityEmployeeDrawer open={!!employeeForm} employee={employeeForm?.employee} onClose={() => setEmployeeForm(undefined)} />
    </div>
  )
}
