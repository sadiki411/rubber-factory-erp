import { Alert, App, Button, Checkbox, Col, DatePicker, Drawer, Form, Input, InputNumber, Row, Select, Space, Switch } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import dayjs, { type Dayjs } from 'dayjs'
import { useEffect, useRef, useState } from 'react'
import { orderApi, qualityApi, toList } from '../api/client'
import { reworkQuantitiesValid, shipmentQuantitiesMatch, shipmentQuantityAllowed } from '../quality'
import type { QualityEmployee, QualityOrder, QualityProcessCard, QualityShipment, QualityShipmentBatch, ReturnRework } from '../types'
import { QualityWeightShipmentDrawer } from './QualityWeightShipmentDrawer'

// Keep this additive source field local while the import worker owns types.ts.
function orderSourceSystem(order?: QualityOrder) {
  return (order as (QualityOrder & { source_system?: string }) | undefined)?.source_system
}

interface BaseDrawerProps {
  open: boolean
  onClose: () => void
}

interface ShipmentDrawerProps extends BaseDrawerProps {
  mode?: 'PIECES' | 'WEIGHTED'
  shipment?: QualityShipment
  orders: QualityOrder[]
  employees: QualityEmployee[]
  processCards?: QualityProcessCard[]
  existingShipments?: QualityShipment[]
  existingBatches?: QualityShipmentBatch[]
  resetKey?: string | number
  onSubmit?: (payload: import('../types').QualityShipmentBatchInput) => Promise<unknown>
  onSaved?: (result?: unknown) => void | Promise<void>
}

