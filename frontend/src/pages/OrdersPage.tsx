import { EditOutlined, FileExcelOutlined, HistoryOutlined, PlusOutlined, SearchOutlined, WarningOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Empty, Grid, Input, List, Select, Space, Table, Tabs, Tag, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'
import utc from 'dayjs/plugin/utc'
import { useState } from 'react'
import { materialReceiptApi, orderApi, toList } from '../api/client'
import { BusinessImportDrawer } from '../components/BusinessImportDrawer'
import { BusinessImportHistoryDrawer } from '../components/BusinessImportHistoryDrawer'
import { MaterialReceiptDrawer } from '../components/MaterialReceiptDrawer'
import { OrderFormDrawer } from '../components/OrderFormDrawer'
import { PageTitle } from '../components/PageTitle'
import type { MaterialReceipt, Order, OrderMaterialStatus, OrderProcessCardStatus, OrderStatus } from '../types'

type OrderTab = 'orders-open' | 'orders-completed' | 'orders-cancelled' | 'receipts'

const DEFAULT_ORDERING = 'due_date,process_card_status,order_date'
const SORT_PRESETS = [
  { value: DEFAULT_ORDERING, label: '交期：近到远' },
  { value: 'process_card_status,due_date,order_date', label: '流程卡：未齐优先' },
  { value: 'order_date,due_date,process_card_status', label: '下单日期：旧到新' },
  { value: '-order_date,due_date,process_card_status', label: '下单日期：新到旧' },
]
const SORT_LEVEL_OPTIONS = [
  { value: 'due_date', label: '交期：近到远' },
  { value: '-due_date', label: '交期：远到近' },
  { value: 'process_card_status', label: '流程卡：未收到优先' },
  { value: '-process_card_status', label: '流程卡：已收齐优先' },
  { value: 'order_date', label: '下单日期：旧到新' },
  { value: '-order_date', label: '下单日期：新到旧' },
  { value: 'order_no', label: '订单号：升序' },
  { value: '-order_no', label: '订单号：降序' },
]

dayjs.extend(utc)

const ORDER_STATUS_META: Record<OrderStatus, { text: string; color: string }> = {
  OPEN: { text: '进行中', color: 'processing' },
  COMPLETED: { text: '已完成', color: 'success' },
  CANCELLED: { text: '已取消', color: 'default' },
}

const MATERIAL_META: Record<OrderMaterialStatus, { text: string; color: string }> = {
  UNKNOWN: { text: '未核算', color: 'default' },
  NOT_RECEIVED: { text: '未收到', color: 'error' },
  PARTIAL: { text: '未发够', color: 'warning' },
  SUFFICIENT: { text: '已发够', color: 'success' },
  OVER: { text: '超额到料', color: 'blue' },
}

const PROCESS_CARD_META: Record<OrderProcessCardStatus, { text: string; color: string }> = {
  NOT_RECEIVED: { text: '未收到', color: 'error' },
  PARTIAL: { text: '未覆盖订单数量', color: 'warning' },
  RECEIVED: { text: '已收到', color: 'success' },
}

function exactOrderValue(value: unknown, suffix = '') {
  return value === null || value === undefined || value === '' ? '未登记' : `${String(value)}${suffix}`
}

function formattedTimestamp(value?: string | null) {
  if (!value) return '未登记'
  const parsed = dayjs(value).utcOffset(8)
  return parsed.isValid() ? parsed.format('YYYY-MM-DD HH:mm') : value
}

function productionRequiredText(value?: boolean | null) {
  if (value === null || value === undefined) return '未登记'
  return value ? '需要生产' : '无需生产'
}

function MaterialStatusTag({ status = 'UNKNOWN' }: { status?: OrderMaterialStatus }) {
  const meta = MATERIAL_META[status]
  return <Tag color={meta.color}>{meta.text}</Tag>
}

function ProcessCardStatusTag({ status = 'NOT_RECEIVED' }: { status?: OrderProcessCardStatus }) {
  const meta = PROCESS_CARD_META[status]
  return <Tag color={meta.color}>{meta.text}</Tag>
}

export function OrdersPage() {
  const screens = Grid.useBreakpoint()
  const mobile = screens.md === false
  const [query, setQuery] = useState('')
  const [activeTab, setActiveTab] = useState<OrderTab>('orders-open')
  const [productionRequired, setProductionRequired] = useState<'' | 'yes' | 'no'>('')
  const [materialStatus, setMaterialStatus] = useState<OrderMaterialStatus | ''>('')
  const [processCardStatus, setProcessCardStatus] = useState<OrderProcessCardStatus | ''>('')
  const [ordering, setOrdering] = useState(DEFAULT_ORDERING)
  const [receiptQuery, setReceiptQuery] = useState('')
  const [receiptLink, setReceiptLink] = useState<'' | 'linked' | 'unlinked'>('')
  const [editing, setEditing] = useState<Order>()
  const [formOpen, setFormOpen] = useState(false)
  const [editingReceipt, setEditingReceipt] = useState<MaterialReceipt>()
  const [receiptFormOpen, setReceiptFormOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [importHistoryOpen, setImportHistoryOpen] = useState(false)

  const orderStatus: OrderStatus = activeTab === 'orders-completed' ? 'COMPLETED' : activeTab === 'orders-cancelled' ? 'CANCELLED' : 'OPEN'
  const showingOrders = activeTab !== 'receipts'
  const ordersQuery = useQuery({
    queryKey: ['orders', { query, orderStatus, productionRequired, materialStatus, processCardStatus, ordering }],
    queryFn: async () => toList(await orderApi.list({
      q: query || undefined,
      status: orderStatus,
      production_required: productionRequired === '' ? undefined : productionRequired === 'yes',
      material_status: materialStatus || undefined,
      process_card_status: processCardStatus || undefined,
      ordering,
      page_size: 1000,
    })),
    enabled: showingOrders,
  })
  const orderOptionsQuery = useQuery({
    queryKey: ['orders', 'receipt-options'],
    queryFn: async () => toList(await orderApi.list({ page_size: 1000 })),
    enabled: activeTab === 'receipts' || receiptFormOpen,
  })
  const receiptsQuery = useQuery({
    queryKey: ['material-receipts', { receiptQuery, receiptLink }],
    queryFn: async () => toList(await materialReceiptApi.list({
      q: receiptQuery || undefined,
      linked: receiptLink === '' ? undefined : receiptLink === 'linked',
      page_size: 1000,
    })),
    enabled: activeTab === 'receipts',
  })
  const unlinkedReceiptsQuery = useQuery({
    queryKey: ['material-receipts', 'unlinked-count'],
    queryFn: async () => {
      const payload = await materialReceiptApi.list({ linked: false, page_size: 1 })
      return Array.isArray(payload) ? payload.length : (payload.count ?? payload.results.length)
    },
  })

  const openForm = (record?: Order) => {
    setEditing(record)
    setFormOpen(true)
  }
  const openReceiptForm = (record?: MaterialReceipt) => {
    setEditingReceipt(record)
    setReceiptFormOpen(true)
  }
  const receipts = receiptsQuery.data || []
  const unlinkedReceiptCount = unlinkedReceiptsQuery.data || 0
  const orderingLevels = ordering.split(',').filter(Boolean).slice(0, 3)

  const updateOrderingLevel = (index: number, value?: string) => {
    const next = [...orderingLevels]
    if (!value) next.splice(index, 1)
    else next[index] = value
    const deduplicated = next.filter((token, tokenIndex, values) => {
      const field = token.replace(/^-/, '')
      return values.findIndex((candidate) => candidate.replace(/^-/, '') === field) === tokenIndex
    })
    setOrdering(deduplicated.join(',') || DEFAULT_ORDERING)
  }

  const columns: TableColumnsType<Order> = [
    { title: '订单号 / 项次', key: 'order', fixed: 'left', width: 205, render: (_, row) => <Button type="link" className="table-primary-link" onClick={() => openForm(row)}>{row.order_no}{row.item_no ? ` / ${row.item_no}` : ''}</Button> },
    { title: '流程卡', key: 'process_card', width: 190, render: (_, row) => <span>{exactOrderValue(row.process_card_text)}{(row.process_card_count !== null && row.process_card_count !== undefined) || (row.process_card_covered_quantity !== null && row.process_card_covered_quantity !== undefined) ? <><br /><Typography.Text type="secondary">{exactOrderValue(row.process_card_count, ' 张')} · 覆盖 {exactOrderValue(row.process_card_covered_quantity)}</Typography.Text></> : null}<br /><ProcessCardStatusTag status={row.process_card_status} /></span> },
    { title: '规格 / 产品', key: 'specification', width: 220, render: (_, row) => <span>{row.specification || '-'}<br /><Typography.Text type="secondary">{row.product_name || row.product_code || '-'}</Typography.Text></span> },
    { title: '材质', dataIndex: 'material', width: 125, render: (value) => value || '-' },
    {
      title: '交期',
      dataIndex: 'due_date',
      width: 110,
      sorter: true,
      sortDirections: ['ascend', 'descend', 'ascend'],
      sortOrder: orderingLevels[0] === 'due_date' ? 'ascend' : orderingLevels[0] === '-due_date' ? 'descend' : null,
      render: (value) => value || <Tag color="warning">无交期</Tag>,
    },
    { title: '订单量', dataIndex: 'order_quantity', width: 95, render: (value) => exactOrderValue(value) },
    { title: '胶料用量', dataIndex: 'required_material_kg', width: 115, render: (value) => exactOrderValue(value, ' kg') },
    { title: '已发胶料', key: 'received_material', width: 130, render: (_, row) => <span>{exactOrderValue(row.received_material_kg, ' kg')}<br /><Typography.Text type="secondary">差额 {exactOrderValue(row.material_gap_kg, ' kg')}</Typography.Text></span> },
    { title: '成型工时', dataIndex: 'forming_hours', width: 105, render: (value) => exactOrderValue(value, ' h') },
    {
      title: '下单日期',
      dataIndex: 'order_date',
      width: 110,
      sorter: true,
      sortDirections: ['ascend', 'descend', 'ascend'],
      sortOrder: orderingLevels[0] === 'order_date' ? 'ascend' : orderingLevels[0] === '-order_date' ? 'descend' : null,
      render: (value) => value || '未登记',
    },
    { title: '模具型号 / 尺寸', key: 'mold', width: 165, render: (_, row) => <span>{row.product_specification?.mold_model?.code || row.product_specification?.mold_no || '-'}<br /><Typography.Text type="secondary">{row.mold_size || row.product_specification?.mold_size || '-'}</Typography.Text></span> },
    { title: '是否生产', dataIndex: 'production_required', width: 105, render: (value) => <Tag color={value === true ? 'processing' : 'default'}>{productionRequiredText(value)}</Tag> },
    { title: '生产进度', key: 'production_progress', width: 155, render: (_, row) => <span>{exactOrderValue(row.produced_quantity)} / {exactOrderValue(row.order_quantity)}<br />{row.production_target_reached ? <Tag color="success">生产已达标</Tag> : <Typography.Text type="secondary">欠 {exactOrderValue(row.production_remaining_quantity)} 件</Typography.Text>}</span> },
    { title: '出货日期', dataIndex: 'shipment_date', width: 120, render: (value) => exactOrderValue(value) },
    { title: '净有效出货', key: 'weighted_shipped_quantity', width: 135, render: (_, row) => <span>{exactOrderValue(row.weighted_shipped_quantity)} / {exactOrderValue(row.order_quantity)}<br /><Typography.Text type="secondary">待出 {exactOrderValue(row.weighted_remaining_quantity)}</Typography.Text></span> },
    { title: '最后更新', key: 'last_updated', width: 155, render: (_, row) => formattedTimestamp(row.last_data_updated_at || row.updated_at) },
    { title: '胶料状态', dataIndex: 'material_status', width: 100, render: (value) => <MaterialStatusTag status={value} /> },
    { title: '订单状态', dataIndex: 'status', width: 100, render: (value: OrderStatus, row) => <Tag color={ORDER_STATUS_META[value]?.color}>{row.status_display || ORDER_STATUS_META[value]?.text || value}</Tag> },
    { title: '操作', key: 'action', fixed: 'right', width: 80, render: (_, row) => <Button type="link" icon={<EditOutlined />} onClick={() => openForm(row)}>编辑</Button> },
  ]
  const receiptColumns: TableColumnsType<MaterialReceipt> = [
    { title: '关联状态', key: 'linked', fixed: 'left', width: 115, render: (_, row) => row.order_id || row.order ? <Tag color="success">已关联订单</Tag> : <Tag color="error" icon={<WarningOutlined />}>待关联</Tag> },
    { title: '订单号 / 项次', key: 'order', fixed: 'left', width: 205, render: (_, row) => <Button type="link" className="table-primary-link" onClick={() => openReceiptForm(row)}>{row.order?.order_no || row.order_no || '未填写'}{row.order?.item_no || row.item_no ? ` / ${row.order?.item_no || row.item_no}` : ''}</Button> },
    { title: '成品 / 规格', key: 'product', width: 210, render: (_, row) => <span>{row.finished_product_name || '-'}<br /><Typography.Text type="secondary">{row.specification || '-'}</Typography.Text></span> },
    { title: '材质 / 批次', key: 'material', width: 170, render: (_, row) => <span>{row.material || '-'}<br /><Typography.Text type="secondary">{row.batch_no || '-'}</Typography.Text></span> },
    { title: '片材尺寸', dataIndex: 'sheet_size', width: 135, render: (value) => value || '-' },
    { title: '发料重量', dataIndex: 'weight_kg', width: 120, render: (value) => exactOrderValue(value, ' kg') },
    { title: '发料日期', dataIndex: 'issued_on', width: 120, render: (value) => value || '未登记' },
    { title: '制造日期', dataIndex: 'manufactured_on', width: 135, render: (value) => value || '未登记' },
    { title: '来源', key: 'source', width: 165, render: (_, row) => row.source_sheet ? `${row.source_sheet}${row.source_row ? ` · 第${row.source_row}行` : ''}` : '在线录入' },
    { title: '最后更新', dataIndex: 'updated_at', width: 155, render: (value) => formattedTimestamp(value) },
    { title: '操作', key: 'action', fixed: 'right', width: 85, render: (_, row) => <Button type="link" icon={<EditOutlined />} onClick={() => openReceiptForm(row)}>{row.order_id || row.order ? '编辑' : '关联'}</Button> },
  ]

  return (
    <div className="page-container orders-page">
      <PageTitle
        title="订单管理"
        description="统一管理订单、胶料到料和流程卡状态；空值表示尚未登记，实际为零时会明确显示 0。"
        extra={<Space wrap><Button icon={<HistoryOutlined />} onClick={() => setImportHistoryOpen(true)}>导入记录</Button><Button icon={<FileExcelOutlined />} onClick={() => setImportOpen(true)}>导入订单 / 发料单</Button>{showingOrders ? <Button type="primary" icon={<PlusOutlined />} onClick={() => openForm()}>新增订单</Button> : <Button type="primary" icon={<PlusOutlined />} onClick={() => openReceiptForm()}>新增发料记录</Button>}</Space>}
      />
      <Tabs
        className="business-page-tabs"
        activeKey={activeTab}
        onChange={(key) => setActiveTab(key as OrderTab)}
        items={[
          { key: 'orders-open', label: '进行中订单' },
          { key: 'orders-completed', label: '已完成订单' },
          { key: 'orders-cancelled', label: '已取消订单' },
          { key: 'receipts', label: <span>发料记录 {unlinkedReceiptCount > 0 && <Tag color="error">{unlinkedReceiptCount} 条待关联</Tag>}</span> },
        ]}
      />

      {showingOrders ? (
        <>
          <Card className="filter-card">
            <div className="business-filter-row order-filter-row">
              <Input allowClear prefix={<SearchOutlined />} placeholder="搜索订单号、项次、产品、规格、材质或批次" value={query} onChange={(event) => setQuery(event.target.value)} />
              <Select value={productionRequired} onChange={setProductionRequired} options={[{ value: '', label: '全部生产安排' }, { value: 'yes', label: '需要生产' }, { value: 'no', label: '无需生产' }]} />
              <Select value={materialStatus} onChange={setMaterialStatus} options={[{ value: '', label: '全部胶料状态' }, ...Object.entries(MATERIAL_META).map(([value, meta]) => ({ value, label: meta.text }))]} />
              <Select value={processCardStatus} onChange={setProcessCardStatus} options={[{ value: '', label: '全部流程卡状态' }, ...Object.entries(PROCESS_CARD_META).map(([value, meta]) => ({ value, label: meta.text }))]} />
              <Select<string>
                aria-label="订单排序"
                value={SORT_PRESETS.some((item) => item.value === ordering) ? ordering : undefined}
                placeholder="自定义排序"
                onChange={setOrdering}
                options={SORT_PRESETS}
              />
            </div>
            <details className="order-sort-details">
              <summary>自定义三级排序（后面的条件只在前面相同时生效）</summary>
              <div className="order-sort-levels">
                {[0, 1, 2].map((index) => (
                  <Select<string>
                    key={index}
                    allowClear={index > 0}
                    aria-label={`第${index + 1}级排序`}
                    placeholder={`第${index + 1}级排序`}
                    value={orderingLevels[index]}
                    onChange={(value) => updateOrderingLevel(index, value)}
                    options={SORT_LEVEL_OPTIONS.map((option) => ({
                      ...option,
                      disabled: orderingLevels.some((token, otherIndex) => otherIndex !== index && token.replace(/^-/, '') === option.value.replace(/^-/, '')),
                    }))}
                  />
                ))}
              </div>
            </details>
          </Card>
          {ordersQuery.isError && <Alert className="business-page-alert" type="error" showIcon title="订单读取失败" description={(ordersQuery.error as Error).message} />}
          {mobile ? (
            <List
              className="mobile-record-list business-mobile-list"
              loading={ordersQuery.isLoading}
              dataSource={ordersQuery.data || []}
              locale={{ emptyText: <Empty description="暂无订单" /> }}
              renderItem={(record) => (
                <List.Item>
                  <Card className="mobile-record-card business-mobile-card order-mobile-card" role="button" tabIndex={0} onClick={() => openForm(record)}>
                    <div className="record-card-heading"><Typography.Title level={4}>{record.order_no}{record.item_no ? ` / ${record.item_no}` : ''}</Typography.Title><Tag color={ORDER_STATUS_META[record.status]?.color}>{record.status_display || ORDER_STATUS_META[record.status]?.text}</Tag></div>
                    <Typography.Text>{record.specification || '-'} · {record.product_name || record.product_code || '-'}</Typography.Text>
                    <Typography.Text type="secondary">材质 {record.material || '-'} · 交期 {record.due_date || '未登记'}</Typography.Text>
                    <div className="order-mobile-statuses"><span><small>胶料</small><MaterialStatusTag status={record.material_status} /></span><span><small>流程卡</small><ProcessCardStatusTag status={record.process_card_status} /></span></div>
                    <div className="business-mobile-grid">
                      <span><small>流程卡</small><b>{exactOrderValue(record.process_card_text)}</b></span>
                      <span><small>规格</small><b>{record.specification || '-'}</b></span>
                      <span><small>胶料</small><b>{record.material || '-'}</b></span>
                      <span><small>交期</small><b>{record.due_date || '未登记'}</b></span>
                      <span><small>订单量</small><b>{exactOrderValue(record.order_quantity)}</b></span>
                      <span><small>胶料用量</small><b>{exactOrderValue(record.required_material_kg, ' kg')}</b></span>
                      <span><small>已发胶料</small><b>{exactOrderValue(record.received_material_kg, ' kg')}</b></span>
                      <span><small>胶料差额</small><b>{exactOrderValue(record.material_gap_kg, ' kg')}</b></span>
                      <span><small>成型工时</small><b>{exactOrderValue(record.forming_hours, ' h')}</b></span>
                      <span><small>下单日期</small><b>{record.order_date || '未登记'}</b></span>
                      <span><small>模具型号</small><b>{record.product_specification?.mold_model?.code || record.product_specification?.mold_no || '-'}</b></span>
                      <span><small>模具尺寸</small><b>{record.mold_size || record.product_specification?.mold_size || '-'}</b></span>
                      <span><small>是否生产</small><b>{productionRequiredText(record.production_required)}</b></span>
                      <span><small>生产进度</small><b>{exactOrderValue(record.produced_quantity)} / {exactOrderValue(record.order_quantity)}{record.production_target_reached ? ' · 已达标' : ` · 欠${exactOrderValue(record.production_remaining_quantity)}`}</b></span>
                      <span><small>出货日期</small><b>{exactOrderValue(record.shipment_date)}</b></span>
                      <span><small>净有效出货</small><b>{exactOrderValue(record.weighted_shipped_quantity)} / {exactOrderValue(record.order_quantity)} · 待出{exactOrderValue(record.weighted_remaining_quantity)}</b></span>
                      <span><small>最后更新</small><b>{formattedTimestamp(record.last_data_updated_at || record.updated_at)}</b></span>
                      {((record.process_card_count !== null && record.process_card_count !== undefined) || (record.process_card_covered_quantity !== null && record.process_card_covered_quantity !== undefined)) && <span><small>流程卡明细</small><b>{exactOrderValue(record.process_card_count, ' 张')} / 覆盖 {exactOrderValue(record.process_card_covered_quantity)}</b></span>}
                    </div>
                    <Button block icon={<EditOutlined />} onClick={(event) => { event.stopPropagation(); openForm(record) }}>编辑订单</Button>
                  </Card>
                </List.Item>
              )}
            />
          ) : (
            <Card className="data-card" styles={{ body: { padding: 0 } }}>
              <Table
                rowKey="id"
                loading={ordersQuery.isLoading}
                dataSource={ordersQuery.data || []}
                columns={columns}
                scroll={{ x: 2680 }}
                pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (total) => `共 ${total} 条` }}
                onChange={(_, __, sorter) => {
                  if (Array.isArray(sorter) || !sorter.field || !sorter.order) return
                  if (sorter.field === 'order_date') setOrdering(`${sorter.order === 'ascend' ? 'order_date' : '-order_date'},due_date,process_card_status`)
                  if (sorter.field === 'due_date') setOrdering(`${sorter.order === 'ascend' ? 'due_date' : '-due_date'},process_card_status,order_date`)
                }}
              />
            </Card>
          )}
        </>
      ) : (
        <>
          <Card className="filter-card">
            <div className="business-filter-row receipt-filter-row">
              <Input allowClear prefix={<SearchOutlined />} placeholder="搜索发料单订单号、项次、成品、规格、材质或批次" value={receiptQuery} onChange={(event) => setReceiptQuery(event.target.value)} />
              <Select value={receiptLink} onChange={setReceiptLink} options={[{ value: '', label: '全部关联状态' }, { value: 'unlinked', label: '仅看待关联' }, { value: 'linked', label: '仅看已关联' }]} />
            </div>
          </Card>
          {unlinkedReceiptCount > 0 && <Alert className="business-page-alert" type="warning" showIcon title={`有 ${unlinkedReceiptCount} 条发料记录尚未关联具体订单`} description="这些重量暂未计入订单“已发胶料”。选择“仅看待关联”，再点击记录并关联订单明细，保存后会立即重新汇总。" />}
          {receiptsQuery.isError && <Alert className="business-page-alert" type="error" showIcon title="发料记录读取失败" description={(receiptsQuery.error as Error).message} />}
          {mobile ? (
            <List
              className="mobile-record-list business-mobile-list"
              loading={receiptsQuery.isLoading}
              dataSource={receipts}
              locale={{ emptyText: <Empty description="暂无发料记录" /> }}
              renderItem={(record) => (
                <List.Item>
                  <Card
                    className="mobile-record-card business-mobile-card receipt-mobile-card"
                    role="button"
                    tabIndex={0}
                    onClick={() => openReceiptForm(record)}
                    onKeyDown={(event) => {
                      if (event.key !== 'Enter' && event.key !== ' ') return
                      event.preventDefault()
                      openReceiptForm(record)
                    }}
                  >
                    <div className="record-card-heading"><Typography.Title level={4}>{record.order?.order_no || record.order_no || '未填写订单号'}{record.order?.item_no || record.item_no ? ` / ${record.order?.item_no || record.item_no}` : ''}</Typography.Title>{record.order_id || record.order ? <Tag color="success">已关联</Tag> : <Tag color="error" icon={<WarningOutlined />}>待关联</Tag>}</div>
                    <Typography.Text>{record.finished_product_name || '-'} · {record.specification || '-'}</Typography.Text>
                    <Typography.Text type="secondary">材质 {record.material || '-'} · 批次 {record.batch_no || '-'}</Typography.Text>
                    <div className="business-mobile-grid">
                      <span><small>发料重量</small><b>{exactOrderValue(record.weight_kg, ' kg')}</b></span>
                      <span><small>片材尺寸</small><b>{record.sheet_size || '-'}</b></span>
                      <span><small>发料日期</small><b>{record.issued_on || '未登记'}</b></span>
                      <span><small>制造日期</small><b>{record.manufactured_on || '未登记'}</b></span>
                      <span><small>来源</small><b>{record.source_sheet || '在线录入'}</b></span>
                      <span><small>最后更新</small><b>{formattedTimestamp(record.updated_at)}</b></span>
                    </div>
                    <Button block type={record.order_id || record.order ? 'default' : 'primary'} icon={<EditOutlined />} onClick={(event) => { event.stopPropagation(); openReceiptForm(record) }}>{record.order_id || record.order ? '编辑发料记录' : '关联到订单'}</Button>
                  </Card>
                </List.Item>
              )}
            />
          ) : (
            <Card className="data-card" styles={{ body: { padding: 0 } }}>
              <Table rowKey="id" loading={receiptsQuery.isLoading} dataSource={receipts} columns={receiptColumns} scroll={{ x: 1615 }} pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (total) => `共 ${total} 条` }} />
            </Card>
          )}
        </>
      )}

      <OrderFormDrawer open={formOpen} order={editing} onClose={() => setFormOpen(false)} />
      <MaterialReceiptDrawer open={receiptFormOpen} receipt={editingReceipt} orders={orderOptionsQuery.data || []} ordersLoading={orderOptionsQuery.isLoading} onClose={() => setReceiptFormOpen(false)} />
      <BusinessImportDrawer open={importOpen} context="orders" onClose={() => setImportOpen(false)} />
      <BusinessImportHistoryDrawer open={importHistoryOpen} onClose={() => setImportHistoryOpen(false)} />
    </div>
  )
}
