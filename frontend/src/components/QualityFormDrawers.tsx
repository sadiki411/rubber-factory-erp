import { Alert, App, Button, Col, DatePicker, Drawer, Form, Input, InputNumber, Row, Select, Space, Switch } from 'antd'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import dayjs, { type Dayjs } from 'dayjs'
import { useEffect } from 'react'
import { qualityApi } from '../api/client'
import { reworkQuantitiesValid, shipmentQuantitiesMatch, shipmentQuantityAllowed } from '../quality'
import type { QualityEmployee, QualityOrder, QualityShipment, ReturnRework } from '../types'

interface BaseDrawerProps {
  open: boolean
  onClose: () => void
}

interface ShipmentDrawerProps extends BaseDrawerProps {
  shipment?: QualityShipment
  orders: QualityOrder[]
  employees: QualityEmployee[]
}

export function QualityShipmentDrawer({ open, shipment, orders, employees, onClose }: ShipmentDrawerProps) {
  const [form] = Form.useForm<Record<string, any>>()
  const queryClient = useQueryClient()
  const { message } = App.useApp()

  useEffect(() => {
    if (!open) return
    form.resetFields()
    form.setFieldsValue(shipment ? {
      ...shipment,
      shipment_date: dayjs(shipment.shipment_date),
      order_id: shipment.order_id || shipment.order?.id,
      inspector_id: shipment.inspector_id || shipment.inspector?.id,
    } : { shipment_date: dayjs() })
  }, [form, open, shipment])

  const mutation = useMutation({
    mutationFn: (values: Record<string, any>) => {
      const body = { ...values, shipment_date: (values.shipment_date as Dayjs).format('YYYY-MM-DD') }
      return shipment ? qualityApi.updateShipment(shipment.id, body) : qualityApi.createShipment(body)
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['quality'] })
      message.success(shipment ? '出货记录已更新' : '出货记录已保存')
      onClose()
    },
    onError: (error: Error) => message.error(error.message),
  })

  const submit = async () => mutation.mutate(await form.validateFields())
  const inspectors = employees.filter((item) => item.is_active && ['INSPECTOR', 'BOTH'].includes(item.role))

  return (
    <Drawer
      open={open}
      onClose={onClose}
      size={680}
      title={shipment ? `编辑出货 · ${shipment.shipment_no}` : '新增出货记录'}
      footer={<Space className="drawer-footer-actions"><Button onClick={onClose}>取消</Button><Button type="primary" loading={mutation.isPending} onClick={() => void submit()}>保存</Button></Space>}
    >
      <Alert className="quality-form-alert" type="info" showIcon title="出货数量不能超过合格数量，质检数量必须等于合格数量与不良数量之和。" />
      <Form form={form} layout="vertical" requiredMark="optional">
        <Row gutter={14}>
          <Col xs={24} sm={12}><Form.Item name="shipment_no" label="出货单号" rules={[{ required: true, whitespace: true, message: '请输入出货单号' }]}><Input placeholder="例如 CK-202607-001" /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="shipment_date" label="出货日期" rules={[{ required: true, message: '请选择出货日期' }]}><DatePicker style={{ width: '100%' }} /></Form.Item></Col>
        </Row>
        <Form.Item name="order_id" label="订单 / 批次" rules={[{ required: true, message: '请选择订单批次' }]}>
          <Select showSearch optionFilterProp="label" placeholder="选择订单批次" options={orders.map((item) => ({ value: item.id, label: [item.order_no, item.batch_no, item.product_name || item.specification].filter(Boolean).join(' · ') }))} />
        </Form.Item>
        <Form.Item name="inspector_id" label="责任品检员" rules={[{ required: true, message: '请选择品检员' }]}>
          <Select showSearch optionFilterProp="label" placeholder="选择品检员" options={inspectors.map((item) => ({ value: item.id, label: `${item.employee_no} · ${item.name}${item.team ? ` · ${item.team}` : ''}` }))} />
        </Form.Item>
        <Row gutter={14}>
          <Col xs={12} sm={6}><Form.Item name="inspection_quantity" label="质检数量" rules={[{ required: true, type: 'number', min: 1, message: '质检数量必须大于 0' }]}><InputNumber min={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={12} sm={6}><Form.Item name="qualified_quantity" label="合格数量" rules={[{ required: true, message: '请输入合格数量' }]}><InputNumber min={0} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={12} sm={6}>
            <Form.Item
              name="defective_quantity"
              label="不良数量"
              dependencies={['inspection_quantity', 'qualified_quantity']}
              rules={[
                { required: true, message: '请输入不良数量' },
                {
                  validator: (_, value) => shipmentQuantitiesMatch(
                    Number(form.getFieldValue('inspection_quantity')),
                    Number(form.getFieldValue('qualified_quantity')),
                    Number(value),
                  ) ? Promise.resolve() : Promise.reject(new Error('质检数量必须等于合格数量 + 不良数量')),
                },
              ]}
            >
              <InputNumber min={0} precision={0} style={{ width: '100%' }} />
            </Form.Item>
          </Col>
          <Col xs={12} sm={6}>
            <Form.Item
              name="shipped_quantity"
              label="实际出货"
              dependencies={['qualified_quantity']}
              rules={[
                { required: true, type: 'number', min: 1, message: '出货数量必须大于 0' },
                {
                  validator: (_, value) => shipmentQuantityAllowed(Number(value), Number(form.getFieldValue('qualified_quantity')))
                    ? Promise.resolve()
                    : Promise.reject(new Error('出货数量不能超过合格数量')),
                },
              ]}
            >
              <InputNumber min={1} precision={0} style={{ width: '100%' }} />
            </Form.Item>
          </Col>
        </Row>
        <Form.Item name="notes" label="备注"><Input.TextArea rows={3} maxLength={500} showCount placeholder="可记录包装、交接或出货异常" /></Form.Item>
      </Form>
    </Drawer>
  )
}

