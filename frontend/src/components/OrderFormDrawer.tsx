import { Alert, App, Button, Col, DatePicker, Descriptions, Drawer, Form, Input, InputNumber, Row, Select, Space, Tag } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import dayjs, { type Dayjs } from 'dayjs'
import { useEffect } from 'react'
import { orderApi, productSpecificationApi, toList } from '../api/client'
import type { Order, OrderMaterialStatus, OrderProcessCardStatus } from '../types'

interface Props {
  open: boolean
  order?: Order
  onClose: () => void
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

function exactValue(value: unknown, suffix = '') {
  return value === null || value === undefined || value === '' ? '未登记' : `${String(value)}${suffix}`
}

export function OrderFormDrawer({ open, order, onClose }: Props) {
  const [form] = Form.useForm<Record<string, any>>()
  const queryClient = useQueryClient()
  const { message } = App.useApp()
  const selectedSpecificationId = Form.useWatch<number>('product_specification_id', form)
  const specificationsQuery = useQuery({
    queryKey: ['product-specifications', 'order-options'],
    queryFn: async () => toList(await productSpecificationApi.list({ page_size: 1000 })),
    enabled: open,
  })

  useEffect(() => {
    if (!open) return
    form.resetFields()
    form.setFieldsValue(order ? {
      ...order,
      product_specification_id: order.product_specification_id || order.product_specification?.id,
      order_date: order.order_date ? dayjs(order.order_date) : undefined,
      due_date: order.due_date ? dayjs(order.due_date) : undefined,
      production_required_choice: order.production_required === true ? 'YES' : order.production_required === false ? 'NO' : 'UNKNOWN',
    } : {
      order_date: dayjs(),
      status: 'OPEN',
      production_required_choice: 'UNKNOWN',
    })
  }, [form, open, order])

  const mutation = useMutation({
    mutationFn: (values: Record<string, any>) => {
      const productionChoice = values.production_required_choice
      const editableValues = { ...values }
      delete editableValues.production_required_choice
      const body = {
        ...editableValues,
        order_date: values.order_date ? (values.order_date as Dayjs).format('YYYY-MM-DD') : null,
        due_date: values.due_date ? (values.due_date as Dayjs).format('YYYY-MM-DD') : null,
        production_required: productionChoice === 'YES' ? true : productionChoice === 'NO' ? false : null,
      }
      return order ? orderApi.update(order.id, body) : orderApi.create(body)
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['orders'] }),
        queryClient.invalidateQueries({ queryKey: ['quality'] }),
        queryClient.invalidateQueries({ queryKey: ['production'] }),
        queryClient.invalidateQueries({ queryKey: ['analytics'] }),
      ])
      message.success(order ? '订单资料已更新' : '订单已创建')
      onClose()
    },
    onError: (error: Error) => message.error(error.message),
  })

  const selectSpecification = (id?: number) => {
    const selected = specificationsQuery.data?.find((item) => item.id === id)
    if (!selected) return
    form.setFieldsValue({
      product_code: selected.customer_product_no || form.getFieldValue('product_code'),
      product_name: selected.product_name || form.getFieldValue('product_name'),
      specification: selected.specification || form.getFieldValue('specification'),
      material: selected.material || form.getFieldValue('material'),
      mold_size: selected.mold_size || form.getFieldValue('mold_size'),
      forming_hours: /^\d+(?:\.\d+)?$/.test(String(selected.standard_hours || '').trim()) ? selected.standard_hours : form.getFieldValue('forming_hours'),
    })
  }

  const selectedSpecification = specificationsQuery.data?.find((item) => item.id === selectedSpecificationId)
    || (order && order.product_specification?.id === selectedSpecificationId ? order.product_specification : undefined)

  const submit = async () => mutation.mutate(await form.validateFields())

  return (
    <Drawer
      className="business-data-drawer"
      open={open}
      onClose={onClose}
      size={800}
      title={order ? `编辑订单 · ${order.order_no}${order.item_no ? ` / ${order.item_no}` : ''}` : '新增订单'}
      footer={<Space className="drawer-footer-actions"><Button onClick={onClose}>取消</Button><Button type="primary" loading={mutation.isPending} onClick={() => void submit()}>保存</Button></Space>}
    >
      {order && (
        <Descriptions className="order-balance-summary" size="small" bordered column={{ xs: 2, sm: 4 }}>
          <Descriptions.Item label="所需胶料">{exactValue(order.required_material_kg, ' kg')}</Descriptions.Item>
          <Descriptions.Item label="发料单累计">{exactValue(order.imported_received_material_kg, ' kg')}</Descriptions.Item>
          <Descriptions.Item label="已发胶料合计">{exactValue(order.received_material_kg, ' kg')}</Descriptions.Item>
          <Descriptions.Item label="胶料差额">{exactValue(order.material_gap_kg, ' kg')}</Descriptions.Item>
          <Descriptions.Item label="胶料状态"><Tag color={MATERIAL_META[order.material_status || 'UNKNOWN'].color}>{MATERIAL_META[order.material_status || 'UNKNOWN'].text}</Tag></Descriptions.Item>
          <Descriptions.Item label="流程卡状态"><Tag color={PROCESS_CARD_META[order.process_card_status || 'NOT_RECEIVED'].color}>{PROCESS_CARD_META[order.process_card_status || 'NOT_RECEIVED'].text}</Tag></Descriptions.Item>
        </Descriptions>
      )}
      {order?.source_sheet && <Alert className="business-source-alert" type="info" showIcon title={`来源：${order.source_sheet}${order.source_row ? ` 第 ${order.source_row} 行` : ''}`} description="在线修改不会删除原始导入内容，便于后续核对。" />}

      <Form form={form} layout="vertical" requiredMark="optional">
        <div className="business-form-section">订单资料</div>
        <Row gutter={14}>
          <Col xs={24} sm={12}><Form.Item name="order_no" label="订单编号" rules={[{ required: true, whitespace: true, message: '请输入订单编号' }]}><Input /></Form.Item></Col>
          <Col xs={12} sm={6}><Form.Item name="item_no" label="项次"><Input /></Form.Item></Col>
          <Col xs={12} sm={6}><Form.Item name="batch_no" label="批次号"><Input /></Form.Item></Col>
          <Col xs={24}><Form.Item name="product_specification_id" label="关联产品规格"><Select allowClear showSearch optionFilterProp="label" loading={specificationsQuery.isLoading} onChange={selectSpecification} placeholder="选择后带入产品、规格、材质和模具资料" options={(specificationsQuery.data || []).map((item) => ({ value: item.id, label: [item.customer_product_no, item.product_name, item.specification].filter(Boolean).join(' · '), disabled: !item.is_active }))} /></Form.Item></Col>
          {selectedSpecification && <Col xs={24}><Alert type="info" showIcon title="产品工艺参考" description={`标准工时：${selectedSpecification.standard_hours || '-'}；一次硫化：${selectedSpecification.primary_curing || '-'}；二烤：${selectedSpecification.secondary_curing || '-'}；模具：${selectedSpecification.mold_no || selectedSpecification.mold_size || '-'}`} /></Col>}
          <Col xs={24} sm={12}><Form.Item name="product_code" label="产品编码"><Input /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="product_name" label="产品名称"><Input /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="specification" label="规格" rules={[{ required: true, whitespace: true, message: '请输入规格' }]}><Input /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="material" label="材质 / 胶料"><Input /></Form.Item></Col>
          <Col xs={12} sm={8}><Form.Item name="order_quantity" label="订单数量" rules={[{ required: true, message: '请输入订单数量' }]}><InputNumber min={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={12} sm={8}><Form.Item name="forming_hours" label="成型工时"><Input placeholder="可保留原始小数" /></Form.Item></Col>
          <Col xs={24} sm={8}><Form.Item name="mold_size" label="模具 / 模具尺寸"><Input /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="order_date" label="订单日期"><DatePicker style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="due_date" label="交付日期"><DatePicker style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="status" label="订单状态" rules={[{ required: true }]}><Select options={[{ value: 'OPEN', label: '进行中' }, { value: 'COMPLETED', label: '已完成' }, { value: 'CANCELLED', label: '已取消' }]} /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="production_required_choice" label="是否需要生产"><Select options={[{ value: 'UNKNOWN', label: '未登记' }, { value: 'YES', label: '需要生产' }, { value: 'NO', label: '无需生产' }]} /></Form.Item></Col>
        </Row>

        <div className="business-form-section">胶料与流程卡</div>
        <Alert className="business-form-hint" type="info" showIcon title="“发料单累计”由导入的客户发料清单自动汇总；手工登记量与其相加形成“已发胶料合计”。空值和 0 会分别保存。" />
        <Row gutter={14}>
          <Col xs={24} sm={12}><Form.Item name="required_material_kg" label="所需胶料（kg）"><Input inputMode="decimal" placeholder="未登记时留空" /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="manual_received_material_kg" label="手工登记已发胶料（kg）"><Input inputMode="decimal" placeholder="未登记时留空；实际为 0 时填写 0" /></Form.Item></Col>
          <Col xs={12}><Form.Item name="process_card_count" label="流程卡张数"><InputNumber min={0} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={12}><Form.Item name="process_card_covered_quantity" label="流程卡覆盖订单数量"><InputNumber min={0} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
        </Row>
        <Form.Item name="legacy_shipment_text" label="原出货 / 流程卡说明"><Input.TextArea rows={2} maxLength={1000} showCount /></Form.Item>
        <Form.Item name="notes" label="备注"><Input.TextArea rows={4} maxLength={2000} showCount /></Form.Item>
      </Form>
    </Drawer>
  )
}
