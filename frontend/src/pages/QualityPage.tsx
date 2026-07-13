import {
  AuditOutlined,
  CheckCircleOutlined,
  EditOutlined,
  PlusOutlined,
  SearchOutlined,
  SendOutlined,
  ToolOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import { Alert, Button, Card, Col, DatePicker, Empty, Input, Progress, Row, Skeleton, Space, Statistic, Table, Tabs, Tag, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import { useQuery } from '@tanstack/react-query'
import dayjs, { type Dayjs } from 'dayjs'
import { useMemo, useState } from 'react'
import { qualityApi, toList } from '../api/client'
import {
  QualityEmployeeDrawer,
  QualityOrderDrawer,
  QualityReworkDrawer,
  QualityShipmentDrawer,
} from '../components/QualityFormDrawers'
import { PageTitle } from '../components/PageTitle'
import { formatQualityDate, isHighReworkCount, qualityNumber } from '../quality'
import type {
  QualityDailyTrend,
  QualityEmployee,
  QualityEmployeeRole,
  QualityEmployeeStatistics,
  QualityOrder,
  QualityOrderStatistics,
  QualityShipment,
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
  const [range, setRange] = useState<[Dayjs, Dayjs]>([dayjs().startOf('month'), dayjs().endOf('month')])
  const [query, setQuery] = useState('')
  const [activeTab, setActiveTab] = useState('daily')
  const [shipmentForm, setShipmentForm] = useState<{ shipment?: QualityShipment }>()
  const [reworkForm, setReworkForm] = useState<{ rework?: ReturnRework }>()
  const [orderForm, setOrderForm] = useState<{ order?: QualityOrder }>()
  const [employeeForm, setEmployeeForm] = useState<{ employee?: QualityEmployee }>()
  const dateFrom = range[0].format('YYYY-MM-DD')
  const dateTo = range[1].format('YYYY-MM-DD')

  const summaryQuery = useQuery({
    queryKey: ['quality', 'summary', dateFrom, dateTo],
    queryFn: () => qualityApi.summary({ date_from: dateFrom, date_to: dateTo }),
  })
  const employeesQuery = useQuery({
    queryKey: ['quality', 'employees'],
    queryFn: async () => toList(await qualityApi.listEmployees({ page_size: 1000 })),
  })
  const ordersQuery = useQuery({
    queryKey: ['quality', 'orders'],
    queryFn: async () => toList(await qualityApi.listOrders({ page_size: 1000 })),
  })
  const shipmentsQuery = useQuery({
    queryKey: ['quality', 'shipments', { dateFrom, dateTo, query }],
    queryFn: async () => toList(await qualityApi.listShipments({ q: query, date_from: dateFrom, date_to: dateTo, page_size: 1000 })),
  })
  const shipmentOptionsQuery = useQuery({
    queryKey: ['quality', 'shipments', 'options'],
    queryFn: async () => toList(await qualityApi.listShipments({ page_size: 1000 })),
  })
  const reworksQuery = useQuery({
    queryKey: ['quality', 'reworks', { dateFrom, dateTo, query }],
    queryFn: async () => toList(await qualityApi.listReworks({ q: query, date_from: dateFrom, date_to: dateTo, page_size: 1000 })),
  })

  const employees = useMemo(() => employeesQuery.data || [], [employeesQuery.data])
  const orders = useMemo(() => ordersQuery.data || [], [ordersQuery.data])
  const shipments = shipmentsQuery.data || []
  const shipmentOptions = shipmentOptionsQuery.data || []
  const reworks = reworksQuery.data || []
  const summary = summaryQuery.data
  const totals = summary?.totals
  const keyword = query.trim().toLowerCase()

  const filteredEmployees = useMemo(() => employees.filter((item) => !keyword || [item.employee_no, item.name, item.team, item.role_display].some((value) => String(value || '').toLowerCase().includes(keyword))), [employees, keyword])
  const performanceRows = useMemo(() => (summary?.employee_stats || []).filter((item) => !keyword || [item.employee_no, item.name, item.team].some((value) => String(value || '').toLowerCase().includes(keyword))), [keyword, summary?.employee_stats])
  const orderRows = useMemo<OrderRow[]>(() => {
    const stats = new Map((summary?.order_stats || []).map((item) => [item.order_id, item]))
    return orders
      .filter((item) => !keyword || [item.order_no, item.batch_no, item.product_code, item.product_name, item.specification, item.material].some((value) => value.toLowerCase().includes(keyword)))
      .map((order) => ({ order, stats: stats.get(order.id) }))
  }, [keyword, orders, summary?.order_stats])

  const shipmentColumns: TableColumnsType<QualityShipment> = [
    { title: '出货日期', dataIndex: 'shipment_date', fixed: 'left', width: 110, render: (value) => formatQualityDate(value) },
    { title: '出货单号', dataIndex: 'shipment_no', fixed: 'left', width: 160, render: (value, record) => <Button type="link" className="table-primary-link" onClick={() => setShipmentForm({ shipment: record })}>{value}</Button> },
    { title: '订单 / 批次', key: 'order', width: 190, render: (_, record) => <span><strong>{record.order?.order_no || '-'}</strong><br /><Typography.Text type="secondary">{record.order?.batch_no || '-'}</Typography.Text></span> },
    { title: '产品 / 规格', key: 'product', width: 180, render: (_, record) => <span>{record.order?.product_name || '-'}<br /><Typography.Text type="secondary">{record.order?.specification || '-'}</Typography.Text></span> },
    { title: '责任品检员', key: 'inspector', width: 130, render: (_, record) => <strong>{record.inspector?.name || '-'}</strong> },
    { title: '质检数量', dataIndex: 'inspection_quantity', width: 105, render: (value) => qualityNumber(value) },
    { title: '合格 / 不良', key: 'quality', width: 120, render: (_, record) => <span><span className="quality-good-text">{qualityNumber(record.qualified_quantity)}</span> / <span className={record.defective_quantity ? 'quality-danger-text' : ''}>{qualityNumber(record.defective_quantity)}</span></span> },
    { title: '实际出货', dataIndex: 'shipped_quantity', width: 105, render: (value) => <strong>{qualityNumber(value)}</strong> },
    { title: '累计退货', dataIndex: 'returned_quantity', width: 105, render: (value) => <span className={value ? 'quality-danger-text' : ''}>{qualityNumber(value)}</span> },
    { title: '返工次数', dataIndex: 'rework_count', width: 110, render: reworkCountTag },
    { title: '操作', key: 'action', fixed: 'right', width: 76, render: (_, record) => <Button type="link" icon={<EditOutlined />} onClick={() => setShipmentForm({ shipment: record })}>编辑</Button> },
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
    { title: '操作', key: 'action', fixed: 'right', width: 76, render: (_, record) => <Button type="link" icon={<EditOutlined />} onClick={() => setReworkForm({ rework: record })}>编辑</Button> },
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
    { title: '操作', key: 'action', fixed: 'right', width: 76, render: (_, row) => <Button type="link" icon={<EditOutlined />} onClick={() => setOrderForm({ order: row.order })}>编辑</Button> },
  ]

  const performanceColumns: TableColumnsType<QualityEmployeeStatistics> = [
    { title: '员工', key: 'employee', fixed: 'left', width: 145, render: (_, row) => <span><strong>{row.name}</strong><br /><Typography.Text type="secondary">{row.employee_no}{row.team ? ` · ${row.team}` : ''}</Typography.Text></span> },
    { title: '岗位', dataIndex: 'role', width: 115, render: (value: QualityEmployeeRole) => <Tag color={ROLE_META[value]?.color}>{ROLE_META[value]?.text || value}</Tag> },
    { title: '质检数量', dataIndex: 'inspection_quantity', width: 110, sorter: (a, b) => a.inspection_quantity - b.inspection_quantity, render: (value) => <strong>{qualityNumber(value)}</strong> },
    { title: '质检天数', dataIndex: 'inspection_days', width: 105, render: (value) => `${qualityNumber(value)} 天` },
    { title: '参与出货', dataIndex: 'shipment_count', width: 105, render: (value) => `${qualityNumber(value)} 批` },
    { title: '责任退货数量', dataIndex: 'responsible_return_quantity', width: 130, sorter: (a, b) => a.responsible_return_quantity - b.responsible_return_quantity, render: (value) => <strong className={value ? 'quality-danger-text' : ''}>{qualityNumber(value)}</strong> },
    { title: '返工处理数量', dataIndex: 'reworked_quantity', width: 130, sorter: (a, b) => a.reworked_quantity - b.reworked_quantity, render: (value) => <strong className="quality-rework-text">{qualityNumber(value)}</strong> },
    { title: '返工合格 / 报废', key: 'rework_result', width: 145, render: (_, row) => `${qualityNumber(row.recovered_quantity)} / ${qualityNumber(row.scrap_quantity)}` },
    { title: '一次合格率', dataIndex: 'first_pass_rate', width: 120, render: (value) => rateText(value) },
    { title: '责任退货率', dataIndex: 'return_rate', width: 120, render: (value) => <span className={Number(value || 0) > 0 ? 'quality-danger-text' : ''}>{rateText(value)}</span> },
    { title: '返工通过率', dataIndex: 'rework_pass_rate', width: 120, render: (value) => rateText(value) },
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
      key: 'daily',
      label: '每日出货',
      children: <div className="quality-tab-content">
        <DailyTrend rows={summary?.daily_trend || []} loading={summaryQuery.isLoading} />
        <div className="section-heading"><div><Typography.Title level={3}>每日出货台账</Typography.Title><Typography.Text type="secondary">逐批记录质检、合格、不良和实际出货数量。</Typography.Text></div><Button type="primary" icon={<PlusOutlined />} onClick={() => setShipmentForm({})}>新增出货</Button></div>
        {tableCard(shipments, shipmentColumns, shipmentsQuery.isLoading, 'id', 1430, '所选日期暂无出货记录')}
      </div>,
    },
    {
      key: 'reworks',
      label: '退货返工',
      children: <div className="quality-tab-content">
        <Alert className="quality-responsibility-alert" type="info" showIcon title="绩效口径分开统计" description="责任品检员记录退货责任；返工处理人记录实际返工工作量，两项不会混为同一指标。" />
        <div className="section-heading"><div><Typography.Title level={3}>退货返工记录</Typography.Title><Typography.Text type="secondary">记录每次退货原因、责任归属、返工处理与最终结果。</Typography.Text></div><Button type="primary" icon={<PlusOutlined />} onClick={() => setReworkForm({})}>登记退货返工</Button></div>
        {tableCard(reworks, reworkColumns, reworksQuery.isLoading, 'id', 1350, '所选日期暂无退货返工记录')}
      </div>,
    },
    {
      key: 'orders',
      label: '订单统计',
      children: <div className="quality-tab-content">
        <div className="section-heading"><div><Typography.Title level={3}>订单批次统计</Typography.Title><Typography.Text type="secondary">汇总每批订单的质检、出货、退货和返工表现。</Typography.Text></div><Button type="primary" icon={<PlusOutlined />} onClick={() => setOrderForm({})}>新增订单批次</Button></div>
        {tableCard(orderRows, orderColumns, ordersQuery.isLoading || summaryQuery.isLoading, (row) => row.order.id, 1510, '暂无订单批次')}
      </div>,
    },
    {
      key: 'performance',
      label: '员工绩效',
      children: <div className="quality-tab-content">
        <div className="section-heading"><div><Typography.Title level={3}>员工绩效依据</Typography.Title><Typography.Text type="secondary">默认按本月统计；可调整上方日期范围。责任退货量与返工处理量分列展示。</Typography.Text></div></div>
        {tableCard(performanceRows, performanceColumns, summaryQuery.isLoading, 'employee_id', 1420, '所选日期暂无员工绩效记录')}
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

  const anyError = summaryQuery.error || employeesQuery.error || ordersQuery.error || shipmentsQuery.error || reworksQuery.error

  return (
    <div className="page-container quality-page">
      <PageTitle
        title="品检出货与退货返工"
        description="记录每日质检与出货、每次退货返工、订单批次统计和员工绩效，为橡胶制品品质管理提供可追溯依据。"
        extra={<Space wrap><Button icon={<ToolOutlined />} onClick={() => setReworkForm({})}>登记退货返工</Button><Button type="primary" icon={<PlusOutlined />} onClick={() => setShipmentForm({})}>新增出货</Button></Space>}
      />

      <Card className="filter-card quality-filter-card">
        <div className="quality-filter-row">
          <RangePicker allowClear={false} value={range} onChange={(value) => value?.[0] && value?.[1] && setRange([value[0], value[1]])} />
          <Input allowClear prefix={<SearchOutlined />} placeholder="搜索出货单、订单、批次、产品或员工" value={query} onChange={(event) => setQuery(event.target.value)} />
          <Typography.Text type="secondary">统计区间：{dateFrom} 至 {dateTo}</Typography.Text>
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

      <QualityShipmentDrawer open={!!shipmentForm} shipment={shipmentForm?.shipment} orders={orders} employees={employees} onClose={() => setShipmentForm(undefined)} />
      <QualityReworkDrawer open={!!reworkForm} rework={reworkForm?.rework} shipments={shipmentOptions} employees={employees} onClose={() => setReworkForm(undefined)} />
      <QualityOrderDrawer open={!!orderForm} order={orderForm?.order} onClose={() => setOrderForm(undefined)} />
      <QualityEmployeeDrawer open={!!employeeForm} employee={employeeForm?.employee} onClose={() => setEmployeeForm(undefined)} />
    </div>
  )
}