interface OrderDrawerProps extends BaseDrawerProps {
  order?: QualityOrder
}

export function QualityOrderDrawer({ open, order, onClose }: OrderDrawerProps) {
  const [form] = Form.useForm<Record<string, any>>()
  const queryClient = useQueryClient()
  const { message } = App.useApp()

  useEffect(() => {
    if (!open) return
    form.resetFields()
    form.setFieldsValue(order ? {
      ...order,
      order_date: dayjs(order.order_date),
      due_date: order.due_date ? dayjs(order.due_date) : undefined,
    } : { order_date: dayjs(), status: 'OPEN' })
  }, [form, open, order])

  const mutation = useMutation({
    mutationFn: (values: Record<string, any>) => {
      const body = {
        ...values,
        order_date: (values.order_date as Dayjs).format('YYYY-MM-DD'),
        due_date: values.due_date ? (values.due_date as Dayjs).format('YYYY-MM-DD') : null,
      }
      return order ? qualityApi.updateOrder(order.id, body) : qualityApi.createOrder(body)
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['quality'] })
      message.success(order ? '订单资料已更新' : '订单批次已创建')
      onClose()
    },
    onError: (error: Error) => message.error(error.message),
  })

  const submit = async () => mutation.mutate(await form.validateFields())

  return (
    <Drawer open={open} onClose={onClose} size={680} title={order ? `编辑订单 · ${order.order_no}` : '新增订单批次'} footer={<Space className="drawer-footer-actions"><Button onClick={onClose}>取消</Button><Button type="primary" loading={mutation.isPending} onClick={() => void submit()}>保存</Button></Space>}>
      <Form form={form} layout="vertical" requiredMark="optional">
        <Row gutter={14}>
          <Col xs={24} sm={12}><Form.Item name="order_no" label="订单编号" rules={[{ required: true, whitespace: true, message: '请输入订单编号' }]}><Input /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="batch_no" label="批次号"><Input placeholder="无批次号时可留空" /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="product_code" label="产品编码"><Input placeholder="可选" /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="product_name" label="产品名称" rules={[{ required: true, whitespace: true, message: '请输入产品名称' }]}><Input /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="specification" label="规格" rules={[{ required: true, whitespace: true, message: '请输入规格' }]}><Input /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="material" label="材质 / 胶料" rules={[{ required: true, whitespace: true, message: '请输入材质' }]}><Input /></Form.Item></Col>
          <Col xs={12} sm={8}><Form.Item name="order_quantity" label="订单数量" rules={[{ required: true, message: '请输入订单数量' }]}><InputNumber min={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={12} sm={8}><Form.Item name="mold_size" label="模具尺寸"><Input /></Form.Item></Col>
          <Col xs={24} sm={8}><Form.Item name="status" label="订单状态" rules={[{ required: true }]}><Select options={[{ value: 'OPEN', label: '进行中' }, { value: 'COMPLETED', label: '已完成' }, { value: 'CANCELLED', label: '已取消' }]} /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="order_date" label="订单日期" rules={[{ required: true, message: '请选择订单日期' }]}><DatePicker style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="due_date" label="交付日期"><DatePicker style={{ width: '100%' }} /></Form.Item></Col>
        </Row>
        <Form.Item name="notes" label="备注"><Input.TextArea rows={3} maxLength={500} showCount /></Form.Item>
      </Form>
    </Drawer>
  )
}

