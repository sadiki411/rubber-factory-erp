import {
  Alert,
  App,
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
  Popconfirm,
  Row,
  Select,
  Space,
  Table,
  Tag,
  Tabs,
  Typography,
} from 'antd'
import type { TableColumnsType } from 'antd'
import dayjs, { type Dayjs } from 'dayjs'
import { useEffect, useMemo, useState } from 'react'
import { PlusOutlined } from '@ant-design/icons'
import { qualityWorkflowApi } from '../api/client'
import type {
  QualityEmployee,
  QualityOrder,
  QualityProcessCard,
  QualityReworkCase,
  QualityShipmentBatch,
  QualityUnitWeight,
} from '../types'

interface Props {
  orders: QualityOrder[]
  employees: QualityEmployee[]
  cards: QualityProcessCard[]
  unitWeights: QualityUnitWeight[]
  batches: QualityShipmentBatch[]
  shipmentOptions?: QualityShipmentBatch[]
  reworkCases: QualityReworkCase[]
  onRefresh: () => Promise<void>
}

function dateValue(value?: string | null) {
  return value ? dayjs(value) : undefined
}

function dateText(value?: string | null) {
  return value ? dayjs(value).format('YYYY-MM-DD') : '-'
}

function valueText(value: unknown, suffix = '') {
  return value == null || value === '' ? '-' : `${String(value)}${suffix}`
}

function shipmentStatusText(status?: QualityShipmentBatch['status']) {
  if (status === 'CONFIRMED') return '已确认'
  if (status === 'VOID') return '已作废'
  return '草稿'
}

export interface ShipmentBatchReviewDrawerProps {
  open: boolean
  item?: QualityShipmentBatch
  employees: QualityEmployee[]
  onClose: () => void
  onSaved: () => Promise<void>
}

