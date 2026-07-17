import { Alert, App, Button, Col, DatePicker, Drawer, Form, Input, Row, Select, Space } from 'antd'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import dayjs, { type Dayjs } from 'dayjs'
import { useEffect } from 'react'
import { materialReceiptApi } from '../api/client'
import type { MaterialReceipt, Order } from '../types'

interface Props {
  open: boolean
  receipt?: MaterialReceipt
  orders: Order[]
  ordersLoading?: boolean
  onClose: () => void
}

interface MaterialReceiptFormValues {
  order_id?: number | null
  order_no?: string
  item_no?: string
  finished_product_name?: string
  specification?: string
  material?: string
  batch_no?: string
  sheet_size?: string
  weight_kg?: string
  manufactured_on?: Dayjs | null
}

function orderOptionLabel(order: Order) {
  const identity = [order.order_no, order.item_no].filter(Boolean).join(' / ')
  const product = [order.product_name, order.specification, order.material].filter(Boolean).join(' · ')
  const quantity = order.order_quantity === null || order.order_quantity === undefined ? '' : `数量 ${order.order_quantity}`
  return [identity, product, quantity, `明细 #${order.id}`].filter(Boolean).join(' · ')
}

export function MaterialReceiptDrawer({ open, receipt, orders, ordersLoading = false, onClose }: Props) {
  const [form] = Form.useForm<MaterialReceiptFormValues>()
  const queryClient = useQueryClient()
  const { message } = App.useApp()
  const selectedOrderId = Form.useWatch('order_id', form)

  const matchingOrderIds = orders
    .filter((item) => {
      if (!receipt || item.order_no !== receipt.order_no) return false
      return !receipt.item_no || !item.item_no || item.item_no === receipt.item_no
    })
    .map((item) => item.id)
  const orderOptions = [...orders]
    .sort((left, right) => {
      const leftMatch = matchingOrderIds.includes(left.id) ? 1 : 0
      const rightMatch = matchingOrderIds.includes(right.id) ? 1 : 0
      return rightMatch - leftMatch || right.id - left.id
    })
    .map((item) => ({
      value: item.id,
      label: `${matchingOrderIds.includes(item.id) ? '建议匹配 · ' : ''}${orderOptionLabel(item)}`,
    }))

  useEffect(() => {
    if (!open) return
    form.resetFields()
    form.setFieldsValue(receipt ? {
      order_id: receipt.order_id || receipt.order?.id,
      order_no: receipt.order_no,
      item_no: receipt.item_no,
      finished_product_name: receipt.finished_product_name,
      specification: receipt.specification,
      material: receipt.material,
      batch_no: receipt.batch_no,
      sheet_size: receipt.sheet_size,
      weight_kg: String(receipt.weight_kg),
      manufactured_on: receipt.manufactured_on ? dayjs(receipt.manufactured_on) : undefined,
    } : {})
  }, [form, open, receipt])

  const mutation = useMutation({
    mutationFn: (values: MaterialReceiptFormValues) => {
      const body = {
        ...values,
        order_id: values.order_id ?? null,
        manufactured_on: values.manufactured_on ? values.manufactured_on.format('YYYY-MM-DD') : null,
      }
      return receipt ? materialReceiptApi.update(receipt.id, body) : materialReceiptApi.create(body)
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['material-receipts'] }),
        queryClient.invalidateQueries({ queryKey: ['orders'] }),
        queryClient.invalidateQueries({ queryKey: ['quality'] }),
        queryClient.invalidateQueries({ queryKey: ['analytics'] }),
      ])
      message.success(receipt ? '发料记录已更新，订单胶料状态已同步' : '发料记录已创建')
      onClose()
    },
    onError: (error: Error) => message.error(error.message),
  })

  const selectOrder = (id?: number) => {
    const selected = orders.find((item) => item.id === id)
    if (!selected) return
    form.setFieldsValue({
      order_no: selected.order_no,
      item_no: selected.item_no || '',
      finished_product_name: selected.product_name || form.getFieldValue('finished_product_name'),
      specification: selected.specification || form.getFieldValue('specification'),
      material: selected.material || form.getFieldValue('material'),
    })
  }

  const submit = () => {
    form.validateFields()
      .then((values) => mutation.mutate(values))
      .catch(() => undefined)
  }

  return (
    <Drawer
      className="business-data-drawer"
      open={open}
      onClose={onClose}
      size={680}
      title={receipt ? `编辑发料记录 · ${receipt.order_no || receipt.batch_no || receipt.id}` : '新增发料记录'}
      footer={<Space className="drawer-footer-actions"><Button onClick={onClose}>取消</Button><Button type="primary" loading={mutation.isPending} onClick={submit}>保存并同步订单</Button></Space>}
    >
      {!receipt?.order_id && !receipt?.order && receipt && (
        <Alert className="business-source-alert" type="warning" showIcon title="这条发料记录尚未关联订单" description="请选择具体订单明细。保存后重量会立即计入该订单的“已发胶料”，并重新计算是否发够。" />
      )}
      {receipt?.source_sheet && <Alert className="business-source-alert" type="info" showIcon title={`来源：${receipt.source_sheet}${receipt.source_row ? ` 第 ${receipt.source_row} 行` : ''}`} description="原始发料单内容仍保留，可在线修正关联和结构化字段。" />}
      <Form form={form} layout="vertical" requiredMark="optional">
        <div className="business-form-section">订单关联</div>
        {receipt && matchingOrderIds.length > 0 && !selectedOrderId && (
          <Alert
            className="business-form-hint"
            type="info"
            showIcon
            title={`找到 ${matchingOrderIds.length} 条可能匹配的订单明细`}
            description="候选项已排在下拉列表最前面；请结合项次、产品、规格、材质和数量确认，系统不会自动关联。"
          />
        )}
        <Form.Item name="order_id" label="关联订单明细" extra="选择后会同步订单号、项次及产品信息；清除选择可恢复为未关联状态。">
          <Select
            allowClear
            showSearch
            optionFilterProp="label"
            loading={ordersLoading}
            placeholder={ordersLoading ? '正在读取订单明细…' : '按订单号、项次、产品、规格或材质搜索'}
            notFoundContent={ordersLoading ? '正在读取…' : '没有可关联的订单明细'}
            onChange={selectOrder}
            options={orderOptions}
          />
        </Form.Item>
        <Row gutter={14}>
          <Col xs={24} sm={12}>
            <Form.Item name="order_no" label="发料单订单号" rules={[{ required: true, whitespace: true, message: '请输入订单号或先关联订单明细' }]} extra={selectedOrderId ? '已按关联订单锁定；如需修改，请先清除上方关联。' : undefined}>
              <Input readOnly={Boolean(selectedOrderId)} />
            </Form.Item>
          </Col>
          <Col xs={24} sm={12}><Form.Item name="item_no" label="项次"><Input readOnly={Boolean(selectedOrderId)} /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="finished_product_name" label="成品名称"><Input /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="specification" label="规格"><Input /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="material" label="材质 / 胶料"><Input /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="batch_no" label="胶料批次"><Input /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="sheet_size" label="片材尺寸"><Input /></Form.Item></Col>
          <Col xs={24} sm={12}>
            <Form.Item
              name="weight_kg"
              label="发料重量（kg）"
              rules={[
                { required: true, whitespace: true, message: '请输入发料重量' },
                { pattern: /^\d+(?:\.\d{1,3})?$/, message: '请输入不小于 0 且最多 3 位小数的重量' },
              ]}
            >
              <Input inputMode="decimal" placeholder="例如 12.500" />
            </Form.Item>
          </Col>
          <Col xs={24} sm={12}><Form.Item name="manufactured_on" label="制造 / 发料日期"><DatePicker style={{ width: '100%' }} /></Form.Item></Col>
        </Row>
      </Form>
    </Drawer>
  )
}