interface EmployeeDrawerProps extends BaseDrawerProps {
  employee?: QualityEmployee
}

export function QualityEmployeeDrawer({ open, employee, onClose }: EmployeeDrawerProps) {
  const [form] = Form.useForm<Record<string, any>>()
  const queryClient = useQueryClient()
  const { message } = App.useApp()

  useEffect(() => {
    if (!open) return
    form.resetFields()
    form.setFieldsValue(employee || { role: 'INSPECTOR', is_active: true })
  }, [employee, form, open])

  const mutation = useMutation({
    mutationFn: (values: Record<string, any>) => employee ? qualityApi.updateEmployee(employee.id, values) : qualityApi.createEmployee(values),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['quality'] })
      message.success(employee ? '员工档案已更新' : '员工档案已创建')
      onClose()
    },
    onError: (error: Error) => message.error(error.message),
  })

  const submit = async () => mutation.mutate(await form.validateFields())

  return (
    <Drawer open={open} onClose={onClose} size={520} title={employee ? `编辑员工 · ${employee.name}` : '新增员工档案'} footer={<Space className="drawer-footer-actions"><Button onClick={onClose}>取消</Button><Button type="primary" loading={mutation.isPending} onClick={() => void submit()}>保存</Button></Space>}>
      <Form form={form} layout="vertical" requiredMark="optional">
        <Form.Item name="employee_no" label="员工工号" rules={[{ required: true, whitespace: true, message: '请输入员工工号' }]}><Input placeholder="必须唯一" /></Form.Item>
        <Form.Item name="name" label="姓名" rules={[{ required: true, whitespace: true, message: '请输入员工姓名' }]}><Input /></Form.Item>
        <Form.Item name="team" label="班组"><Input placeholder="例如 品检一组" /></Form.Item>
        <Form.Item name="role" label="岗位角色" rules={[{ required: true }]}><Select options={[{ value: 'INSPECTOR', label: '品检员' }, { value: 'REWORKER', label: '返工员' }, { value: 'BOTH', label: '品检兼返工' }]} /></Form.Item>
        <Form.Item name="is_active" label="状态" valuePropName="checked"><Switch checkedChildren="启用" unCheckedChildren="停用" /></Form.Item>
        <Form.Item name="notes" label="备注"><Input.TextArea rows={3} maxLength={500} showCount /></Form.Item>
      </Form>
    </Drawer>
  )
}

interface ReworkDrawerProps extends BaseDrawerProps {
  rework?: ReturnRework
  shipments: QualityShipment[]
  employees: QualityEmployee[]
}

