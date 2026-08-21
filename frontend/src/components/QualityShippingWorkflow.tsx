import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  DatePicker,
  Descriptions,
  Divider,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Timeline,
  Typography,
} from 'antd'
import type { TableColumnsType, TableProps } from 'antd'
import { EditOutlined, PlusOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { useEffect, useMemo, useState } from 'react'
import {
  expectedWeightKg,
  formatQualityDate,
  orderUnitWeightG,
  qualityNumber,
  resolvedProcessCardReworkCount,
} from '../quality'
import type {
  QualityEmployee,
  QualityProcessCard,
  QualityOrder,
  QualityShipment,
  QualityShipmentBatch,
  QualityShipmentBatchInput,
  QualityReworkCase,
  ReturnRework,
} from '../types'
import { QualityWeightShipmentDrawer, type QualityWeightShipmentLineSeed } from './QualityWeightShipmentDrawer'

export interface WorkflowCard {
  key: string
  cardNo: string
  order: QualityOrder
  processCard?: QualityProcessCard
  quantity: number
  shippedQuantity: number
  remainingQuantity: number
  unitWeightG: number | null
  expectedWeightKg: number | null
  shippedWeightKg: number
  maxAllowedWeightKg: number | null
  dueDate: string | null
  reworkCount: number
  missingDate: boolean
  overdue: boolean
  status: 'READY' | 'PARTIAL' | 'REWORK' | 'SHIPPED'
}

interface WorkflowProps {
  orders: QualityOrder[]
  employees?: QualityEmployee[]
  processCards?: QualityProcessCard[]
  shipments: QualityShipment[]
  batches?: QualityShipmentBatch[]
  reworks: ReturnRework[]
  reworkCases?: QualityReworkCase[]
  searchText?: string
  loading?: boolean
  onOpenRework: (rework?: ReturnRework) => void
  onOpenTimeline: (card: WorkflowCard) => void
  onSubmitBatch: (payload: QualityShipmentBatchInput) => Promise<void>
  onOpenShipment?: () => void
  onSaveProcessCard?: (body: Record<string, unknown>, card?: QualityProcessCard) => Promise<void>
}

function parseWeightFromNotes(notes?: string) {
  const match = String(notes || '').match(/(?:净重|实际净重|weight)\s*[=:：]\s*([\d.]+)/i)
  const parsed = match ? Number(match[1]) : NaN
  return Number.isFinite(parsed) ? parsed : 0
}

function cardForOrder(order: QualityOrder, shipments: QualityShipment[], reworks: ReturnRework[]): WorkflowCard {
  const orderShipments = shipments.filter((item) => item.order_id === order.id || item.order?.id === order.id)
  const shippedQuantity = orderShipments.reduce((sum, item) => sum + Number(item.shipped_quantity || 0), 0)
  const quantity = Math.max(0, Number(order.process_card_covered_quantity || order.order_quantity || 0))
  const remainingQuantity = Math.max(0, quantity - shippedQuantity)
  const unitWeightG = orderUnitWeightG(order)
  const dueDate = order.due_date || order.order_date || null
  const reworkCount = orderShipments.reduce((sum, item) => sum + Number(item.rework_count || 0), 0)
    + reworks.filter((item) => item.shipment?.order_id === order.id || item.shipment?.order?.id === order.id).length
  const today = dayjs().startOf('day')
  const due = dueDate ? dayjs(dueDate).startOf('day') : null
  const overdue = Boolean(remainingQuantity && due?.isValid() && due.isBefore(today))
  const missingDate = Boolean(shippedQuantity && !String(order.shipment_date || '').trim())
  const status = remainingQuantity <= 0 ? 'SHIPPED' : reworkCount ? 'REWORK' : shippedQuantity ? 'PARTIAL' : 'READY'
  return {
    key: `${order.id}`,
    cardNo: order.process_card_text?.split(/[\n,，;]/)[0]?.trim() || `PC-${order.order_no}-${order.item_no || order.id}`,
    order,
    processCard: undefined,
    quantity,
    shippedQuantity,
    remainingQuantity,
    unitWeightG,
    expectedWeightKg: expectedWeightKg(remainingQuantity, unitWeightG),
    shippedWeightKg: orderShipments.reduce((sum, item) => sum + parseWeightFromNotes(item.notes), 0),
    maxAllowedWeightKg: null,
    dueDate,
    reworkCount,
    missingDate,
    overdue,
    status,
  }
}

function statusTag(card: WorkflowCard) {
  if (card.overdue) return <Tag color="error">逾期待出货</Tag>
  if (card.missingDate) return <Tag color="warning">待补出货日期</Tag>
  const labels = { READY: '待出货', PARTIAL: '部分出货', REWORK: '返工中', SHIPPED: '已完成' } as const
  const colors = { READY: 'blue', PARTIAL: 'processing', REWORK: 'warning', SHIPPED: 'success' } as const
  return <Tag color={colors[card.status]}>{labels[card.status]}</Tag>
}

function BatchShipmentDrawer({
  open,
  cards,
  orders,
  employees,
  shipments,
  batches,
  onClose,
  onSubmit,
}: {
  open: boolean
  cards: WorkflowCard[]
  orders: QualityOrder[]
  employees: QualityEmployee[]
  shipments: QualityShipment[]
  batches: QualityShipmentBatch[]
  onClose: () => void
  onSubmit: (payload: QualityShipmentBatchInput) => Promise<void>
}) {
  const lineSeeds: QualityWeightShipmentLineSeed[] = cards.map((card) => ({
    key: card.key,
    process_card_id: card.processCard?.id ?? card.key,
    card_no: card.cardNo,
    order_id: card.order.id,
    order: card.order,
    quantity: card.remainingQuantity,
    remaining_quantity: card.remainingQuantity,
    unit_weight_g: card.unitWeightG,
    net_weight_kg: card.expectedWeightKg,
  }))
  return <QualityWeightShipmentDrawer
    open={open}
    orders={orders}
    employees={employees}
    lines={lineSeeds}
    existingShipments={shipments}
    existingBatches={batches}
    onClose={onClose}
    onSubmit={onSubmit}
  />
}

function ReworkTimelineDrawer({
  open,
  card,
  reworks,
  reworkCases,
  shipments,
  onClose,
  onAdd,
}: {
  open: boolean
  card?: WorkflowCard
  reworks: ReturnRework[]
  reworkCases: QualityReworkCase[]
  shipments: QualityShipment[]
  onClose: () => void
  onAdd: (shipment?: QualityShipment) => void
}) {
  const rows = useMemo(() => {
    if (!card) return []
    return reworks.filter((item) => item.shipment?.order_id === card.order.id || item.shipment?.order?.id === card.order.id).sort((a, b) => String(a.rework_date).localeCompare(String(b.rework_date)) || a.id - b.id)
  }, [card, reworks])
  const linkedShipment = shipments.find((item) => item.order_id === card?.order.id || item.order?.id === card?.order.id)
  const weightedRows = useMemo(() => {
    if (!card) return []
    return reworkCases.filter((item) => item.process_card_id != null && String(item.process_card_id) === String(card.processCard?.id)
      || item.source?.order_ids?.includes(card.order.id))
  }, [card, reworkCases])
  return (
    <Drawer open={open} onClose={onClose} width={560} title={card ? `${card.cardNo} · 返工时间线` : '返工时间线'} footer={<Space className="drawer-footer-actions"><Button onClick={onClose}>关闭</Button><Button type="primary" onClick={() => onAdd(linkedShipment)}>登记返工</Button></Space>}>
      {card && <Descriptions column={1} size="small" bordered><Descriptions.Item label="订单 / 产品">{card.order.order_no} · {card.order.product_name || card.order.specification}</Descriptions.Item><Descriptions.Item label="计划数量">{qualityNumber(card.quantity)} 件</Descriptions.Item><Descriptions.Item label="累计返工">{card.reworkCount} 次</Descriptions.Item></Descriptions>}
      <Divider orientation="horizontal">处理记录</Divider>
      {(rows.length || weightedRows.length) ? <Timeline items={[
        ...weightedRows.map((item) => ({
          color: item.status === 'COMPLETED' ? 'green' : item.status === 'SCRAPPED' ? 'red' : 'blue',
          label: <span>{item.case_no}<br />{formatQualityDate(item.opened_on)}</span>,
          children: <Card size="small" className="quality-rework-timeline-card"><Space wrap><Tag color="blue">客户整批退货</Tag><Tag>{item.attempt_count || item.attempts?.length || 0}轮返工</Tag></Space><div className="quality-rework-timeline-quantities">{item.source ? `第${item.source.shipment_unit_no}批 · ${qualityNumber(item.source.pieces_per_batch)}件 · ${qualityNumber(item.source.single_batch_net_weight_kg, 3)}kg` : `${qualityNumber(item.affected_quantity)}件`}</div><Typography.Paragraph ellipsis={{ rows: 2 }} type="secondary">{item.reason || item.notes || '未填写原因'}</Typography.Paragraph></Card>,
        })),
        ...rows.map((item, index) => ({
         color: item.status === 'COMPLETED' ? 'green' : item.status === 'PROCESSING' ? 'blue' : 'orange',
         label: <span>第{index + 1}次<br />{formatQualityDate(item.rework_date)}</span>,
          children: <Card size="small" className="quality-rework-timeline-card"><Space wrap><Tag>{item.reason_category_display || item.reason_category}</Tag><Tag color={item.status === 'COMPLETED' ? 'success' : 'warning'}>{item.status_display || item.status}</Tag><Tag>历史只读</Tag></Space><div className="quality-rework-timeline-quantities">退回 {qualityNumber(item.returned_quantity)} 件 · 返工 {qualityNumber(item.reworked_quantity)} 件 · 合格 {qualityNumber(item.recovered_quantity)} 件 · 报废 {qualityNumber(item.scrap_quantity)} 件</div><Typography.Paragraph ellipsis={{ rows: 2 }} type="secondary">{item.reason || item.notes || '未填写原因'}</Typography.Paragraph></Card>,
        })),
      ]} /> : <Empty description="暂无返工记录，可从下方登记第1次返工" />}
    </Drawer>
  )
}

function ProcessCardDrawer({ open, card, orders, onClose, onSave }: { open: boolean; card?: QualityProcessCard; orders: QualityOrder[]; onClose: () => void; onSave?: (body: Record<string, unknown>, card?: QualityProcessCard) => Promise<void> }) {
  const [form] = Form.useForm<Record<string, unknown>>()
  const [saving, setSaving] = useState(false)
  useEffect(() => {
    if (!open) return
    form.resetFields()
    const legacyMaterialWeightKg = card?.material_weight_g == null
      ? undefined
      : Number(card.material_weight_g) / 1000
    form.setFieldsValue({
      ...card,
      demand_date: (card?.demand_date || card?.due_date) ? dayjs(card.demand_date || card.due_date) : undefined,
      material_issue_weight_kg: card?.material_issue_weight_kg ?? legacyMaterialWeightKg ?? card?.raw_data?.material_issue_weight_kg,
      qr_text: card?.qr_text || card?.raw_data?.qr_text,
      qr_image_url: card?.raw_data?.qr_image_url,
    })
  }, [card, form, open])
  const submit = async () => {
    const values = await form.validateFields()
    if (values.demand_date && dayjs.isDayjs(values.demand_date)) values.demand_date = values.demand_date.format('YYYY-MM-DD')
    const materialWeight = values.material_issue_weight_kg
    const qrText = values.qr_text
    const qrImageUrl = values.qr_image_url
    delete values.material_issue_weight_kg
    delete values.qr_text
    delete values.qr_image_url
    values.material_issue_weight_kg = materialWeight ?? null
    values.qr_text = qrText || ''
    values.raw_data = { ...(card?.raw_data || {}), ...(materialWeight != null ? { material_issue_weight_kg: materialWeight } : {}), ...(qrText ? { qr_text: qrText } : {}), ...(qrImageUrl ? { qr_image_url: qrImageUrl } : {}) }
    if (!onSave) return
    setSaving(true)
    try { await onSave(values, card); onClose() } finally { setSaving(false) }
  }
  return <Drawer open={open} onClose={onClose} width={560} title={card ? '编辑流程卡' : '新增流程卡'} footer={<Space className="drawer-footer-actions"><Button onClick={onClose}>取消</Button><Button type="primary" loading={saving} onClick={() => void submit()}>保存</Button></Space>}>
    <Form form={form} layout="vertical">
      <Form.Item name="card_no" label="流程卡号" rules={[{ required: true, message: '请输入流程卡号' }]}><Input /></Form.Item>
      <Form.Item name="order_id" label="订单" rules={[{ required: true, message: '请选择订单' }]}><Select showSearch optionFilterProp="label" options={orders.map((order) => ({ value: order.id, label: `${order.order_no} · ${order.product_name || order.specification}` }))} /></Form.Item>
      <Row gutter={10}><Col xs={24} sm={12}><Form.Item name="source_item_no" label="订单项次"><Input /></Form.Item></Col><Col xs={24} sm={12}><Form.Item name="quantity" label="卡上数量" rules={[{ required: true, type: 'number', min: 1 }]}><InputNumber min={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col></Row>
      <Row gutter={10}><Col xs={24} sm={12}><Form.Item name="unit_weight_g" label="成品单重(g)" extra="可先留空；补录单重后才可出货"><InputNumber min={0.00001} precision={5} style={{ width: '100%' }} /></Form.Item></Col><Col xs={24} sm={12}><Form.Item name="material_issue_weight_kg" label="胶料重量(kg)" extra="仅记录发料重量，不参与成品重量"><InputNumber min={0} precision={3} style={{ width: '100%' }} /></Form.Item></Col></Row>
      <Form.Item name="demand_date" label="需求日期"><DatePicker style={{ width: '100%' }} /></Form.Item>
      <Form.Item name="qr_text" label="二维码文本（可选）"><Input maxLength={500} /></Form.Item>
      <Form.Item name="qr_image_url" label="二维码图片地址（可选）"><Input type="url" placeholder="https://…" /></Form.Item>
      <Form.Item name="notes" label="备注"><Input.TextArea rows={3} /></Form.Item>
    </Form>
  </Drawer>
}

export function QualityShippingWorkflow({ orders, employees = [], processCards = [], shipments, batches = [], reworks, reworkCases = [], searchText = '', loading, onOpenRework, onOpenTimeline, onSubmitBatch, onOpenShipment, onSaveProcessCard }: WorkflowProps) {
  const [selectedKeys, setSelectedKeys] = useState<React.Key[]>([])
  const [basketOpen, setBasketOpen] = useState(false)
  const [onlyAlerts, setOnlyAlerts] = useState(false)
  const [timelineCard, setTimelineCard] = useState<WorkflowCard>()
  const [processCardForm, setProcessCardForm] = useState<QualityProcessCard | null | undefined>(undefined)
  const cards = useMemo(() => {
    if (!processCards.length) {
      const keyword = searchText.trim().toLowerCase()
      const fallbackOrders = keyword
        ? orders.filter((order) => [order.order_no, order.item_no, order.batch_no, order.product_code, order.product_name, order.specification, order.material]
          .some((value) => String(value || '').toLowerCase().includes(keyword)))
        : orders
      return fallbackOrders.map((order) => cardForOrder(order, shipments, reworks)).filter((card) => card.quantity > 0)
    }
    return processCards.map((item) => {
      const order = item.order || orders.find((candidate) => candidate.id === item.order_id)
      if (!order) return null
      const quantity = Number(item.quantity || 0)
      const unitWeightG = item.unit_weight_g == null ? null : Number(item.unit_weight_g)
      const shippedWeightKg = Number(item.delivered_net_weight_kg ?? item.shipped_net_weight_kg ?? 0)
      const legacyShippedQuantity = shipments.filter((shipment) => shipment.order_id === order.id || shipment.order?.id === order.id).reduce((sum, shipment) => sum + Number(shipment.shipped_quantity || 0), 0)
      // Piece remainder is authoritative from the API. Never infer pieces from kilograms.
      const shippedQuantity = item.delivered_piece_quantity != null
        ? Number(item.delivered_piece_quantity)
        : item.shipped_quantity != null
          ? Number(item.shipped_quantity)
          : legacyShippedQuantity
      const remainingQuantity = Math.max(0, quantity - shippedQuantity)
      const dueDate = item.demand_date || item.due_date || null
      const linkedCaseCount = reworkCases.filter((entry) => String(entry.process_card_id || '') === String(item.id) || entry.source?.order_ids?.includes(order.id)).length
      // Current APIs already include weighted return cases in rework_count.
      // Only derive a fallback for older responses where the field is absent.
      const reworkCount = resolvedProcessCardReworkCount(item, linkedCaseCount)
      const due = dueDate ? dayjs(dueDate).startOf('day') : null
      const maxAllowedWeightKg = item.max_allowed_weight_kg == null ? null : Number(item.max_allowed_weight_kg)
      const missingDate = shippedWeightKg > 0 && batches.some((batch) => !batch.shipment_date && (batch.lines || []).some((line) => String(line.process_card_id || line.process_card?.id) === String(item.id)))
      return { key: String(item.id), cardNo: item.card_no, processCard: item, order, quantity, shippedQuantity, remainingQuantity, unitWeightG, expectedWeightKg: expectedWeightKg(remainingQuantity, unitWeightG), shippedWeightKg, maxAllowedWeightKg, dueDate, reworkCount, missingDate, overdue: Boolean(remainingQuantity && due?.isValid() && due.isBefore(dayjs().startOf('day'))), status: remainingQuantity <= 0 ? 'SHIPPED' : reworkCount ? 'REWORK' : shippedQuantity ? 'PARTIAL' : 'READY' } satisfies WorkflowCard
    }).filter(Boolean).filter((item) => (item as WorkflowCard).quantity > 0) as WorkflowCard[]
  }, [orders, processCards, reworks, reworkCases, shipments, batches, searchText])
  const visibleCards = useMemo(() => onlyAlerts ? cards.filter((card) => card.overdue || card.missingDate || card.reworkCount > 0) : cards, [cards, onlyAlerts])
  const selectedCards = cards.filter((card) => card.processCard && selectedKeys.includes(card.key))
  const pendingCards = cards.filter((card) => card.remainingQuantity > 0)
  const missingDateCount = cards.filter((card) => card.missingDate).length
  const pendingReworkCount = reworks.filter((item) => item.status !== 'COMPLETED').length + reworkCases.filter((item) => !['COMPLETED', 'SCRAPPED', 'CANCELLED'].includes(item.status)).length

  const columns: TableColumnsType<WorkflowCard> = [
    { title: '流程卡', key: 'card', fixed: 'left', width: 190, render: (_, card) => <Space direction="vertical" size={0}><Button type="link" className="table-primary-link" onClick={() => { setTimelineCard(card); onOpenTimeline(card) }}><strong>{card.cardNo}</strong><br /><Typography.Text type="secondary">{card.order.order_no} / {card.order.item_no || '-'}</Typography.Text></Button>{onSaveProcessCard && <Button type="link" size="small" icon={<EditOutlined />} onClick={() => setProcessCardForm(processCards.find((item) => String(item.id) === card.key) || null)}>编辑</Button>}</Space> },
    { title: '产品 / 规格', key: 'product', width: 190, render: (_, card) => <span>{card.order.product_name || '-'}<br /><Typography.Text type="secondary">{card.order.specification} · {card.order.material}</Typography.Text></span> },
    { title: '计划 / 剩余', key: 'quantity', width: 130, render: (_, card) => <span>{qualityNumber(card.quantity)}<br /><Typography.Text type="secondary">剩余 {qualityNumber(card.remainingQuantity)}</Typography.Text></span> },
    { title: '单重 / 出货kg', key: 'weight', width: 160, render: (_, card) => <span>{card.unitWeightG ? `${qualityNumber(card.unitWeightG, 4)} g` : <Tag color="warning">待补单重</Tag>}<br /><Typography.Text type="secondary">已发 {card.shippedWeightKg.toFixed(3)} · 剩余理论 {card.expectedWeightKg === null ? '-' : `${card.expectedWeightKg.toFixed(3)} kg`}</Typography.Text></span> },
    { title: '交期', dataIndex: 'dueDate', width: 110, render: (value) => formatQualityDate(value) },
    { title: '状态', key: 'status', width: 130, render: (_, card) => statusTag(card) },
    { title: '返工', dataIndex: 'reworkCount', width: 80, render: (value) => value ? <Badge count={value} overflowCount={99} color="#c4433b" /> : '-' },
    { title: '操作', key: 'actions', fixed: 'right', width: 170, render: (_, card) => <Space size={2}><Button type="link" size="small" disabled={!card.processCard || !card.remainingQuantity || !card.unitWeightG} onClick={() => { setSelectedKeys([card.key]); setBasketOpen(true) }}>加入出货篮</Button><Button type="link" size="small" onClick={() => { setTimelineCard(card); onOpenTimeline(card) }}>时间线</Button></Space> },
  ]
  const rowSelection: TableProps<WorkflowCard>['rowSelection'] = { selectedRowKeys: selectedKeys, onChange: setSelectedKeys, getCheckboxProps: (card) => ({ disabled: !card.processCard || !card.remainingQuantity || !card.unitWeightG }) }
  return (
    <div className="quality-workflow">
      <Row gutter={[12, 12]} className="quality-workflow-kpis">
        <Col xs={12} md={6}><Card className="quality-workflow-kpi"><Statistic title="待出货流程卡" value={pendingCards.length} suffix="张" /></Card></Col>
        <Col xs={12} md={6}><Card className="quality-workflow-kpi"><Statistic title="待出货件数" value={pendingCards.reduce((sum, card) => sum + card.remainingQuantity, 0)} suffix="件" /></Card></Col>
        <Col xs={12} md={6}><Card className={`quality-workflow-kpi ${missingDateCount ? 'has-warning' : ''}`}><Statistic title="待补出货日期" value={missingDateCount} suffix="条" /></Card></Col>
        <Col xs={12} md={6}><Card className={`quality-workflow-kpi ${pendingReworkCount ? 'has-warning' : ''}`}><Statistic title="待处理返工" value={pendingReworkCount} suffix="条" /></Card></Col>
      </Row>
      {(missingDateCount || cards.some((card) => card.overdue)) && <Alert className="quality-workflow-alert" type="warning" showIcon message={`需要关注 ${missingDateCount + cards.filter((card) => card.overdue).length} 项记录`} description="出货日期缺失、交期已过或返工未完成的流程卡会显示在这里。先补日期，再安排出货。" action={<Button size="small" onClick={() => setOnlyAlerts((value) => !value)}>{onlyAlerts ? '显示全部' : '只看提醒'}</Button>} />}
      <Card className="quality-workflow-card" title={<div className="quality-workflow-heading"><div><Typography.Title level={4}>流程卡与待出货篮</Typography.Title><Typography.Text type="secondary">流程卡单重未录入时可保存，但不可加入出货篮；胶料重量仅作记录，不参与成品重量计算。新增出货统一使用重量登记抽屉。</Typography.Text></div><Space wrap><Button onClick={() => setProcessCardForm(null)} icon={<PlusOutlined />}>新增流程卡</Button>{onOpenShipment && <Button onClick={onOpenShipment}>新增重量出货</Button>}<Button disabled={!selectedCards.length} onClick={() => setBasketOpen(true)}>出货篮（{selectedCards.length}）</Button><Button type="primary" disabled={!selectedCards.length} onClick={() => setBasketOpen(true)}>批量登记出货</Button></Space></div>}>
        <Table<WorkflowCard> rowKey="key" loading={loading} dataSource={visibleCards} columns={columns} rowSelection={rowSelection} scroll={{ x: 1170 }} pagination={{ pageSize: 12, showSizeChanger: true, showTotal: (total) => `共 ${total} 张流程卡` }} locale={{ emptyText: <Empty description={onlyAlerts ? '暂无待处理提醒' : '暂无可用流程卡'} /> }} />
      </Card>
      <BatchShipmentDrawer open={basketOpen} cards={selectedCards} orders={orders} employees={employees} shipments={shipments} batches={batches} onClose={() => setBasketOpen(false)} onSubmit={async (payload) => { await onSubmitBatch(payload); setSelectedKeys([]) }} />
      <ProcessCardDrawer open={processCardForm !== undefined} card={processCardForm || undefined} orders={orders} onClose={() => setProcessCardForm(undefined)} onSave={onSaveProcessCard} />
      <ReworkTimelineDrawer open={!!timelineCard} card={timelineCard} reworks={reworks} reworkCases={reworkCases} shipments={shipments} onClose={() => setTimelineCard(undefined)} onAdd={() => onOpenRework()} />
    </div>
  )
}
