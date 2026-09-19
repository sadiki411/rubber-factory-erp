import { CalculatorOutlined } from '@ant-design/icons'
import { Alert, App, Button, Card, Checkbox, Col, Drawer, Form, Input, InputNumber, Row, Select, Space, Statistic, Tag, Typography } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { moldApi, orderApi, productionApi, toList } from '../api/client'
import type { MoldAsset, ProductionRun } from '../types'
import { moldModelOf } from '../types'

interface Props {
  open: boolean
  run?: ProductionRun
  initialDraft?: Record<string, any>
  onClose: () => void
  onSaved?: (run: ProductionRun) => void
}

function stationLabel(group?: string, position?: number, code?: string) {
  return group && position ? `${group}组 · ${position}号机台` : code ? `${code}号机台` : '未指定机台'
}

export function ProductionLedgerTaskDrawer({ open, run, initialDraft, onClose, onSaved }: Props) {
  const [form] = Form.useForm<Record<string, any>>()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const watchedOrderIds = Form.useWatch<number[]>('order_ids', form)
  const selectedOrderIds = watchedOrderIds || []
  const watchedOrderTargets = Form.useWatch<Record<number, number>>('order_targets', { form, preserve: true })
  const orderTargets = watchedOrderTargets || {}
  const moldId = Form.useWatch<number>('mold_id', form)
  const cavities = Number(Form.useWatch<number>('cavities', form) || 0)
  const defectMode = Form.useWatch<'RATE' | 'QUANTITY'>('estimated_defect_mode', form) || 'RATE'
  const defectRate = Number(Form.useWatch<number>('estimated_defect_rate', form) || 0)
  const defectQuantity = Number(Form.useWatch<number>('estimated_defect_quantity', form) || 0)

  const ordersQuery = useQuery({
    queryKey: ['orders', 'production-ledger-options'],
    queryFn: async () => toList(await orderApi.list({ page_size: 1000 })),
    enabled: open,
  })
  const stationsQuery = useQuery({
    queryKey: ['production', 'stations'],
    queryFn: async () => toList(await productionApi.stations()),
    enabled: open,
  })
  const moldsQuery = useQuery({
    queryKey: ['molds', 'production-ledger-options'],
    queryFn: async () => toList(await moldApi.list({ page_size: 1000 })),
    enabled: open,
  })

  const selectedOrders = selectedOrderIds
    .map((id) => ordersQuery.data?.find((item) => item.id === id) || (run?.order?.id === id ? run.order : undefined))
    .filter((item): item is NonNullable<typeof item> => Boolean(item))
    .sort((left, right) => (left.due_date || '9999-12-31').localeCompare(right.due_date || '9999-12-31'))
  const selectedOrder = selectedOrders[0]
  const selectedOrderSpecification = selectedOrder?.specification.trim().toLocaleLowerCase() || ''
  const selectedOrderProductName = selectedOrder?.product_name?.trim().toLocaleLowerCase() || ''
  const combinedOrderQuantity = selectedOrders.reduce((sum, item) => sum + Math.max(1, Number(orderTargets[item.id] || item.production_remaining_quantity || item.order_quantity || 1)), 0)
  const selectedMold = moldsQuery.data?.find((item) => item.id === moldId)
  const suggestedMolds = (() => {
    const all = moldsQuery.data || []
    if (!selectedOrder) return all
    return [...all].sort((left, right) => {
      const score = (mold: MoldAsset) => {
        const model = moldModelOf(mold)
        const text = `${model?.code || ''} ${model?.product_name || ''}`.toLocaleLowerCase()
        return Number(Boolean(selectedOrderSpecification && text.includes(selectedOrderSpecification))) * 2
          + Number(Boolean(selectedOrderProductName && text.includes(selectedOrderProductName)))
      }
      return score(right) - score(left)
    })
  })()

  let suggestedMoldCount: number | undefined
  if (selectedOrders.length > 0 && cavities >= 1) {
    const quantity = combinedOrderQuantity
    if (quantity >= 1) {
      suggestedMoldCount = defectMode === 'QUANTITY'
        ? Math.max(1, Math.ceil((quantity + Math.max(defectQuantity, 0)) / cavities))
        : Math.max(1, Math.ceil((quantity / cavities) * (1 + Math.max(defectRate, 0) / 100)))
    }
  }

  useEffect(() => {
    if (!open) return
    form.resetFields()
    const draftOrder = initialDraft?.order_no
      ? ordersQuery.data?.find((item) => item.order_no.trim().toLocaleLowerCase() === String(initialDraft.order_no).trim().toLocaleLowerCase()
        && (!initialDraft.item_no || item.item_no === String(initialDraft.item_no)))
      : undefined
    const allocations = run?.order_allocations?.length
      ? run.order_allocations
      : run?.order_id
        ? [{ order_id: run.order_id, planned_quantity: run.order_quantity }]
        : []
    const initialOrderIds = run ? allocations.map((item) => item.order_id) : (draftOrder ? [draftOrder.id] : [])
    const initialTargets = run
      ? Object.fromEntries(allocations.map((item) => [item.order_id, item.planned_quantity]))
      : draftOrder ? { [draftOrder.id]: Math.max(1, Number(draftOrder.production_remaining_quantity || draftOrder.order_quantity || 1)) } : {}
    form.setFieldsValue(run ? {
      ...run,
      order_id: run.order_id || run.order?.id,
      order_ids: initialOrderIds,
      order_targets: initialTargets,
      station_id: run.station_id || run.station?.id,
      mold_id: run.mold_id || run.mold?.id,
      estimated_defect_mode: run.estimated_defect_mode || 'RATE',
    } : {
      ...initialDraft,
      order_id: draftOrder?.id,
      order_ids: initialOrderIds,
      order_targets: initialTargets,
      estimated_defect_mode: 'RATE',
      estimated_defect_rate: 0,
      estimated_defect_quantity: 0,
    })
  }, [form, initialDraft, open, ordersQuery.data, run])

  const mutation = useMutation({
    mutationFn: async (values: Record<string, any>) => {
      const body = {
        ...values,
        is_ledger_only: true,
        order_id: values.order_id || selectedOrderIds[0] || null,
        selected_orders: selectedOrderIds.map((orderId) => ({
          order_id: orderId,
          planned_quantity: Math.max(1, Number(orderTargets[orderId] || 1)),
        })),
        station_id: values.station_id || null,
        mold_id: values.mold_id || null,
        planned_mold_count: values.planned_mold_count || suggestedMoldCount,
      }
      return run ? productionApi.updateRun(run.id, body) : productionApi.createRun(body)
    },
    onSuccess: async (saved) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['production'] }),
        queryClient.invalidateQueries({ queryKey: ['orders'] }),
      ])
      message.success(run ? '生产手工账任务已更新' : '生产手工账任务已建立')
      onSaved?.(saved)
      onClose()
    },
    onError: (error: Error) => message.error(error.message),
  })

  const updateCombinedOrderFields = (orderIds: number[], targets: Record<number, number>) => {
    const selected = orderIds
      .map((id) => ordersQuery.data?.find((item) => item.id === id) || (run?.order?.id === id ? run.order : undefined))
      .filter((item): item is NonNullable<typeof item> => Boolean(item))
    if (!selected.length) {
      form.setFieldsValue({ order_id: undefined, order_no: undefined, order_quantity: undefined, specification: undefined, material: undefined })
      return
    }
    const primary = [...selected].sort((left, right) => (left.due_date || '9999-12-31').localeCompare(right.due_date || '9999-12-31'))[0]
    const total = selected.reduce((sum, item) => sum + Math.max(1, Number(targets[item.id] || item.production_remaining_quantity || item.order_quantity || 1)), 0)
    form.setFieldsValue({
      order_id: primary.id,
      order_no: primary.order_no,
      specification: primary.specification,
      material: primary.material,
      order_quantity: total,
    })
  }

  const selectOrders = (orderIds: number[]) => {
    const nextTargets = { ...orderTargets }
    orderIds.forEach((id) => {
      if (nextTargets[id]) return
      const order = ordersQuery.data?.find((item) => item.id === id)
      if (order) nextTargets[id] = Math.max(1, Number(order.production_remaining_quantity || order.order_quantity || 1))
    })
    Object.keys(nextTargets).forEach((id) => {
      if (!orderIds.includes(Number(id))) delete nextTargets[Number(id)]
    })
    form.setFieldValue('order_targets', nextTargets)
    updateCombinedOrderFields(orderIds, nextTargets)
  }

  const selectMold = (id?: number) => {
    const mold = moldsQuery.data?.find((item) => item.id === id)
    if (mold?.default_cavities) form.setFieldValue('cavities', mold.default_cavities)
  }

  const submit = async () => mutation.mutate(await form.validateFields())

  const firstSelectedOrder = selectedOrders[0]
  const compatibleOrders = (() => {
    const currentIds = new Set(selectedOrderIds)
    return (ordersQuery.data || []).filter((item) => {
      if (currentIds.has(item.id)) return true
      if (item.status !== 'OPEN') return false
      if (!firstSelectedOrder) return true
      return item.specification.trim().toLocaleLowerCase() === selectedOrderSpecification
        && item.material.trim().toLocaleLowerCase() === (firstSelectedOrder.material.trim().toLocaleLowerCase())
    })
  })()

  return (
    <Drawer
      className="production-ledger-task-drawer"
      open={open}
      onClose={onClose}
      size={720}
      title={run ? `编辑生产任务 · ${run.order_no}` : '新增生产手工账任务'}
      footer={<Space className="drawer-footer-actions"><Button onClick={onClose}>取消</Button><Button type="primary" loading={mutation.isPending} onClick={() => void submit()}>保存任务</Button></Space>}
    >
      <Alert type="info" showIcon title="先建立任务，再按机台累计读数交接" description="只要求能确认订单和孔数；机台、具体模具、工艺参数都可留空以后补录。任务不会自动占用机台或改变模具状态。" />
      {initialDraft && <Alert className="production-ledger-order-alert" type="warning" showIcon title="以下内容来自照片识别，请逐项核对" description="订单号、孔数、目标模数等关键数字必须以纸质原件为准；系统不会把低置信结果直接写入。" />}
      <Form form={form} layout="vertical" requiredMark="optional">
        <div className="production-form-section">订单与孔数</div>
        <Form.Item name="order_ids" label="本次合并生产的订单" rules={[{ required: true, message: '请至少选择一个订单' }]} extra="先选择一个订单，之后只显示规格和材质相同的未完成订单；系统按交期先后分配合计产量。">
          <Select mode="multiple" showSearch optionFilterProp="label" loading={ordersQuery.isLoading} onChange={selectOrders} placeholder="可一次选择多个相同规格、相同材质订单" options={compatibleOrders.map((order) => ({ value: order.id, label: [order.order_no, order.item_no, order.due_date ? `交期 ${order.due_date}` : '未填交期', order.specification, order.material].filter(Boolean).join(' · ') }))} />
        </Form.Item>
        {selectedOrders.length > 0 && <Card size="small" title={`合并生产清单 · 共 ${selectedOrders.length} 个订单`} className="production-order-allocation-card">
          <Space direction="vertical" size={10} style={{ width: '100%' }}>
            {selectedOrders.map((order, index) => <div key={order.id} className="production-order-allocation-row">
              <div><Space wrap><Tag color="blue">交期顺序 {index + 1}</Tag><strong>{order.order_no}{order.item_no ? ` / ${order.item_no}` : ''}</strong></Space><Typography.Text type="secondary">交期 {order.due_date || '未填写'} · 原订单 {order.order_quantity} 件 · 当前剩余 {order.production_remaining_quantity ?? order.order_quantity} 件</Typography.Text></div>
              <InputNumber min={1} precision={0} value={orderTargets[order.id]} addonBefore="本次生产" addonAfter="件" onChange={(value) => {
                const next = { ...orderTargets, [order.id]: Math.max(1, Number(value || 1)) }
                form.setFieldValue('order_targets', next)
                updateCombinedOrderFields(selectedOrderIds, next)
              }} />
            </div>)}
          </Space>
        </Card>}
        <Row gutter={12}>
          <Col xs={24} sm={12}><Form.Item name="mold_id" label="具体实物模具（可后补）"><Select allowClear showSearch optionFilterProp="label" loading={moldsQuery.isLoading} onChange={selectMold} placeholder="不选模具也可直接填孔数" options={suggestedMolds.map((mold) => ({ value: mold.id, label: `${mold.asset_code} · ${moldModelOf(mold)?.code || '-'} · ${moldModelOf(mold)?.product_name || '-'}` }))} /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="cavities" label="本次有效孔数" rules={[{ required: true, message: '请输入本次有效孔数' }]}><InputNumber min={1} precision={0} style={{ width: '100%' }} placeholder="例如 6" /></Form.Item></Col>
        </Row>
        {selectedMold && <Form.Item name="save_cavities_as_mold_default" valuePropName="checked"><Checkbox>把本次孔数保存为模具 {selectedMold.asset_code} 的默认孔数</Checkbox></Form.Item>}

        <div className="production-form-section"><CalculatorOutlined /> 目标模数</div>
        <Row gutter={12}>
          <Col xs={24} sm={8}><Form.Item name="estimated_defect_mode" label="预估不良方式"><Select options={[{ value: 'RATE', label: '按百分比' }, { value: 'QUANTITY', label: '按件数' }]} /></Form.Item></Col>
          {defectMode === 'RATE' ? <Col xs={24} sm={8}><Form.Item name="estimated_defect_rate" label="预估不良率(%)"><InputNumber min={0} precision={2} style={{ width: '100%' }} /></Form.Item></Col> : <Col xs={24} sm={8}><Form.Item name="estimated_defect_quantity" label="预估不良件数"><InputNumber min={0} precision={0} style={{ width: '100%' }} /></Form.Item></Col>}
          <Col xs={24} sm={8}><Form.Item name="planned_mold_count" label="目标模数（可调整）"><InputNumber min={1} precision={0} placeholder={suggestedMoldCount ? `建议 ${suggestedMoldCount}` : undefined} style={{ width: '100%' }} /></Form.Item></Col>
        </Row>
        {suggestedMoldCount && <div className="production-ledger-target"><Statistic title="系统建议目标模数" value={suggestedMoldCount} suffix="模" /></div>}

        <div className="production-form-section">可后补资料</div>
        <Row gutter={12}>
          <Col xs={24} sm={12}><Form.Item name="station_id" label="机台（6台中选填）"><Select allowClear loading={stationsQuery.isLoading} placeholder="未确定可留空" options={(stationsQuery.data || []).map((station) => ({ value: station.id, label: stationLabel(station.group, station.position_no, station.code) }))} /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="compound_size" label="胶料尺寸"><Input /></Form.Item></Col>
          <Col xs={12} sm={8}><Form.Item name="strip_weight_kg" label="条重(kg)"><InputNumber min={0} precision={3} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={12} sm={8}><Form.Item name="strips_per_batch" label="条数"><InputNumber min={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={24} sm={8}><Form.Item name="curing_seconds" label="硫化时间(秒)"><InputNumber min={0} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
        </Row>
        <Form.Item name="notes" label="备注"><Input.TextArea rows={3} /></Form.Item>
      </Form>
    </Drawer>
  )
}