export function QualityReworkDrawer({ open, rework, shipments, employees, onClose }: ReworkDrawerProps) {
  const [form] = Form.useForm<Record<string, any>>()
  const queryClient = useQueryClient()
  const { message } = App.useApp()

  useEffect(() => {
    if (!open) return
    form.resetFields()
    form.setFieldsValue(rework ? {
      ...rework,
      shipment_id: rework.shipment_id || rework.shipment?.id,
      responsible_inspector_id: rework.responsible_inspector_id || rework.responsible_inspector?.id,
      rework_employee_id: rework.rework_employee_id || rework.rework_employee?.id,
      rework_date: dayjs(rework.rework_date),
    } : {
      rework_date: dayjs(),
      status: 'PENDING',
      reworked_quantity: 0,
      recovered_quantity: 0,
      scrap_quantity: 0,
      work_hours: 0,
    })
  }, [form, open, rework])

  const mutation = useMutation({
    mutationFn: (values: Record<string, any>) => {
      const body = { ...values, rework_date: (values.rework_date as Dayjs).format('YYYY-MM-DD') }
      return rework ? qualityApi.updateRework(rework.id, body) : qualityApi.createRework(body)
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['quality'] })
      message.success(rework ? '退货返工记录已更新' : '退货返工记录已保存')
      onClose()
    },
    onError: (error: Error) => message.error(error.message),
  })

  const submit = async () => mutation.mutate(await form.validateFields())
  const inspectors = employees.filter((item) => item.is_active && ['INSPECTOR', 'BOTH'].includes(item.role))
  const reworkers = employees.filter((item) => item.is_active && ['REWORKER', 'BOTH'].includes(item.role))
  const selectShipment = (shipmentId: number) => {
    const selected = shipments.find((item) => item.id === shipmentId)
    if (selected?.inspector?.id) {
      form.setFieldValue('responsible_inspector_id', selected.inspector.id)
    }
  }

  return (
    <Drawer open={open} onClose={onClose} size={720} title={rework ? `编辑退货返工 · ${rework.shipment?.shipment_no}` : '登记退货返工'} footer={<Space className="drawer-footer-actions"><Button onClick={onClose}>取消</Button><Button type="primary" loading={mutation.isPending} onClick={() => void submit()}>保存</Button></Space>}>
      <Alert className="quality-form-alert" type="warning" showIcon title="责任品检员用于质量责任统计；返工处理人用于返工工作量统计，两者必须分别登记。" />
      <Form form={form} layout="vertical" requiredMark="optional">
        <Row gutter={14}>
          <Col xs={24} sm={14}><Form.Item name="shipment_id" label="原出货记录" rules={[{ required: true, message: '请选择原出货记录' }]}><Select showSearch optionFilterProp="label" onChange={selectShipment} options={shipments.map((item) => ({ value: item.id, label: [item.shipment_no, item.order?.order_no, item.order?.batch_no].filter(Boolean).join(' · ') }))} /></Form.Item></Col>
          <Col xs={24} sm={10}><Form.Item name="rework_date" label="退货 / 返工日期" rules={[{ required: true }]}><DatePicker style={{ width: '100%' }} /></Form.Item></Col>
        </Row>
        <Row gutter={14}>
          <Col xs={24} sm={12}><Form.Item name="responsible_inspector_id" label="责任品检员" rules={[{ required: true, message: '请选择责任品检员' }]}><Select showSearch optionFilterProp="label" options={inspectors.map((item) => ({ value: item.id, label: `${item.employee_no} · ${item.name}` }))} /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="rework_employee_id" label="返工处理人" rules={[{ required: true, message: '请选择返工处理人' }]}><Select showSearch optionFilterProp="label" options={reworkers.map((item) => ({ value: item.id, label: `${item.employee_no} · ${item.name}` }))} /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="reason_category" label="原因分类" rules={[{ required: true }]}><Select options={[{ value: 'APPEARANCE', label: '外观' }, { value: 'DIMENSION', label: '尺寸' }, { value: 'MATERIAL', label: '材料' }, { value: 'MIXED', label: '混料' }, { value: 'PACKAGING', label: '包装' }, { value: 'OTHER', label: '其他' }]} /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="status" label="处理状态" rules={[{ required: true }]}><Select options={[{ value: 'PENDING', label: '待处理' }, { value: 'PROCESSING', label: '处理中' }, { value: 'COMPLETED', label: '已完成' }]} /></Form.Item></Col>
        </Row>
        <Form.Item name="reason" label="退货 / 返工原因" rules={[{ required: true, whitespace: true, message: '请输入具体原因' }]}><Input.TextArea rows={2} maxLength={500} showCount /></Form.Item>
        <Row gutter={14}>
          <Col xs={12} sm={6}><Form.Item name="returned_quantity" label="退货数量" rules={[{ required: true, type: 'number', min: 1, message: '退货数量必须大于 0' }]}><InputNumber min={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={12} sm={6}><Form.Item name="reworked_quantity" label="返工数量" rules={[{ required: true }]}><InputNumber min={0} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={12} sm={6}><Form.Item name="recovered_quantity" label="返工合格" rules={[{ required: true }]}><InputNumber min={0} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={12} sm={6}>
            <Form.Item
              name="scrap_quantity"
              label="报废数量"
              dependencies={['returned_quantity', 'reworked_quantity', 'recovered_quantity']}
              rules={[
                { required: true },
                {
                  validator: (_, value) => reworkQuantitiesValid(
                    Number(form.getFieldValue('returned_quantity')),
                    Number(form.getFieldValue('reworked_quantity')),
                    Number(form.getFieldValue('recovered_quantity')),
                    Number(value),
                  ) ? Promise.resolve() : Promise.reject(new Error('须满足：合格 + 报废 ≤ 返工 ≤ 退货')),
                },
              ]}
            >
              <InputNumber min={0} precision={0} style={{ width: '100%' }} />
            </Form.Item>
          </Col>
          <Col xs={12} sm={8}><Form.Item name="work_hours" label="返工工时"><InputNumber min={0} precision={2} suffix="小时" style={{ width: '100%' }} /></Form.Item></Col>
        </Row>
        <Form.Item name="notes" label="备注"><Input.TextArea rows={3} maxLength={500} showCount /></Form.Item>
      </Form>
    </Drawer>
  )
}