export function ShipmentBatchReviewDrawer({
  open,
  item,
  employees,
  onClose,
  onSaved,
}: ShipmentBatchReviewDrawerProps) {
  const [form] = Form.useForm<Record<string, unknown>>()
  const [saving, setSaving] = useState(false)
  const { message } = App.useApp()
  const lines = item?.lines || []
  const firstLine = lines[0]
  const firstCard = firstLine?.process_card
  const order = item?.order || firstLine?.order || firstCard?.order
  const productSpecification = item?.product_specification
  const productName = item?.product_name_snapshot
    || item?.product_name
    || productSpecification?.product_name
    || order?.product_name
    || firstCard?.product_name_snapshot
    || '-'
  const specification = item?.specification_snapshot
    || item?.specification
    || productSpecification?.specification
    || order?.specification
    || firstLine?.specification_snapshot
    || firstCard?.specification_snapshot
    || '-'
  const material = item?.material_snapshot
    || item?.material
    || productSpecification?.material
    || order?.material
    || firstLine?.material_snapshot
    || firstCard?.material_snapshot
    || '-'
  const unitWeight = item?.unit_weight_g_snapshot
    ?? item?.unit_weight_g
    ?? firstLine?.unit_weight_g_snapshot
    ?? firstLine?.unit_weight_g
  const singleBatchWeight = item?.single_batch_net_weight_kg
    ?? firstLine?.single_batch_net_weight_kg
  const batchCount = item?.product_batch_count
    ?? item?.batch_count
    ?? firstLine?.product_batch_count
    ?? 1
  const piecesPerBatch = item?.pieces_per_batch
    ?? firstLine?.pieces_per_batch
  const standardQuantity = item?.process_card_shipment_quantity
    ?? firstLine?.process_card_shipment_quantity
  const finalPieces = item?.shipped_quantity
    ?? item?.piece_quantity
    ?? (lines.length ? lines.reduce((sum, line) => sum + Number(line.piece_quantity || 0), 0) : null)
  const totalNetWeight = item?.total_net_weight_kg
    ?? item?.net_weight_kg
    ?? item?.actual_weight_kg

  const lineColumns: TableColumnsType<(typeof lines)[number]> = [
    {
      title: '流程卡 / 订单',
      key: 'reference',
      width: 190,
      render: (_, line) => {
        const lineOrder = line.order || line.process_card?.order
        return <span>
          <strong>{line.process_card?.card_no || line.card_no || '-'}</strong>
          <br />
          <Typography.Text type="secondary">{lineOrder?.order_no || item?.order?.order_no || '-'} / {lineOrder?.item_no || item?.order?.item_no || '-'}</Typography.Text>
        </span>
      },
    },
    {
      title: '产品 / 规格 / 材质',
      key: 'product',
      width: 220,
      render: (_, line) => {
        const lineOrder = line.order || line.process_card?.order
        return <span>
          {lineOrder?.product_name || line.process_card?.product_name_snapshot || productName}
          <br />
          <Typography.Text type="secondary">{line.specification_snapshot || line.process_card?.specification_snapshot || lineOrder?.specification || specification} · {line.material_snapshot || line.process_card?.material_snapshot || lineOrder?.material || material}</Typography.Text>
        </span>
      },
    },
    { title: '单重', key: 'unitWeight', width: 110, render: (_, line) => valueText(line.unit_weight_g_snapshot ?? line.unit_weight_g ?? unitWeight, ' g/件') },
    { title: '单批净重', dataIndex: 'single_batch_net_weight_kg', width: 115, render: (value) => valueText(value, ' kg') },
    { title: '批数', dataIndex: 'product_batch_count', width: 75, render: (value) => valueText(value ?? 1, ' 批') },
    { title: '单批件数', dataIndex: 'pieces_per_batch', width: 100, render: (value) => valueText(value, ' 件') },
    { title: '流程卡标准', dataIndex: 'process_card_shipment_quantity', width: 115, render: (value) => valueText(value, ' 件/批') },
    { title: '最终总件数', dataIndex: 'piece_quantity', width: 110, render: (value) => valueText(value, ' 件') },
    { title: '累计净重', dataIndex: 'net_weight_kg', width: 110, render: (value) => valueText(value, ' kg') },
  ]

  useEffect(() => {
    if (!open) return
    form.resetFields()
    form.setFieldsValue({
      shipment_date: dateValue(item?.shipment_date),
      inspector_ids: Array.from(new Set([
        ...(item?.inspector_ids || []),
        ...(item?.inspectors || []).map((inspector) => inspector.id),
        ...(item?.inspector_id == null ? [] : [item.inspector_id]),
      ])),
      backfill_reason: item?.backfill_reason || '',
      notes: item?.notes || '',
    })
  }, [form, item, open])

  const submit = async (confirm: boolean) => {
    if (!item) return
    const values = await form.validateFields()
    const shipmentDate = values.shipment_date as Dayjs | undefined
    const body = {
      shipment_date: shipmentDate?.format('YYYY-MM-DD') || null,
      inspector_ids: Array.isArray(values.inspector_ids) ? values.inspector_ids : [],
      backfill_reason: values.backfill_reason || '',
      notes: values.notes || '',
    }
    setSaving(true)
    try {
      if (item.status === 'CONFIRMED') {
        await qualityWorkflowApi.assignShipmentBatchInspectors(item.id, body.inspector_ids.map(Number))
      } else {
        await qualityWorkflowApi.updateShipmentBatch(item.id, body)
        if (confirm) await qualityWorkflowApi.confirmShipmentBatch(item.id)
      }
      await onSaved()
      message.success(item.status === 'CONFIRMED'
        ? '品检员已补录'
        : confirm ? '出货日期已补齐并确认入账' : '出货批次草稿已保存')
      onClose()
    } catch (error) {
      message.error((error as Error).message || '出货批次保存失败')
    } finally {
      setSaving(false)
    }
  }

  const voidDraft = async () => {
    if (!item) return
    setSaving(true)
    try {
      await qualityWorkflowApi.voidShipmentBatch(item.id)
      await onSaved()
      message.success('出货批次已作废，历史记录仍会保留')
      onClose()
    } catch (error) {
      message.error((error as Error).message || '出货批次作废失败')
    } finally {
      setSaving(false)
    }
  }

  return <Drawer
    open={open}
    onClose={onClose}
    width="min(960px, 100vw)"
    title={item ? `出货批次详情 · ${item.shipment_no}` : '出货批次详情'}
    footer={item?.status === 'VOID'
      ? <Button onClick={onClose}>关闭</Button>
      : item?.status === 'CONFIRMED'
      ? <Space className="drawer-footer-actions"><Button onClick={onClose}>取消</Button><Button type="primary" loading={saving} onClick={() => void submit(false)}>保存品检员</Button></Space>
      : <Space className="drawer-footer-actions"><Popconfirm title="确定作废这个草稿批次吗？" onConfirm={() => void voidDraft()}><Button danger loading={saving}>作废草稿</Button></Popconfirm><Button onClick={onClose}>取消</Button><Button loading={saving} onClick={() => void submit(false)}>保存草稿</Button><Button type="primary" loading={saving} onClick={() => void submit(true)}>保存并确认</Button></Space>}
  >
    <Descriptions
      bordered
      size="small"
      column={{ xs: 1, sm: 2, lg: 3 }}
      items={[
        { key: 'shipmentNo', label: '出货单号', children: <strong>{item?.shipment_no || '-'}</strong> },
        { key: 'shipmentDate', label: '出货日期', children: dateText(item?.shipment_date) },
        { key: 'status', label: '状态', children: <Tag color={item?.status === 'CONFIRMED' ? 'success' : item?.status === 'VOID' ? 'default' : 'warning'}>{shipmentStatusText(item?.status)}</Tag> },
        { key: 'order', label: '订单 / 项次', children: order ? `${order.order_no} / ${order.item_no || '-'}` : '-' },
        { key: 'dueDate', label: '交期', children: dateText(order?.due_date) },
        { key: 'product', label: '产品', children: productName },
        { key: 'specification', label: '规格', children: specification },
        { key: 'material', label: '材质', children: material },
        { key: 'unitWeight', label: '产品单重', children: valueText(unitWeight, ' g/件') },
        { key: 'singleWeight', label: '单批净重', children: valueText(singleBatchWeight, ' kg') },
        { key: 'batchCount', label: '批数', children: valueText(batchCount, ' 批') },
        { key: 'piecesPerBatch', label: '单批件数', children: valueText(piecesPerBatch, ' 件') },
        { key: 'standardQuantity', label: '流程卡标准', children: valueText(standardQuantity, ' 件/批') },
        { key: 'finalPieces', label: '最终总件数', children: valueText(finalPieces, ' 件') },
        { key: 'totalWeight', label: '累计净重', children: valueText(totalNetWeight, ' kg') },
        { key: 'notes', label: '备注', children: item?.notes || '-', span: 3 },
      ]}
    />
    {lines.length > 0 && <>
      <Divider titlePlacement="start">出货明细（{lines.length}行）</Divider>
      <Table rowKey="id" size="small" dataSource={lines} columns={lineColumns} pagination={false} scroll={{ x: 1145 }} />
    </>}
    <Divider titlePlacement="start">{item?.status === 'CONFIRMED' ? '补录或修改品检员' : item?.status === 'VOID' ? '记录状态' : '补充出货资料'}</Divider>
    {item?.status === 'VOID'
      ? <Alert type="info" showIcon message="该出货批次已作废" description="作废记录仅供查询，不能再修改或确认。" />
      : <>
        <Alert type="info" showIcon message={item?.status === 'CONFIRMED' ? '品检员可以在确认出货后补录' : '出货日期必须与实际单据一致'} description={item?.status === 'CONFIRMED' ? '可选择一人或多人；留空保存也不会改变出货数量和重量。' : '如果补录历史日期，请填写补录原因；未填写日期的草稿不能确认入账。'} />
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="inspector_ids" label="品检员（选填，可多选）"><Select mode="multiple" allowClear showSearch optionFilterProp="label" placeholder="选择品检员或暂时留空" options={employees.filter((employee) => employee.is_active && ['INSPECTOR', 'BOTH'].includes(employee.role)).map((employee) => ({ value: employee.id, label: `${employee.employee_no} · ${employee.name}${employee.team ? ` · ${employee.team}` : ''}` }))} /></Form.Item>
          {item?.status !== 'CONFIRMED' && <>
            <Form.Item name="shipment_date" label="实际出货日期" rules={[{ required: true, message: '请选择实际出货日期' }]}><DatePicker style={{ width: '100%' }} /></Form.Item>
            <Form.Item name="backfill_reason" label="历史日期补录原因" extra="仅补录早于今天的日期时必填"><Input.TextArea rows={2} maxLength={300} showCount /></Form.Item>
            <Form.Item name="notes" label="备注"><Input.TextArea rows={3} maxLength={300} showCount /></Form.Item>
          </>}
        </Form>
      </>}
  </Drawer>
}