function LegacyQualityShipmentDrawer({ open, shipment, orders, employees, onClose, onSaved }: ShipmentDrawerProps) {
  const [form] = Form.useForm<Record<string, any>>()
  const queryClient = useQueryClient()
  const { message } = App.useApp()
  const submitting = useRef(false)
  const clientRequestId = useRef<string | undefined>(undefined)
  const [orderSearch, setOrderSearch] = useState('')
  const orderOptionsQuery = useQuery({
    queryKey: ['orders', 'piece-shipment-options', orderSearch],
    queryFn: async () => toList(await orderApi.list({ q: orderSearch, page_size: 1000 })),
    enabled: open,
  })
  const orderOptions = [...new Map([...orders, ...(shipment?.order ? [shipment.order] : []), ...(orderOptionsQuery.data || [])].map((item) => [item.id, item])).values()]
  const orderId = Form.useWatch('order_id', form) ?? shipment?.order?.id
  const selectedOrder = orderOptions.find((item) => String(item.id) === String(orderId))
  const optionalEmployees = orderSourceSystem(selectedOrder) === '艾斯霖'
  const directPieces = !shipment
  const closeDrawer = () => {
    setOrderSearch('')
    onClose()
  }

  useEffect(() => {
    if (!open) return
    clientRequestId.current = shipment ? undefined : crypto.randomUUID()
    form.resetFields()
    form.setFieldsValue(shipment ? {
      ...shipment,
      shipment_date: dayjs(shipment.shipment_date),
      order_id: shipment.order_id || shipment.order?.id,
      inspector_id: shipment.inspector_id || shipment.inspector?.id,
    } : { shipment_date: dayjs(), shipment_no: `QS-COUNT-${dayjs().format('YYYYMMDD')}-${crypto.randomUUID()}`.toUpperCase(), confirmed_qualified_pieces: false })
  }, [form, open, shipment])

  const mutation = useMutation({
    mutationFn: (values: Record<string, any>) => {
      const body: Record<string, unknown> = { ...values, shipment_date: (values.shipment_date as Dayjs).format('YYYY-MM-DD') }
      if (!shipment) body.client_request_id = clientRequestId.current
      delete body.confirmed_qualified_pieces
      if (directPieces) Object.assign(body, { inspection_quantity: values.shipped_quantity, qualified_quantity: values.shipped_quantity, defective_quantity: 0 })
      body.inspector_id = values.inspector_id || null
      return shipment ? qualityApi.updateShipment(shipment.id, body) : qualityApi.createShipment(body)
    },
    onSuccess: (saved) => {
      message.success(shipment ? '件数出货记录已更新' : '件数出货记录已保存')
      closeDrawer()
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ['quality'] }),
        queryClient.invalidateQueries({ queryKey: ['analytics'] }),
        queryClient.invalidateQueries({ queryKey: ['orders'] }),
        queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
        Promise.resolve().then(() => onSaved?.(saved)),
      ]).catch(() => message.warning('出货已保存，部分页面刷新失败，请手动刷新。'))
    },
    onError: (error: Error) => message.error(error.message),
  })

  const submit = async () => {
    if (submitting.current) return
    submitting.current = true
    try { await mutation.mutateAsync(await form.validateFields()) } catch { /* Form/API errors are shown in place. */ }
    finally { submitting.current = false }
  }
  return (
    <Drawer
      open={open}
      onClose={closeDrawer}
      size={680}
      title={shipment ? `编辑出货 · ${shipment.shipment_no}` : '按采购单件数出货（无需流程卡）'}
      footer={<Space className="drawer-footer-actions"><Button onClick={closeDrawer}>取消</Button><Button type="primary" loading={mutation.isPending} onClick={() => void submit()}>保存</Button></Space>}
    >
      <Alert className="quality-form-alert" type="info" showIcon title="直接选择采购单按件数出货，无需流程卡或重量。" description="只关联所选订单。艾斯霖按采购单有效剩余件数校验，退货后可再次出货；员工选填，不会自动新增。" />
      {orderOptionsQuery.isError && <Alert type="error" message="采购单读取失败" description={(orderOptionsQuery.error as Error).message} action={<Button onClick={() => void orderOptionsQuery.refetch()}>重试</Button>} />}
      <Form form={form} layout="vertical" requiredMark="optional">
        <Row gutter={14}>
          <Col xs={24} sm={12}><Form.Item name="shipment_no" label="出货单号" extra={!shipment ? '系统已生成，可修改；留空也会自动生成。' : undefined} rules={shipment ? [{ required: true, whitespace: true, message: '请输入出货单号' }] : []}><Input placeholder="留空由系统自动生成" /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="shipment_date" label="出货日期" rules={[{ required: true, message: '请选择出货日期' }]}><DatePicker style={{ width: '100%' }} /></Form.Item></Col>
        </Row>
        <Form.Item name="order_id" label="采购单 / 项次" rules={[{ required: true, message: '请选择采购单项次' }]}>
          <Select showSearch virtual={false} optionRender={(option) => <div style={{ whiteSpace: 'normal', overflowWrap: 'anywhere' }}>{option.label}</div>} onSearch={setOrderSearch} optionFilterProp="label" loading={orderOptionsQuery.isFetching} placeholder="搜索并选择采购单项次" disabled={Boolean(shipment?.returned_quantity)} options={orderOptions.map((item) => ({ value: item.id, label: [orderSourceSystem(item), item.order_no, item.item_no, item.product_name, item.specification, item.material, `采购 ${item.order_quantity} 件`].filter(Boolean).join(' · ') }))} />
        </Form.Item>
        <Form.Item name="inspector_id" label={optionalEmployees ? '责任品检员（选填）' : '责任品检员'} rules={[{ required: !optionalEmployees, message: '请选择品检员' }]}>
          <Select allowClear showSearch optionFilterProp="label" placeholder="可选择现有品检员" options={employees.filter((item) => item.is_active && ['INSPECTOR', 'BOTH'].includes(item.role)).map((item) => ({ value: item.id, label: item.name }))} />
        </Form.Item>
        {directPieces ? <><Form.Item name="shipped_quantity" label="本次出货件数" rules={[{ required: true, type: 'number', min: 1, message: '请输入大于 0 的整数件数' }]}><InputNumber min={1} precision={0} style={{ width: '100%' }} /></Form.Item>
          <Form.Item name="confirmed_qualified_pieces" valuePropName="checked" rules={[{ validator: (_, value) => value === true ? Promise.resolve() : Promise.reject(new Error('请确认本次出货成品的品检及数量口径')) }]}><Checkbox>确认本次出货均为已品检合格的包装成品，按本次出货件数登记检验/合格数量，不包含其他未出货或不良件数</Checkbox></Form.Item></> :
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
        </Row>}
        <Form.Item name="notes" label="备注"><Input.TextArea rows={3} maxLength={500} showCount placeholder="可记录包装、交接或出货异常" /></Form.Item>
      </Form>
    </Drawer>
  )
}

/**
 * Keep the existing weighted default and explicitly route piece-only entry.
 */
