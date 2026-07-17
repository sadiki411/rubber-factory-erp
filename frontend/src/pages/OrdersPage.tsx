import { EditOutlined, FileExcelOutlined, PlusOutlined, SearchOutlined, WarningOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Empty, Grid, Input, List, Select, Space, Table, Tabs, Tag, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { materialReceiptApi, orderApi, toList } from '../api/client'
import { BusinessImportDrawer } from '../components/BusinessImportDrawer'
import { MaterialReceiptDrawer } from '../components/MaterialReceiptDrawer'
import { OrderFormDrawer } from '../components/OrderFormDrawer'
import { PageTitle } from '../components/PageTitle'
import type { MaterialReceipt, Order, OrderMaterialStatus, OrderProcessCardStatus, OrderStatus } from '../types'

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
  const [activeTab, setActiveTab] = useState<'orders' | 'receipts'>('orders')
  const [status, setStatus] = useState<OrderStatus | ''>('OPEN')
  const [productionRequired, setProductionRequired] = useState<'' | 'yes' | 'no'>('')
  const [materialStatus, setMaterialStatus] = useState<OrderMaterialStatus | ''>('')
  const [receiptQuery, setReceiptQuery] = useState('')
  const [receiptLink, setReceiptLink] = useState<'' | 'linked' | 'unlinked'>('')
  const [editing, setEditing] = useState<Order>()
  const [formOpen, setFormOpen] = useState(false)
  const [editingReceipt, setEditingReceipt] = useState<MaterialReceipt>()
  const [receiptFormOpen, setReceiptFormOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)

  const ordersQuery = useQuery({
    queryKey: ['orders', { query, status, productionRequired, materialStatus }],
    queryFn: async () => toList(await orderApi.list({
      q: query || undefined,
      status: status || undefined,
      production_required: productionRequired === '' ? undefined : productionRequired === 'yes',
      material_status: materialStatus || undefined,
      page_size: 1000,
    })),
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

  const columns: TableColumnsType<Order> = [
    { title: '订单号 / 项次', key: 'order', fixed: 'left', width: 205, render: (_, row) => <Button type="link" className="table-primary-link" onClick={() => openForm(row)}>{row.order_no}{row.item_no ? ` / ${row.item_no}` : ''}</Button> },
    { title: '产品 / 规格', key: 'product', fixed: 'left', width: 220, render: (_, row) => <span>{row.product_name || '-'}<br /><Typography.Text type="secondary">{row.product_code || '-'} · {row.specification || '-'}</Typography.Text></span> },
    { title: '材质', dataIndex: 'material', width: 125, render: (value) => value || '-' },
    { title: '交期', dataIndex: 'due_date', width: 110, render: (value) => value || '未登记' },
    { title: '数量', dataIndex: 'order_quantity', width: 95, render: (value) => exactOrderValue(value) },
    { title: '成型工时', dataIndex: 'forming_hours', width: 105, render: (value) => exactOrderValue(value, ' h') },
    { title: '模具', key: 'mold', width: 155, render: (_, row) => row.product_specification?.mold_no || row.mold_size || '-' },
    { title: '是否生产', dataIndex: 'production_required', width: 105, render: (value) => value === null || value === undefined ? <Tag>未登记</Tag> : <Tag color={value ? 'processing' : 'default'}>{value ? '需要生产' : '无需生产'}</Tag> },
    { title: '所需胶料', dataIndex: 'required_material_kg', width: 115, render: (value) => exactOrderValue(value, ' kg') },
    { title: '已发胶料', dataIndex: 'received_material_kg', width: 115, render: (value) => exactOrderValue(value, ' kg') },
    { title: '胶料差额', dataIndex: 'material_gap_kg', width: 110, render: (value) => exactOrderValue(value, ' kg') },
    { title: '胶料状态', dataIndex: 'material_status', width: 100, render: (value) => <MaterialStatusTag status={value} /> },
    { title: '流程卡张数', dataIndex: 'process_card_count', width: 110, render: (value) => exactOrderValue(value, ' 张') },
    { title: '覆盖订单数量', dataIndex: 'process_card_covered_quantity', width: 125, render: (value) => exactOrderValue(value) },
    { title: '流程卡状态', dataIndex: 'process_card_status', width: 140, render: (value) => <ProcessCardStatusTag status={value} /> },
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
    { title: '制造 / 发料日期', dataIndex: 'manufactured_on', width: 135, render: (value) => value || '未登记' },
    { title: '来源', key: 'source', width: 165, render: (_, row) => row.source_sheet ? `${row.source_sheet}${row.source_row ? ` · 第${row.source_row}行` : ''}` : '在线录入' },
    { title: '操作', key: 'action', fixed: 'right', width: 85, render: (_, row) => <Button type="link" icon={<EditOutlined />} onClick={() => openReceiptForm(row)}>{row.order_id || row.order ? '编辑' : '关联'}</Button> },
  ]

  return (
    <div className="page-container orders-page">
      <PageTitle
        title="订单管理"
        description="统一管理订单、胶料到料和流程卡状态；空值表示尚未登记，实际为零时会明确显示 0。"
        extra={<Space wrap><Button icon={<FileExcelOutlined />} onClick={() => setImportOpen(true)}>导入订单 / 发料单</Button>{activeTab === 'orders' ? <Button type="primary" icon={<PlusOutlined />} onClick={() => openForm()}>新增订单</Button> : <Button type="primary" icon={<PlusOutlined />} onClick={() => openReceiptForm()}>新增发料记录</Button>}</Space>}
      />
      <Tabs
        className="business-page-tabs"
        activeKey={activeTab}
        onChange={(key) => setActiveTab(key as 'orders' | 'receipts')}
        items={[
          { key: 'orders', label: '订单台账' },
          { key: 'receipts', label: <span>发料记录 {unlinkedReceiptCount > 0 && <Tag color="error">{unlinkedReceiptCount} 条待关联</Tag>}</span> },
        ]}
      />

      {activeTab === 'orders' ? (
        <>
          <Card className="filter-card">
            <div className="business-filter-row order-filter-row">
              <Input allowClear prefix={<SearchOutlined />} placeholder="搜索订单号、项次、产品、规格、材质或批次" value={query} onChange={(event) => setQuery(event.target.value)} />
              <Select value={status} onChange={setStatus} options={[{ value: '', label: '全部订单状态' }, { value: 'OPEN', label: '进行中' }, { value: 'COMPLETED', label: '已完成' }, { value: 'CANCELLED', label: '已取消' }]} />
              <Select value={productionRequired} onChange={setProductionRequired} options={[{ value: '', label: '全部生产安排' }, { value: 'yes', label: '需要生产' }, { value: 'no', label: '无需生产' }]} />
              <Select value={materialStatus} onChange={setMaterialStatus} options={[{ value: '', label: '全部胶料状态' }, ...Object.entries(MATERIAL_META).map(([value, meta]) => ({ value, label: meta.text }))]} />
            </div>
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
                    <Typography.Text>{record.product_name || '-'} · {record.specification || '-'}</Typography.Text>
                    <Typography.Text type="secondary">材质 {record.material || '-'} · 数量 {exactOrderValue(record.order_quantity)} · 交期 {record.due_date || '未登记'}</Typography.Text>
                    <div className="order-mobile-statuses"><span><small>胶料</small><MaterialStatusTag status={record.material_status} /></span><span><small>流程卡</small><ProcessCardStatusTag status={record.process_card_status} /></span></div>
                    <div className="business-mobile-grid">
                      <span><small>所需胶料</small><b>{exactOrderValue(record.required_material_kg, ' kg')}</b></span>
                      <span><small>已发胶料</small><b>{exactOrderValue(record.received_material_kg, ' kg')}</b></span>
                      <span><small>胶料差额</small><b>{exactOrderValue(record.material_gap_kg, ' kg')}</b></span>
                      <span><small>流程卡</small><b>{exactOrderValue(record.process_card_count, ' 张')} / 覆盖 {exactOrderValue(record.process_card_covered_quantity)}</b></span>
                    </div>
                    <Button block icon={<EditOutlined />} onClick={(event) => { event.stopPropagation(); openForm(record) }}>编辑订单</Button>
                  </Card>
                </List.Item>
              )}
            />
          ) : (
            <Card className="data-card" styles={{ body: { padding: 0 } }}>
              <Table rowKey="id" loading={ordersQuery.isLoading} dataSource={ordersQuery.data || []} columns={columns} scroll={{ x: 2050 }} pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (total) => `共 ${total} 条` }} />
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
                      <span><small>制造 / 发料日期</small><b>{record.manufactured_on || '未登记'}</b></span>
                      <span><small>来源</small><b>{record.source_sheet || '在线录入'}</b></span>
                    </div>
                    <Button block type={record.order_id || record.order ? 'default' : 'primary'} icon={<EditOutlined />} onClick={(event) => { event.stopPropagation(); openReceiptForm(record) }}>{record.order_id || record.order ? '编辑发料记录' : '关联到订单'}</Button>
                  </Card>
                </List.Item>
              )}
            />
          ) : (
            <Card className="data-card" styles={{ body: { padding: 0 } }}>
              <Table rowKey="id" loading={receiptsQuery.isLoading} dataSource={receipts} columns={receiptColumns} scroll={{ x: 1340 }} pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (total) => `共 ${total} 条` }} />
            </Card>
          )}
        </>
      )}

      <OrderFormDrawer open={formOpen} order={editing} onClose={() => setFormOpen(false)} />
      <MaterialReceiptDrawer open={receiptFormOpen} receipt={editingReceipt} orders={orderOptionsQuery.data || []} ordersLoading={orderOptionsQuery.isLoading} onClose={() => setReceiptFormOpen(false)} />
      <BusinessImportDrawer open={importOpen} context="orders" onClose={() => setImportOpen(false)} />
    </div>
  )
}