function WeightDrawer({
  open,
  item,
  orders,
  onClose,
  onSaved,
}: {
  open: boolean
  item?: QualityUnitWeight
  orders: QualityOrder[]
  onClose: () => void
  onSaved: () => Promise<void>
}) {
  const [form] = Form.useForm<Record<string, unknown>>()
  const [saving, setSaving] = useState(false)
  const { message } = App.useApp()
  const specs = useMemo(() => {
    const map = new Map<number, { id: number; label: string }>()
    orders.forEach((order) => {
      const spec = order.product_specification
      if (spec?.id) map.set(spec.id, { id: spec.id, label: `${spec.product_name || order.product_name} · ${spec.specification || order.specification} · ${spec.material || order.material}` })
    })
    return [...map.values()]
  }, [orders])
  const moldModels = useMemo(() => {
    const map = new Map<number, { id: number; label: string }>()
    orders.forEach((order) => {
      const mold = order.product_specification?.mold_model
      if (mold?.id) map.set(mold.id, { id: mold.id, label: `${mold.code} · ${mold.product_name || '模具型号'}` })
    })
    return [...map.values()]
  }, [orders])

  useEffect(() => {
    if (!open) return
    form.resetFields()
    form.setFieldsValue({
      product_specification_id: item?.product_specification_id ?? undefined,
      mold_model_id: item?.mold_model_id ?? undefined,
      sample_count: item?.sample_count ?? undefined,
      sample_total_weight_g: item?.sample_total_weight_g ?? undefined,
      unit_weight_g: item?.unit_weight_g ?? undefined,
      measured_on: dateValue(item?.measured_on) || dayjs(),
      is_active: item?.is_active ?? true,
      notes: item?.notes || '',
    })
  }, [form, item, open])

  const submit = async () => {
    const values = await form.validateFields()
    if (!values.product_specification_id && !values.mold_model_id) {
      message.warning('请至少关联产品规格或模具型号。')
      return
    }
    const measured = values.measured_on as Dayjs | undefined
    const body: Record<string, unknown> = {
      product_specification_id: values.product_specification_id || null,
      mold_model_id: values.mold_model_id || null,
      sample_count: values.sample_count ?? null,
      sample_total_weight_g: values.sample_total_weight_g ?? null,
      unit_weight_g: values.unit_weight_g ?? null,
      measured_on: measured?.format('YYYY-MM-DD'),
      is_active: values.is_active !== false,
      notes: values.notes || '',
    }
    setSaving(true)
    try {
      if (item) await qualityWorkflowApi.updateUnitWeight(item.id, body)
      else await qualityWorkflowApi.createUnitWeight(body)
      await onSaved()
      message.success('成品单重标准已保存')
      onClose()
    } catch (error) {
      message.error((error as Error).message || '保存单重失败')
    } finally {
      setSaving(false)
    }
  }

  return <Drawer open={open} onClose={onClose} width={520} title={item ? '编辑成品单重标准' : '新增成品单重标准'} footer={<Space className="drawer-footer-actions"><Button onClick={onClose}>取消</Button><Button type="primary" loading={saving} onClick={() => void submit()}>保存</Button></Space>}>
    <Alert type="info" showIcon message="单重单位为 g/件" description="标准单重=样品总净重÷抽样件数。胶料重量不填在这里；流程卡建立时会保存单重快照。" />
    <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
      <Form.Item name="product_specification_id" label="产品规格"><Select allowClear showSearch optionFilterProp="label" options={specs.map((spec) => ({ value: spec.id, label: spec.label }))} placeholder="按产品规格关联（推荐）" /></Form.Item>
      <Form.Item name="mold_model_id" label="模具型号（可选）" extra="不同模具型号单重有差异时选择；下拉为空时可填写产品规格"><Select allowClear showSearch optionFilterProp="label" options={moldModels.map((mold) => ({ value: mold.id, label: mold.label }))} placeholder="选择模具型号" /></Form.Item>
      <Row gutter={12}><Col xs={12}><Form.Item name="sample_count" label="抽样件数"><InputNumber min={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col><Col xs={12}><Form.Item name="sample_total_weight_g" label="样品总净重(g)"><InputNumber min={0.00001} precision={5} style={{ width: '100%' }} /></Form.Item></Col></Row>
      <Form.Item name="unit_weight_g" label="标准单重(g/件)" rules={[{ type: 'number', min: 0.00001, message: '请输入大于0的单重，或填写抽样件数和样品总重' }]}><InputNumber min={0.00001} precision={5} style={{ width: '100%' }} /></Form.Item>
      <Form.Item name="measured_on" label="测量日期" rules={[{ required: true, message: '请选择测量日期' }]}><DatePicker style={{ width: '100%' }} /></Form.Item>
      <Form.Item name="notes" label="备注"><Input.TextArea rows={3} placeholder="例如：模具A版、三次称重平均" /></Form.Item>
    </Form>
  </Drawer>
}

function ReworkCaseDrawer({
  open,
  cards,
  batches,
  shipmentOptions = batches,
  item,
  onClose,
  onSaved,
}: {
  open: boolean
  cards: QualityProcessCard[]
  batches: QualityShipmentBatch[]
  shipmentOptions?: QualityShipmentBatch[]
  item?: QualityReworkCase
  onClose: () => void
  onSaved: () => Promise<void>
}) {
  const [form] = Form.useForm<Record<string, unknown>>()
  const [saving, setSaving] = useState(false)
  const { message } = App.useApp()
  const lines = useMemo(() => shipmentOptions
    .filter((batch) => batch.status === 'CONFIRMED')
    .flatMap((batch) => (batch.lines || []).map((line) => ({ ...line, batchNo: batch.shipment_no }))), [shipmentOptions])
  useEffect(() => {
    if (!open) return
    form.resetFields()
    form.setFieldsValue({
      origin: item?.origin || 'INTERNAL',
      process_card_id: item?.process_card_id ?? undefined,
      shipment_line_id: item?.shipment_line_id ?? undefined,
      opened_on: dateValue(item?.opened_on) || dayjs(),
      reason_category: item?.reason_category || 'OTHER',
      reason: item?.reason || '',
      affected_quantity: item?.affected_quantity ?? undefined,
      affected_weight_kg: item?.affected_weight_kg ?? undefined,
      notes: item?.notes || '',
    })
  }, [form, item, open])
  const origin = Form.useWatch('origin', form)
  const submit = async () => {
    const values = await form.validateFields()
    const opened = values.opened_on as Dayjs | undefined
    const body: Record<string, unknown> = {
      origin: values.origin,
      process_card_id: values.process_card_id,
      shipment_line_id: values.origin === 'CUSTOMER_RETURN' ? values.shipment_line_id : null,
      opened_on: opened?.format('YYYY-MM-DD'),
      reason_category: values.reason_category,
      reason: values.reason || '',
      affected_quantity: values.affected_quantity ?? null,
      affected_weight_kg: values.affected_weight_kg ?? null,
      notes: values.notes || '',
    }
    setSaving(true)
    try {
      if (item) await qualityWorkflowApi.updateReworkCase(item.id, body)
      else await qualityWorkflowApi.createReworkCase(body)
      await onSaved()
      message.success('返工主案已保存')
      onClose()
    } catch (error) {
      message.error((error as Error).message || '保存返工主案失败')
    } finally {
      setSaving(false)
    }
  }
  return <Drawer open={open} onClose={onClose} width={560} title={item ? '编辑返工主案' : '新增返工主案'} footer={<Space className="drawer-footer-actions"><Button onClick={onClose}>取消</Button><Button type="primary" loading={saving} onClick={() => void submit()}>保存</Button></Space>}>
    <Alert type="info" showIcon message="内部返工与客户退回分开记录" description="内部返工不要求出货记录；客户退回必须选择已确认的出货明细，退回重量会返还流程卡可重发额度。" />
    <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
      <Form.Item name="origin" label="返工来源" rules={[{ required: true }]}><Select options={[{ value: 'INTERNAL', label: '内部返工（出货前）' }, { value: 'CUSTOMER_RETURN', label: '客户退回返工（出货后）' }]} /></Form.Item>
      <Form.Item name="process_card_id" label="流程卡" rules={[{ required: true, message: '请选择流程卡' }]}><Select showSearch optionFilterProp="label" options={cards.map((card) => ({ value: card.id, label: `${card.card_no} · ${card.source_order_no || card.order_id}` }))} /></Form.Item>
      {origin === 'CUSTOMER_RETURN' && <Form.Item name="shipment_line_id" label="已确认出货明细" rules={[{ required: true, message: '客户退回必须选择出货明细' }]}><Select showSearch optionFilterProp="label" options={lines.map((line) => ({ value: line.id, label: `${line.batchNo} · ${line.process_card?.card_no || line.process_card_id} · ${line.net_weight_kg}kg` }))} /></Form.Item>}
      <Row gutter={12}><Col xs={12}><Form.Item name="opened_on" label="发生日期" rules={[{ required: true }]}><DatePicker style={{ width: '100%' }} /></Form.Item></Col><Col xs={12}><Form.Item name="reason_category" label="原因分类" rules={[{ required: true }]}><Select options={[{ value: 'APPEARANCE', label: '外观' }, { value: 'DIMENSION', label: '尺寸' }, { value: 'MATERIAL', label: '材质' }, { value: 'MIXED', label: '混料/混装' }, { value: 'PACKAGING', label: '包装' }, { value: 'OTHER', label: '其他' }]} /></Form.Item></Col></Row>
      <Row gutter={12}><Col xs={12}><Form.Item name="affected_quantity" label="影响件数"><InputNumber min={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col><Col xs={12}><Form.Item name="affected_weight_kg" label="影响重量(kg)"><InputNumber min={0.001} precision={3} style={{ width: '100%' }} /></Form.Item></Col></Row>
      <Form.Item name="reason" label="问题描述"><Input.TextArea rows={3} /></Form.Item>
      <Form.Item name="notes" label="备注"><Input.TextArea rows={2} /></Form.Item>
    </Form>
  </Drawer>
}

function AttemptDrawer({
  open,
  item,
  onClose,
  onSaved,
}: {
  open: boolean
  item?: QualityReworkCase
  onClose: () => void
  onSaved: () => Promise<void>
}) {
  const [form] = Form.useForm<Record<string, unknown>>()
  const [saving, setSaving] = useState(false)
  const { message } = App.useApp()
  useEffect(() => {
    if (!open) return
    form.resetFields()
    form.setFieldsValue({ attempt_date: dayjs(), status: 'WAITING_REINSPECTION' })
  }, [form, open])
  const submit = async () => {
    if (!item) return
    const values = await form.validateFields()
    const date = values.attempt_date as Dayjs | undefined
    const body = {
      case_id: item.id,
      attempt_date: date?.format('YYYY-MM-DD'),
      input_quantity: Number(values.input_quantity || 0),
      reworked_quantity: Number(values.reworked_quantity || 0),
      recovered_quantity: Number(values.recovered_quantity || 0),
      scrap_quantity: Number(values.scrap_quantity || 0),
      input_weight_kg: Number(values.input_weight_kg || 0),
      reworked_weight_kg: Number(values.reworked_weight_kg || 0),
      recovered_weight_kg: Number(values.recovered_weight_kg || 0),
      scrap_weight_kg: Number(values.scrap_weight_kg || 0),
      status: values.status,
      notes: values.notes || '',
    }
    setSaving(true)
    try {
      await qualityWorkflowApi.createReworkAttempt(body)
      await onSaved()
      message.success('返工轮次已记录')
      onClose()
    } catch (error) {
      message.error((error as Error).message || '保存返工轮次失败')
    } finally {
      setSaving(false)
    }
  }
  return <Drawer open={open} onClose={onClose} width={540} title={item ? `${item.case_no} · 新增下一轮返工` : '新增返工轮次'} footer={<Space className="drawer-footer-actions"><Button onClick={onClose}>取消</Button><Button type="primary" loading={saving} onClick={() => void submit()}>保存</Button></Space>}>
    <Alert type="info" showIcon message="系统自动生成 R1、R2、R3…" description="可以按件数、重量或两者记录；同一主案累计投入不能超过影响数量。" />
    <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
      <Form.Item name="attempt_date" label="处理日期" rules={[{ required: true }]}><DatePicker style={{ width: '100%' }} /></Form.Item>
      <Row gutter={12}><Col xs={12}><Form.Item name="input_quantity" label="投入件数"><InputNumber min={0} precision={0} style={{ width: '100%' }} /></Form.Item></Col><Col xs={12}><Form.Item name="reworked_quantity" label="返工件数"><InputNumber min={0} precision={0} style={{ width: '100%' }} /></Form.Item></Col></Row>
      <Row gutter={12}><Col xs={12}><Form.Item name="recovered_quantity" label="合格件数"><InputNumber min={0} precision={0} style={{ width: '100%' }} /></Form.Item></Col><Col xs={12}><Form.Item name="scrap_quantity" label="报废件数"><InputNumber min={0} precision={0} style={{ width: '100%' }} /></Form.Item></Col></Row>
      <Row gutter={12}><Col xs={12}><Form.Item name="input_weight_kg" label="投入重量(kg)"><InputNumber min={0} precision={3} style={{ width: '100%' }} /></Form.Item></Col><Col xs={12}><Form.Item name="reworked_weight_kg" label="返工重量(kg)"><InputNumber min={0} precision={3} style={{ width: '100%' }} /></Form.Item></Col></Row>
      <Form.Item name="status" label="本轮结果"><Select options={[{ value: 'PROCESSING', label: '返工中' }, { value: 'WAITING_REINSPECTION', label: '待复检' }, { value: 'COMPLETED', label: '合格完成' }, { value: 'SCRAPPED', label: '报废' }]} /></Form.Item>
      <Form.Item name="notes" label="备注"><Input.TextArea rows={3} /></Form.Item>
    </Form>
  </Drawer>
}

export function QualityWorkflowManagement({ orders, employees, cards, unitWeights, batches, shipmentOptions = batches, reworkCases, onRefresh }: Props) {
  const [weightItem, setWeightItem] = useState<QualityUnitWeight | null | undefined>(undefined)
  const [caseItem, setCaseItem] = useState<QualityReworkCase | null | undefined>(undefined)
  const [attemptCase, setAttemptCase] = useState<QualityReworkCase | undefined>(undefined)
  const [batchItem, setBatchItem] = useState<QualityShipmentBatch | undefined>(undefined)
  const cardLabels = useMemo(() => new Map(cards.map((card) => [String(card.id), card.card_no])), [cards])
  const weightColumns: TableColumnsType<QualityUnitWeight> = [
    { title: '产品 / 材质', key: 'spec', render: (_, row) => {
      const product = row.product_specification
      if (product) {
        const primary = [product.product_name, product.material].filter(Boolean).join(' · ') || '未命名产品'
        return <span><strong>{primary}</strong><br /><Typography.Text type="secondary">{product.specification || '未填写规格'}</Typography.Text></span>
      }
      if (row.mold_model) {
        return <span><strong>{row.mold_model.product_name || '模具型号'}</strong><br /><Typography.Text type="secondary">{row.mold_model.code || '未填写型号'}</Typography.Text></span>
      }
      return <Typography.Text type="secondary">未关联产品规格</Typography.Text>
    } },
    { title: '标准单重', dataIndex: 'unit_weight_g', render: (value) => value == null ? '-' : `${value} g/件` },
    { title: '抽样', key: 'sample', render: (_, row) => row.sample_count && row.sample_total_weight_g ? `${row.sample_count}件 / ${row.sample_total_weight_g}g` : '直接录入' },
    { title: '测量日期', dataIndex: 'measured_on', render: dateText },
    { title: '状态', dataIndex: 'is_active', render: (value) => <Tag color={value ? 'success' : 'default'}>{value ? '启用' : '停用'}</Tag> },
    { title: '操作', key: 'action', render: (_, row) => <Button type="link" onClick={() => setWeightItem(row)}>编辑</Button> },
  ]
  const batchColumns: TableColumnsType<QualityShipmentBatch> = [
    { title: '批次', dataIndex: 'shipment_no', width: 205, render: (value, row) => <span><Button type="link" className="table-primary-link" onClick={() => setBatchItem(row)}><strong>{value}</strong></Button><br /><Typography.Text type="secondary">{dateText(row.shipment_date)}</Typography.Text></span> },
    { title: '订单 / 项次', key: 'orders', width: 190, render: (_, row) => {
      const linked = [row.order, ...(row.lines || []).map((line) => line.order || line.process_card?.order)]
        .filter((order): order is QualityOrder => Boolean(order))
      const summaries = [...new Map(linked.map((order) => [`${order.id}:${order.item_no || ''}`, `${order.order_no} / ${order.item_no || '-'}`])).values()]
      if (!summaries.length && row.order_no) summaries.push(row.order_no)
      return summaries.length ? <Space direction="vertical" size={0}>{summaries.map((summary) => <span key={summary}>{summary}</span>)}</Space> : '-'
    } },
    { title: '产品 / 材质', key: 'products', width: 220, render: (_, row) => {
      const summaries = [
        {
          product: row.product_name_snapshot || row.product_name || row.product_specification?.product_name || row.order?.product_name || '',
          material: row.material_snapshot || row.material || row.product_specification?.material || row.order?.material || '',
          specification: row.specification_snapshot || row.specification || row.product_specification?.specification || row.order?.specification || '',
        },
        ...(row.lines || []).map((line) => {
          const lineOrder = line.order || line.process_card?.order
          return {
            product: line.product_specification?.product_name || lineOrder?.product_name || line.process_card?.product_name_snapshot || '',
            material: line.material_snapshot || line.product_specification?.material || lineOrder?.material || line.process_card?.material_snapshot || '',
            specification: line.specification_snapshot || line.product_specification?.specification || lineOrder?.specification || line.process_card?.specification_snapshot || '',
          }
        }),
      ].filter((summary) => summary.product || summary.material || summary.specification)
      const unique = [...new Map(summaries.map((summary) => [`${summary.product}\u0000${summary.material}\u0000${summary.specification}`, summary])).values()]
      return unique.length ? <Space direction="vertical" size={2}>{unique.map((summary) => <span key={`${summary.product}\u0000${summary.material}\u0000${summary.specification}`}><strong>{summary.product || '-'} · {summary.material || '-'}</strong><br /><Typography.Text type="secondary">{summary.specification || '未填写规格'}</Typography.Text></span>)}</Space> : '-'
    } },
    { title: '交期', key: 'dueDates', width: 115, render: (_, row) => {
      const dates = [
        row.order?.due_date,
        ...(row.lines || []).flatMap((line) => [line.order?.due_date, line.process_card?.order?.due_date, line.process_card?.due_date, line.process_card?.demand_date]),
      ].filter((value): value is string => Boolean(value))
      const unique = [...new Set(dates)].sort()
      return unique.length ? <Space direction="vertical" size={0}>{unique.map((value) => <span key={value}>{dateText(value)}</span>)}</Space> : '-'
    } },
    { title: '客户/说明', key: 'info', width: 175, render: (_, row) => `${row.customer || '-'}${row.delivery_info ? ` · ${row.delivery_info}` : ''}` },
    { title: '明细', dataIndex: 'line_count', width: 105, render: (value, row) => <Button type="link" onClick={() => setBatchItem(row)}>明细 {value || row.lines?.length || 0} 行</Button> },
    { title: '净重', dataIndex: 'net_weight_kg', width: 105, render: (value) => `${value || 0} kg` },
    { title: '品检员', key: 'inspectors', width: 130, render: (_, row) => {
      const people = (row.inspectors || []).length ? row.inspectors || [] : row.inspector ? [row.inspector] : []
      const names = people.map((inspector) => inspector.name)
      return names.length ? names.join('、') : <Tag color="warning">待补录</Tag>
    } },
    { title: '状态', dataIndex: 'status', width: 90, render: (value) => <Tag color={value === 'CONFIRMED' ? 'success' : value === 'VOID' ? 'default' : 'warning'}>{value === 'CONFIRMED' ? '已确认' : value === 'VOID' ? '已作废' : '草稿'}</Tag> },
    { title: '操作', key: 'action', width: 115, render: (_, row) => row.status === 'DRAFT'
      ? <Button type="link" onClick={() => setBatchItem(row)}>补日期 / 确认</Button>
      : row.status === 'CONFIRMED'
        ? <Button type="link" onClick={() => setBatchItem(row)}>{(row.inspectors || []).length || row.inspector ? '修改品检员' : '补录品检员'}</Button>
        : <Typography.Text type="secondary">-</Typography.Text> },
  ]
  const caseColumns: TableColumnsType<QualityReworkCase> = [
    { title: '主案', dataIndex: 'case_no', render: (value, row) => <span><strong>{value}</strong><br /><Typography.Text type="secondary">{row.origin === 'INTERNAL' ? '内部返工' : '客户退回'} · {dateText(row.opened_on)}</Typography.Text></span> },
    { title: '流程卡', dataIndex: 'process_card_id', render: (value) => cardLabels.get(String(value)) || `卡#${value || '-'}` },
    { title: '问题', dataIndex: 'reason', ellipsis: true, render: (value) => value || '-' },
    { title: '轮次', key: 'attempts', render: (_, row) => <Space><BadgeCount count={row.attempt_count || row.attempts?.length || 0} /><Button type="link" size="small" onClick={() => setAttemptCase(row)}>新增轮次</Button></Space> },
    { title: '状态', dataIndex: 'status', render: (value) => <Tag color={value === 'COMPLETED' ? 'success' : value === 'SCRAPPED' ? 'error' : 'warning'}>{value}</Tag> },
    { title: '操作', key: 'action', render: (_, row) => <Button type="link" onClick={() => setCaseItem(row)}>编辑</Button> },
  ]
  const BadgeCount = ({ count }: { count: number }) => <Tag color={count > 2 ? 'error' : 'blue'}>{count}轮</Tag>
  return <div className="quality-workflow-management">
    <Tabs items={[
      { key: 'weights', label: `成品单重标准（${unitWeights.length}）`, children: <Card title="成品单重标准" extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => setWeightItem(null)}>新增标准</Button>}><Typography.Paragraph type="secondary">按产品规格/模具型号维护成品单重；流程卡建立时保存快照，后续修改不会改变历史出货计算。</Typography.Paragraph>{unitWeights.length ? <Table rowKey="id" dataSource={unitWeights} columns={weightColumns} scroll={{ x: 720 }} pagination={{ pageSize: 8 }} /> : <Empty description="尚未维护单重标准，可直接在流程卡上填写单重" />}</Card> },
      { key: 'batches', label: `重量出货批次（${batches.length}）`, children: <Card title="重量出货批次" extra={<Tag color="blue">超过理论+10%会阻断</Tag>}>{batches.length ? <Table rowKey="id" dataSource={batches} columns={batchColumns} scroll={{ x: 1550 }} pagination={{ pageSize: 8 }} /> : <Empty description="暂无重量出货批次" />}</Card> },
      { key: 'rework', label: `返工主案（${reworkCases.length}）`, children: <Card title="内部返工 / 客户退回返工" extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => setCaseItem(null)}>新增返工主案</Button>}><Typography.Paragraph type="secondary">每个主案可追加 R1、R2、R3…；客户退回会保留出货历史并返还可重发额度。</Typography.Paragraph>{reworkCases.length ? <Table rowKey="id" dataSource={reworkCases} columns={caseColumns} scroll={{ x: 900 }} pagination={{ pageSize: 8 }} /> : <Empty description="暂无返工主案" />}</Card> },
    ]} />
    <WeightDrawer open={weightItem !== undefined} item={weightItem || undefined} orders={orders} onClose={() => setWeightItem(undefined)} onSaved={onRefresh} />
    <ShipmentBatchReviewDrawer open={!!batchItem} item={batchItem} employees={employees} onClose={() => setBatchItem(undefined)} onSaved={onRefresh} />
    <ReworkCaseDrawer open={caseItem !== undefined} item={caseItem || undefined} cards={cards} batches={batches} shipmentOptions={shipmentOptions} onClose={() => setCaseItem(undefined)} onSaved={onRefresh} />
    <AttemptDrawer open={!!attemptCase} item={attemptCase} onClose={() => setAttemptCase(undefined)} onSaved={onRefresh} />
  </div>
}