export function QualityShipmentDrawer(props: ShipmentDrawerProps) {
  if (!props.shipment && props.mode !== 'PIECES') {
    return <QualityWeightShipmentDrawer
      open={props.open}
      orders={props.orders}
      employees={props.employees}
      processCards={props.processCards}
      existingShipments={props.existingShipments}
      existingBatches={props.existingBatches}
      resetKey={props.resetKey}
      onSubmit={props.onSubmit}
      onSaved={props.onSaved}
      onClose={props.onClose}
    />
  }
  return <LegacyQualityShipmentDrawer {...props} />
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
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['quality'] }),
        queryClient.invalidateQueries({ queryKey: ['analytics'] }),
      ])
      message.success(employee ? '员工档案已更新' : '员工档案已创建')
      onClose()
    },
    onError: (error: Error) => message.error(error.message),
  })

  const submit = async () => mutation.mutate(await form.validateFields())

  return (
    <Drawer open={open} onClose={onClose} size={520} title={employee ? `编辑员工 · ${employee.name}` : '新增员工档案'} footer={<Space className="drawer-footer-actions"><Button onClick={onClose}>取消</Button><Button type="primary" loading={mutation.isPending} onClick={() => void submit()}>保存</Button></Space>}>
      <Form form={form} layout="vertical" requiredMark="optional">
        <Form.Item name="employee_no" label="员工工号（选填）" extra={employee ? '已有员工的工号不能清空；可以修改为其他唯一编号。' : '不填写时由系统自动生成，保存后仍可编辑。'}><Input placeholder="可留空自动生成" /></Form.Item>
        <Form.Item name="name" label="姓名" rules={[{ required: true, whitespace: true, message: '请输入员工姓名' }]}><Input /></Form.Item>
        <Form.Item name="phone" label="手机号/联系电话"><Input placeholder="选填" /></Form.Item>
        <Form.Item name="team" label="班组"><Input placeholder="例如 品检一组" /></Form.Item>
        <Form.Item name="role" label="品检/返工岗位" rules={[{ required: true }]}><Select options={[{ value: 'PRODUCTION', label: '仅前端生产' }, { value: 'INSPECTOR', label: '品检员' }, { value: 'REWORKER', label: '返工员' }, { value: 'BOTH', label: '品检兼返工' }]} /></Form.Item>
        <Form.Item name="production_enabled" label="前端生产岗位" valuePropName="checked" extra="一个人可以同时拥有生产、品检或返工岗位；勾选后会出现在生产手工账员工选择中。"><Switch checkedChildren="可参与生产" unCheckedChildren="不参与生产" /></Form.Item>
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
  const submitting = useRef(false)
  const clientRequestId = useRef<string | undefined>(undefined)
  const [shipmentSearch, setShipmentSearch] = useState('')
  const shipmentOptionsQuery = useQuery({
    queryKey: ['quality', 'piece-return-options', shipmentSearch],
    queryFn: async () => toList(await qualityApi.listShipments({ q: shipmentSearch, page_size: 1000 })),
    enabled: open,
  })
  const shipmentOptions = [...new Map([...shipments, ...(rework?.shipment ? [rework.shipment] : []), ...(shipmentOptionsQuery.data || [])].map((item) => [item.id, item])).values()]
  const selectedShipmentId = Form.useWatch('shipment_id', form) ?? rework?.shipment_id ?? rework?.shipment?.id
  const selectedShipment = shipmentOptions.find((item) => String(item.id) === String(selectedShipmentId))
  const optionalEmployees = orderSourceSystem(selectedShipment?.order) === '艾斯霖'
  const inheritedInspector = selectedShipment?.inspector || selectedShipment?.inspectors?.[0]

  useEffect(() => {
    if (!open) return
    clientRequestId.current = rework ? undefined : crypto.randomUUID()
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
      reason_category: 'OTHER',
    })
  }, [form, open, rework])

  const mutation = useMutation({
    mutationFn: (values: Record<string, any>) => {
      const body: Record<string, unknown> = { ...values, responsible_inspector_id: inheritedInspector?.id || values.responsible_inspector_id || null, rework_employee_id: values.rework_employee_id || null, rework_date: (values.rework_date as Dayjs).format('YYYY-MM-DD') }
      if (!rework) body.client_request_id = clientRequestId.current
      return rework ? qualityApi.updateRework(rework.id, body) : qualityApi.createRework(body)
    },
    onSuccess: () => {
      message.success(rework ? '退货返工记录已更新' : '退货返工记录已保存')
      onClose()
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ['quality'] }),
        queryClient.invalidateQueries({ queryKey: ['analytics'] }),
        queryClient.invalidateQueries({ queryKey: ['orders'] }),
        queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
      ]).catch(() => message.warning('退货已保存，部分页面刷新失败，请手动刷新。'))
    },
    onError: (error: Error) => message.error(error.message),
  })

  const submit = async () => {
    if (submitting.current) return
    submitting.current = true
    try { await mutation.mutateAsync(await form.validateFields()) } catch { /* Form/API errors are shown in place. */ }
    finally { submitting.current = false }
  }
  const selectShipment = (shipmentId: number) => {
    const selected = shipmentOptions.find((item) => String(item.id) === String(shipmentId))
    const inspector = selected?.inspector || selected?.inspectors?.[0]
    form.setFieldValue('responsible_inspector_id', inspector?.id)
  }

  return (
    <Drawer open={open} onClose={onClose} size={720} title={rework ? `编辑退货返工 · ${rework.shipment?.shipment_no}` : '登记退货返工'} footer={<Space className="drawer-footer-actions"><Button onClick={onClose}>取消</Button><Button type="primary" loading={mutation.isPending} onClick={() => void submit()}>保存</Button></Space>}>
      <Alert className="quality-form-alert" type="info" showIcon title="按原件数出货登记退货，回冲原采购单有效交付。" description="无需流程卡或重量，艾斯霖员工选填。返工完成不会自动增加交付，需从件数出货入口再次登记。" />
      {shipmentOptionsQuery.isError && <Alert type="error" message="原出货查询失败" action={<Button onClick={() => void shipmentOptionsQuery.refetch()}>重试</Button>} />}
      <Form form={form} layout="vertical" requiredMark="optional">
        <Row gutter={14}>
          <Col xs={24} sm={14}><Form.Item name="shipment_id" label="原件数出货记录" rules={[{ required: true, message: '请选择原出货记录' }]}><Select showSearch virtual={false} optionRender={(option) => <div style={{ whiteSpace: 'normal', overflowWrap: 'anywhere' }}>{option.label}</div>} onSearch={setShipmentSearch} loading={shipmentOptionsQuery.isFetching} optionFilterProp="label" onChange={selectShipment} disabled={Boolean(rework)} options={shipmentOptions.map((item) => ({ value: item.id, label: [item.shipment_no, item.order?.order_no, item.order?.item_no, item.order?.product_name, item.order?.specification, item.order?.material, `可退 ${Math.max(0, item.shipped_quantity - (item.returned_quantity || 0) + (rework?.shipment?.id === item.id ? rework.returned_quantity : 0))} 件`].filter(Boolean).join(' · ') }))} /></Form.Item></Col>
          <Col xs={24} sm={10}><Form.Item name="rework_date" label="退货 / 返工日期" rules={[{ required: true }]}><DatePicker style={{ width: '100%' }} /></Form.Item></Col>
        </Row>
        <Row gutter={14}>
          <Col xs={24} sm={12}>{inheritedInspector ? <Alert
            type="success"
            showIcon
            message="责任品检员已从原出货自动带入"
            description={`${inheritedInspector.name}；无需在退货时重复选择。`}
          /> : <Form.Item name="responsible_inspector_id" label="责任品检员（艾斯霖选填）" rules={[{ required: !optionalEmployees, message: '请选择责任品检员' }]}><Select allowClear options={employees.filter((item) => item.is_active && ['INSPECTOR', 'BOTH'].includes(item.role)).map((item) => ({ value: item.id, label: item.name }))} /></Form.Item>}</Col>
          <Col xs={24} sm={12}><Form.Item name="rework_employee_id" label="返工处理人（艾斯霖选填）" rules={[{ required: !optionalEmployees, message: '请选择返工处理人' }]}><Select allowClear options={employees.filter((item) => item.is_active && ['REWORKER', 'BOTH'].includes(item.role)).map((item) => ({ value: item.id, label: item.name }))} /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="reason_category" label="原因分类" rules={[{ required: true }]}><Select options={[{ value: 'APPEARANCE', label: '外观' }, { value: 'STICKING', label: '粘皮' }, { value: 'DIMENSION', label: '尺寸' }, { value: 'MATERIAL', label: '材料' }, { value: 'MIXED', label: '混料' }, { value: 'PACKAGING', label: '包装' }, { value: 'OTHER', label: '其他' }]} /></Form.Item></Col>
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
