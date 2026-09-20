import { CalculatorOutlined, ClockCircleOutlined } from '@ant-design/icons'
import { Alert, App, Button, Card, Checkbox, Col, DatePicker, Drawer, Form, Input, InputNumber, Row, Select, Space, Tag, Typography } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import dayjs, { type Dayjs } from 'dayjs'
import { useEffect, useMemo } from 'react'
import { moldApi, orderApi, productionApi, productSpecificationApi, toList } from '../api/client'
import { productionStationGroupLabel, productionStationNumber, requiresProductionUnloadTime } from '../production'
import type { ProductSpecification, ProductionMold, ProductionRun, ProductionRunStatus, ProductionStation } from '../types'
import { moldCode, moldLocation, moldModelOf } from '../types'

interface Props {
  open: boolean
  run?: ProductionRun
  station?: ProductionStation
  mountedMold?: ProductionMold
  initialStatus?: ProductionRunStatus
  onClose: () => void
  onSuccess?: (run: ProductionRun) => void
}

function asNumber(value: unknown, fallback = 0) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function plainNumber(value: unknown) {
  const text = String(value ?? '').trim()
  if (!/^\d+(?:\.\d+)?$/.test(text)) return undefined
  const parsed = Number(text)
  return Number.isFinite(parsed) ? parsed : undefined
}

function plainInteger(value: unknown) {
  const parsed = plainNumber(value)
  return parsed !== undefined && Number.isInteger(parsed) ? parsed : undefined
}

function curingSeconds(value: unknown) {
  const text = String(value ?? '').trim()
  const explicit = text.match(/(\d+(?:\.\d+)?)\s*(?:秒|s(?:ec(?:ond)?s?)?)/i)
  return explicit ? Number(explicit[1]) : plainNumber(text)
}

function weightKg(value: unknown) {
  const text = String(value ?? '').trim()
  const kg = text.match(/(\d+(?:\.\d+)?)\s*kg\b/i)
  if (kg) return Number(kg[1])
  const grams = text.match(/(\d+(?:\.\d+)?)\s*(?:g\b|克)/i)
  return grams ? Number(grams[1]) / 1000 : undefined
}

function allocationSignature(allocations: Array<{ order_id: number; planned_quantity: number }>) {
  return allocations
    .map((item) => ({ order_id: Number(item.order_id), planned_quantity: Number(item.planned_quantity) }))
    .sort((left, right) => left.order_id - right.order_id)
}

export function ProductionRunDrawer({ open, run, station, mountedMold, initialStatus = 'RUNNING', onClose, onSuccess }: Props) {
  const [form] = Form.useForm()
  const selectedStatus = Form.useWatch('status', form)
  const selectedStationId = Form.useWatch<number>('station_id', form)
  const selectedProductSpecificationId = Form.useWatch<number>('product_specification_id', form)
  const selectedLoadedAt = Form.useWatch('loaded_at', form)
  const watchedSelectedOrderIds = Form.useWatch<number[]>('order_ids', form)
  const selectedOrderIds = useMemo(() => watchedSelectedOrderIds || [], [watchedSelectedOrderIds])
  const trialTask = Form.useWatch<boolean>('is_trial_task', form) || false
  const watchedOrderTargets = Form.useWatch<Record<number, number>>('order_targets', { form, preserve: true })
  const orderTargets = watchedOrderTargets || {}
  const originalAllocations = run
    ? run.order_allocations?.length
      ? run.order_allocations
      : run.order_id
        ? [{ order_id: run.order_id, planned_quantity: run.order_quantity }]
        : []
    : []
  const currentAllocations = selectedOrderIds.map((orderId) => ({
    order_id: orderId,
    planned_quantity: Math.max(1, Number(orderTargets[orderId] || 1)),
  }))
  const orderAllocationsChanged = Boolean(run)
    && JSON.stringify(allocationSignature(originalAllocations)) !== JSON.stringify(allocationSignature(currentAllocations))
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const stationsQuery = useQuery({
    queryKey: ['production', 'stations'],
    queryFn: async () => toList(await productionApi.stations()),
    enabled: open,
  })
  const moldsQuery = useQuery({
    queryKey: ['molds', 'production-select'],
    queryFn: async () => toList(await moldApi.list({ page_size: 1000 })),
    enabled: open,
  })
  const ordersQuery = useQuery({
    queryKey: ['orders', 'production-select'],
    queryFn: async () => toList(await orderApi.list({ page_size: 1000 })),
    enabled: open,
  })
  const productSpecificationsQuery = useQuery({
    queryKey: ['product-specifications', 'production-select'],
    queryFn: async () => toList(await productSpecificationApi.list({ page_size: 1000 })),
    enabled: open,
  })

  useEffect(() => {
    if (!open) return
    if (run) {
      const allocations = run.order_allocations?.length
        ? run.order_allocations
        : run.order_id
          ? [{ order_id: run.order_id, planned_quantity: run.order_quantity }]
          : []
      form.resetFields()
      form.setFieldsValue({
        ...run,
        large_strip_weight_g: run.large_strip_weight_g ?? (run.strip_weight_kg != null ? Number(run.strip_weight_kg) * 1000 : undefined),
        large_strip_count: run.large_strip_count ?? run.strips_per_batch,
        station_id: run.station?.id,
        mold_id: run.mold?.id,
        order_ids: allocations.map((item) => item.order_id),
        order_targets: Object.fromEntries(allocations.map((item) => [item.order_id, item.planned_quantity])),
        is_trial_task: allocations.length === 0 && !run.order_id,
        product_specification_id: run.product_specification_id || run.product_specification?.id,
        loaded_at: run.loaded_at ? dayjs(run.loaded_at) : undefined,
        expected_change_at: run.expected_change_at ? dayjs(run.expected_change_at) : undefined,
        material_changed_at: run.material_changed_at ? dayjs(run.material_changed_at) : undefined,
        unloaded_at: run.unloaded_at ? dayjs(run.unloaded_at) : undefined,
      })
    } else {
      const loadedAt = dayjs().second(0)
      const planned = initialStatus === 'PLANNED'
      form.resetFields()
      form.setFieldsValue({
        station_id: station?.id,
        mold_id: mountedMold?.id,
        specification: mountedMold?.product_name || mountedMold?.model_code,
        cavities: 1,
        estimated_defect_rate: 3,
        curing_seconds: 60,
        estimated_hours: 8,
        loaded_at: planned ? undefined : loadedAt,
        expected_change_at: planned ? undefined : loadedAt.add(8, 'hour'),
        status: initialStatus,
        order_targets: {},
        is_trial_task: false,
      })
    }
  }, [form, initialStatus, mountedMold?.id, mountedMold?.model_code, mountedMold?.product_name, open, run, station?.id])

  const mutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) => run
      ? productionApi.updateRun(run.id, payload)
      : productionApi.createRun(payload),
    onSuccess: async (result) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['production'] }),
        queryClient.invalidateQueries({ queryKey: ['analytics'] }),
      ])
      if (run?.is_settled && !result.is_settled) message.warning('价格或生产资料已变化，原完工结算已撤销，请重新结算。')
      else message.success(run ? '生产记录已更新' : '生产记录已创建')
      onSuccess?.(result)
      onClose()
    },
    onError: (error: Error) => message.error(error.message),
  })

  const applyProductSpecification = (specification?: ProductSpecification) => {
    if (!specification) return
    const cavities = plainInteger(specification.effective_cavities) ?? plainInteger(specification.total_cavities)
    const strips = plainInteger(specification.strip_count)
    const seconds = curingSeconds(specification.primary_curing)
    const stripWeight = weightKg(specification.actual_cut_weight || specification.cut_weight)
    form.setFieldsValue({
      specification: specification.specification || specification.product_name || form.getFieldValue('specification'),
      material: specification.material || form.getFieldValue('material'),
      compound_size: specification.actual_material_length || specification.material_length || form.getFieldValue('compound_size'),
      ...(cavities !== undefined ? { cavities } : {}),
      ...(strips !== undefined ? { strips_per_batch: strips } : {}),
      ...(seconds !== undefined ? { curing_seconds: seconds } : {}),
      ...(stripWeight !== undefined ? { strip_weight_kg: stripWeight } : {}),
      ...(specification.large_strip_weight_g != null ? { large_strip_weight_g: Number(specification.large_strip_weight_g) } : {}),
      ...(specification.large_strip_count != null ? { large_strip_count: specification.large_strip_count } : {}),
      ...(specification.small_strip_weight_g != null ? { small_strip_weight_g: Number(specification.small_strip_weight_g) } : {}),
      ...(specification.small_strip_count != null ? { small_strip_count: specification.small_strip_count } : {}),
    })
  }

  const updateCombinedOrderFields = (orderIds: number[], targets: Record<number, number>) => {
    const selected = orderIds
      .map((id) => ordersQuery.data?.find((item) => item.id === id))
      .filter((item): item is NonNullable<typeof item> => Boolean(item))
    if (!selected.length) {
      form.setFieldsValue({ order_no: undefined, order_quantity: undefined })
      return
    }
    const order = [...selected].sort((left, right) => {
      if (!left.due_date) return 1
      if (!right.due_date) return -1
      return left.due_date.localeCompare(right.due_date)
    })[0]
    const linkedSpecification = order.product_specification
      || productSpecificationsQuery.data?.find((item) => item.id === order.product_specification_id)
    applyProductSpecification(linkedSpecification)
    const total = selected.reduce((sum, item) => sum + Math.max(1, targets[item.id] || item.production_remaining_quantity || item.order_quantity), 0)
    form.setFieldsValue({
      order_no: order.order_no,
      order_quantity: total,
      specification: order.specification || linkedSpecification?.specification || linkedSpecification?.product_name,
      material: order.material || linkedSpecification?.material,
      product_specification_id: linkedSpecification?.id || order.product_specification_id,
      ...(plainNumber(order.forming_hours) !== undefined ? { estimated_hours: plainNumber(order.forming_hours) } : {}),
    })
    recalculate({ order_quantity: total }, { ...form.getFieldsValue(), order_quantity: total })
  }

  const selectOrders = (orderIds: number[]) => {
    const nextTargets = { ...orderTargets }
    orderIds.forEach((id) => {
      if (nextTargets[id]) return
      const order = ordersQuery.data?.find((item) => item.id === id)
      if (!order) return
      nextTargets[id] = Math.max(1, Number(order.production_remaining_quantity || order.order_quantity || 1))
    })
    Object.keys(nextTargets).forEach((id) => {
      if (!orderIds.includes(Number(id))) delete nextTargets[Number(id)]
    })
    form.setFieldValue('order_targets', nextTargets)
    updateCombinedOrderFields(orderIds, nextTargets)
  }

  const selectProductSpecification = (id?: number) => {
    applyProductSpecification(productSpecificationsQuery.data?.find((item) => item.id === id))
  }

  const selectedStation = stationsQuery.data?.find((item) => item.id === selectedStationId)
  const selectedProductSpecification = productSpecificationsQuery.data?.find((item) => item.id === selectedProductSpecificationId)
    || (run && run.product_specification?.id === selectedProductSpecificationId ? run.product_specification : undefined)
  const firstSelectedOrder = ordersQuery.data?.find((item) => item.id === selectedOrderIds[0])
  const compatibleOrders = useMemo(() => {
    const currentIds = new Set(selectedOrderIds)
    return (ordersQuery.data || []).filter((item) => {
      if (currentIds.has(item.id)) return true
      if (item.status !== 'OPEN') return false
      if (!firstSelectedOrder) return true
      return item.specification.trim().toLocaleLowerCase() === firstSelectedOrder.specification.trim().toLocaleLowerCase()
        && item.material.trim().toLocaleLowerCase() === firstSelectedOrder.material.trim().toLocaleLowerCase()
    })
  }, [firstSelectedOrder, ordersQuery.data, selectedOrderIds])
  const selectedOrders = selectedOrderIds
    .map((id) => ordersQuery.data?.find((item) => item.id === id))
    .filter((item): item is NonNullable<typeof item> => Boolean(item))
    .sort((left, right) => (left.due_date || '9999-12-31').localeCompare(right.due_date || '9999-12-31'))
  const linksLocked = !!run && run.status !== 'PLANNED'
  const selectableMolds = (moldsQuery.data || []).filter((mold) => {
    if (selectedStatus === 'RUNNING') {
      return mold.status === 'ON_MACHINE' && (!selectedStation?.machine || mold.machine?.id === selectedStation.machine.id)
    }
    if (selectedStatus === 'PLANNED') {
      return mold.status === 'IN_STOCK' || (
        mold.status === 'ON_MACHINE'
        && (!selectedStation?.machine || mold.machine?.id === selectedStation.machine.id)
      )
    }
    return true
  })

  const recalculate = (changedValues: Record<string, unknown>, allValues: Record<string, unknown>) => {
    const changed = Object.keys(changedValues)[0]
    const planDrivers = ['order_quantity', 'cavities', 'estimated_defect_rate']
    const hourDrivers = [...planDrivers, 'planned_mold_count', 'curing_seconds']
    if (![...hourDrivers, 'estimated_hours', 'loaded_at'].includes(changed)) return
    const orderQuantity = asNumber(allValues.order_quantity)
    const cavities = Math.max(asNumber(allValues.cavities, 1), 1)
    const defectRate = Math.max(asNumber(allValues.estimated_defect_rate), 0)
    const curingSeconds = Math.max(asNumber(allValues.curing_seconds), 0)
    const plannedMolds = planDrivers.includes(changed)
      ? (orderQuantity ? Math.ceil((orderQuantity * (1 + defectRate / 100)) / cavities) : undefined)
      : asNumber(allValues.planned_mold_count) || undefined
    const estimatedHours = hourDrivers.includes(changed) && plannedMolds && curingSeconds
      ? Number(((plannedMolds * curingSeconds) / 3600).toFixed(2))
      : asNumber(allValues.estimated_hours) || undefined
    const loadedAt = allValues.loaded_at as Dayjs | undefined
    form.setFieldsValue({
      planned_mold_count: plannedMolds,
      ...(estimatedHours ? { estimated_hours: estimatedHours } : {}),
      ...(loadedAt && estimatedHours ? { expected_change_at: loadedAt.add(estimatedHours, 'hour') } : {}),
    })
  }

  const submit = async () => {
    const values = await form.validateFields()
    const apiValues = { ...values }
    delete apiValues.order_targets
    delete apiValues.order_ids
    delete apiValues.is_trial_task
    const payload = {
      ...apiValues,
      loaded_at: values.loaded_at ? values.loaded_at.toISOString() : null,
      expected_change_at: values.expected_change_at ? values.expected_change_at.toISOString() : null,
      material_changed_at: values.material_changed_at ? values.material_changed_at.toISOString() : null,
      unloaded_at: values.unloaded_at ? values.unloaded_at.toISOString() : null,
      mold_id: values.mold_id || null,
      order_id: values.order_id || null,
      selected_orders: selectedOrderIds.length
        ? selectedOrderIds.map((orderId) => ({
          order_id: orderId,
          planned_quantity: Math.max(1, orderTargets[orderId] || 1),
        }))
        : undefined,
      order_change_reason: values.order_change_reason,
      product_specification_id: values.product_specification_id || null,
    }
    mutation.mutate(payload)
  }

  const handleStatusChange = (value: ProductionRun['status']) => {
    if (value === 'PLANNED') {
      form.setFieldsValue({ loaded_at: undefined, expected_change_at: undefined, material_changed_at: undefined, unloaded_at: undefined })
      return
    }
    if (value === 'RUNNING') {
      const loadedAt = form.getFieldValue('loaded_at') as Dayjs | undefined
      const selectedMold = moldsQuery.data?.find((mold) => mold.id === form.getFieldValue('mold_id'))
      const station = stationsQuery.data?.find((item) => item.id === form.getFieldValue('station_id'))
      const validMountedMold = selectedMold?.status === 'ON_MACHINE' && (!station?.machine || selectedMold.machine?.id === station.machine.id)
      form.setFieldsValue({
        loaded_at: loadedAt || dayjs().second(0),
        unloaded_at: undefined,
        ...(!validMountedMold ? { mold_id: undefined } : {}),
      })
      return
    }
    if (value === 'COMPLETED') {
      const now = dayjs().second(0)
      form.setFieldsValue({
        loaded_at: (form.getFieldValue('loaded_at') as Dayjs | undefined) || now,
        unloaded_at: (form.getFieldValue('unloaded_at') as Dayjs | undefined) || now,
      })
      return
    }
    if (value === 'CANCELLED') {
      if (!run) {
        form.setFieldsValue({ loaded_at: undefined, expected_change_at: undefined, material_changed_at: undefined, unloaded_at: undefined })
        return
      }
      const loadedAt = form.getFieldValue('loaded_at') as Dayjs | undefined
      if (loadedAt) {
        form.setFieldsValue({ unloaded_at: (form.getFieldValue('unloaded_at') as Dayjs | undefined) || dayjs().second(0) })
      }
    }
  }

  return (
    <Drawer
      open={open}
      onClose={onClose}
      size={680}
      title={run ? `编辑生产记录 · ${run.order_no}` : initialStatus === 'PLANNED' ? '新增待上机计划' : mountedMold ? `登记生产 · ${mountedMold.model_code}` : '新增生产记录'}
      footer={<Space className="drawer-footer-actions"><Button onClick={onClose}>取消</Button><Button type="primary" loading={mutation.isPending} onClick={submit}>{run ? '保存修改' : selectedStatus === 'PLANNED' ? '保存待上机计划' : selectedStatus === 'COMPLETED' ? '保存已完成记录' : selectedStatus === 'CANCELLED' ? '保存已取消记录' : '确认上机'}</Button></Space>}
    >
      <Alert
        type="info"
        showIcon
        icon={<ClockCircleOutlined />}
        title={run?.status === 'PLANNED' ? '待上机计划请在此维护资料，实际开始时请回到详情点击“确认上机”。' : '预计换模时间可以留空，系统会按上模时间与预计工时自动计算；也可以人工校正。'}
      />
      <Form form={form} layout="vertical" requiredMark="optional" onValuesChange={recalculate} className="production-form">
        <Row gutter={14}>
          <Col xs={24} sm={12}>
            <Form.Item name="station_id" label="机台" rules={[{ required: true, message: '请选择机台' }]}>
              <Select
                showSearch
                optionFilterProp="label"
                loading={stationsQuery.isLoading}
                placeholder="例如 一组 · 1号机台"
                onChange={(stationId) => {
                  if (selectedStatus !== 'RUNNING') return
                  const selectedMold = moldsQuery.data?.find((mold) => mold.id === form.getFieldValue('mold_id'))
                  const nextStation = stationsQuery.data?.find((item) => item.id === stationId)
                  if (selectedMold && nextStation?.machine && selectedMold.machine?.id !== nextStation.machine.id) form.setFieldValue('mold_id', undefined)
                }}
                options={(stationsQuery.data || []).filter((item) => item.is_active).map((item) => ({ value: item.id, label: `${productionStationGroupLabel(item.group)} · ${productionStationNumber(item)}号机台` }))}
              />
            </Form.Item>
          </Col>
          <Col xs={24} sm={12}>
            <Form.Item name="status" label="生产状态" rules={[{ required: true }]}>
              <Select onChange={handleStatusChange} options={[
                { value: 'PLANNED', label: '待上机' },
                {
                  value: 'RUNNING',
                  label: run && run.status !== 'RUNNING' ? '生产中（请通过确认上机进入）' : '生产中',
                  disabled: !!run && run.status !== 'RUNNING',
                },
                {
                  value: 'COMPLETED',
                  label: run?.status === 'PLANNED' ? '已完成（需先确认上机）' : '已完成',
                  disabled: run?.status === 'PLANNED',
                },
                { value: 'CANCELLED', label: '已取消' },
              ]} />
            </Form.Item>
          </Col>
        </Row>

        <div className="production-form-section">订单与产品规格</div>
        <Alert
          className="business-form-hint"
          type="info"
          showIcon
          title="可一次选择多个相同规格、相同材质的订单"
          description="系统按交期从近到远分配合计产量，并自动汇总本次计划数量和总模数；订单最终是否完成仍由你在订单管理中手工确认。"
        />
        <Form.Item name="is_trial_task" valuePropName="checked">
          <Checkbox onChange={(event) => {
            if (event.target.checked) {
              form.setFieldsValue({ order_ids: [], order_targets: {}, order_no: '', order_quantity: undefined })
            }
          }}>无订单试模 / 临时生产任务</Checkbox>
        </Form.Item>
        <Row gutter={14}>
          <Col xs={24}>
            <Form.Item name="order_ids" label="本次合并生产的订单" rules={trialTask ? undefined : [{ required: true, message: '请至少选择一个订单' }]} extra={trialTask ? '当前为无订单试模/临时任务，可直接填写临时任务号、规格和材质。' : '选择第一张订单后，只显示规格和材质相同的进行中订单；已完成、已取消订单不会出现在候选列表。'}>
              <Select
                mode="multiple"
                disabled={trialTask}
                showSearch
                optionFilterProp="label"
                loading={ordersQuery.isLoading}
                onChange={selectOrders}
                placeholder="先选一个订单，再继续勾选同规格、同材质订单"
                options={compatibleOrders.map((item) => ({
                  value: item.id,
                  label: [item.order_no, item.item_no, item.due_date ? `交期 ${item.due_date}` : '未填交期', item.specification, item.material].filter(Boolean).join(' · '),
                }))}
              />
            </Form.Item>
          </Col>
          <Col xs={24} sm={12}>
            <Form.Item name="product_specification_id" label="关联产品规格（可选）" extra={linksLocked ? '开始生产后产品规格关联不可更换。' : '原始工艺参数会显示为参考；只有能无歧义转换的数字才自动带入生产字段。'}>
              <Select
                allowClear
                showSearch
                optionFilterProp="label"
                disabled={linksLocked}
                loading={productSpecificationsQuery.isLoading}
                onChange={selectProductSpecification}
                placeholder="按产品名称、客户编号或规格搜索"
                options={(productSpecificationsQuery.data || []).map((item) => ({ value: item.id, label: [item.customer_product_no, item.product_name, item.specification].filter(Boolean).join(' · '), disabled: !item.is_active }))}
              />
            </Form.Item>
          </Col>
        </Row>
        {selectedOrders.length > 0 && <Card size="small" title={`合并生产清单 · 共 ${selectedOrders.length} 个订单`} className="production-order-allocation-card">
          <Space direction="vertical" size={10} style={{ width: '100%' }}>
            {selectedOrders.map((order, index) => <div key={order.id} className="production-order-allocation-row">
              <div>
                <Space wrap><Tag color="blue">交期顺序 {index + 1}</Tag><strong>{order.order_no}{order.item_no ? ` / ${order.item_no}` : ''}</strong></Space>
                <Typography.Text type="secondary">交期 {order.due_date || '未填写'} · 原订单 {order.order_quantity} 件 · 当前剩余 {order.production_remaining_quantity ?? order.order_quantity} 件</Typography.Text>
              </div>
              <InputNumber
                min={1}
                precision={0}
                value={orderTargets[order.id]}
                addonBefore="本次生产"
                addonAfter="件"
                onChange={(value) => {
                  const next = { ...orderTargets, [order.id]: Math.max(1, Number(value || 1)) }
                  form.setFieldValue('order_targets', next)
                  updateCombinedOrderFields(selectedOrderIds, next)
                }}
              />
            </div>)}
          </Space>
        </Card>}
        {selectedProductSpecification && (
          <Alert
            className="production-specification-reference"
            type="info"
            showIcon
            title={`工艺参考 · ${selectedProductSpecification.product_name}`}
            description={<div className="production-specification-reference-grid"><span>实际切料长：{selectedProductSpecification.actual_material_length || selectedProductSpecification.material_length || '-'}</span><span>实际切料重：{selectedProductSpecification.actual_cut_weight || selectedProductSpecification.cut_weight || '-'}</span><span>条数：{selectedProductSpecification.strip_count || '-'}</span><span>一次硫化：{selectedProductSpecification.primary_curing || '-'}</span><span>二烤：{selectedProductSpecification.secondary_curing || '-'}</span><span>孔数：{selectedProductSpecification.effective_cavities || '-'} / {selectedProductSpecification.total_cavities || '-'}</span><span>模具型号：{selectedProductSpecification.mold_model?.code || selectedProductSpecification.mold_no || '-'}</span></div>}
          />
        )}

        <Row gutter={14}>
          <Col xs={24} sm={12}><Form.Item name="order_no" label={trialTask ? '临时任务号' : '交期最早订单号（自动）'} rules={[{ required: true, message: trialTask ? '请输入临时任务号' : '请先选择订单' }]}><Input disabled={!trialTask} placeholder={trialTask ? '例如 试模-20260917-01' : undefined} /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="operator" label="默认作业员（可选）"><Input placeholder="录入日报时可自动带出，仍可修改" /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="specification" label="规格" rules={[{ required: true, message: '请输入产品规格' }]}><Input /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="material" label="材质 / 胶料配方" rules={[{ required: true, message: '请输入材质' }]}><Input placeholder="例如 配方A" /></Form.Item></Col>
        </Row>
        {run && <Form.Item
          name="order_change_reason"
          label="关联订单修改原因"
          dependencies={['order_ids', 'order_targets']}
          rules={[{
            validator: (_, value) => orderAllocationsChanged && !String(value || '').trim()
              ? Promise.reject(new Error('增加、移除订单或调整本次生产数量时必须填写修改原因'))
              : Promise.resolve(),
          }]}
          extra="只有增加、移除订单或调整本次生产数量时必填；系统会保留修改前后的订单分配记录。"
        >
          <Input.TextArea rows={2} maxLength={1000} showCount placeholder="例如：追加同规格订单，合并生产以减少重复换模" />
        </Form.Item>}

        <Form.Item
          name="mold_id"
          label="关联模具"
          rules={[{ required: selectedStatus === 'PLANNED' || selectedStatus === 'RUNNING', message: '待上机计划或生产中记录必须关联具体模具' }]}
          extra={selectedStatus === 'PLANNED' ? '确认上机时，系统会将该模具同步移出货架并登记到所选机台。' : undefined}
        >
          <Select
            allowClear
            showSearch
            optionFilterProp="label"
            loading={moldsQuery.isLoading}
            placeholder={selectedStatus === 'RUNNING' ? '选择已在该机台上机的模具' : '按模具编号、型号或产品名称搜索'}
            options={selectableMolds.map((mold) => ({
              value: mold.id,
              label: `${moldCode(mold)} · ${moldModelOf(mold)?.code || '-'} · ${moldLocation(mold)}`,
            }))}
          />
        </Form.Item>

        <div className="production-form-section"><CalculatorOutlined /> 产量与工时计划</div>
        <Row gutter={14}>
          <Col xs={12} sm={8}><Form.Item name="order_quantity" label="本次合计生产数量" rules={[{ required: true, message: trialTask ? '请输入计划生产数量' : '请先选择订单' }]}><InputNumber min={1} precision={0} disabled={!trialTask} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={12} sm={8}><Form.Item name="cavities" label="模具孔数" rules={[{ required: true }]}><InputNumber min={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={12} sm={8}><Form.Item name="estimated_defect_rate" label="预估不良率(%)"><InputNumber min={0} max={100} precision={2} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={12} sm={8}><Form.Item name="planned_mold_count" label="计划生产模数" rules={[{ required: true }]}><InputNumber min={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={12} sm={8}><Form.Item name="curing_seconds" label="硫化时间(秒)"><InputNumber min={0} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={12} sm={8}><Form.Item name="estimated_hours" label="预计生产工时(小时)"><InputNumber min={0.01} precision={2} style={{ width: '100%' }} /></Form.Item></Col>
        </Row>

        {selectedStatus !== 'PLANNED' && <Row gutter={14}>
          <Col xs={24} sm={12}><Form.Item name="loaded_at" label="上模时间" rules={[{ required: selectedStatus === 'RUNNING' || selectedStatus === 'COMPLETED', message: '生产中或已完成记录必须填写上模时间' }]}><DatePicker showTime format="YYYY-MM-DD HH:mm" style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="expected_change_at" label="预计换模时间" rules={[{
            validator: (_, value: Dayjs | null) => {
              if (!value) return Promise.resolve()
              const loadedAt = form.getFieldValue('loaded_at') as Dayjs | undefined
              if (!loadedAt) return Promise.reject(new Error('请先填写上模时间'))
              return value.isBefore(loadedAt)
                ? Promise.reject(new Error('预计换模时间不能早于上模时间'))
                : Promise.resolve()
            },
          }]}><DatePicker showTime format="YYYY-MM-DD HH:mm" style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="material_changed_at" label="最近换料时间（可选）" dependencies={['loaded_at', 'unloaded_at']} rules={[{
            validator: (_, value: Dayjs | null) => {
              if (!value) return Promise.resolve()
              const loadedAt = form.getFieldValue('loaded_at') as Dayjs | undefined
              const unloadedAt = form.getFieldValue('unloaded_at') as Dayjs | undefined
              if (!loadedAt) return Promise.reject(new Error('请先填写上模时间'))
              if (value.isBefore(loadedAt)) return Promise.reject(new Error('换料时间不能早于上模时间'))
              return unloadedAt && value.isAfter(unloadedAt)
                ? Promise.reject(new Error('换料时间不能晚于停机时间'))
                : Promise.resolve()
            },
          }]}><DatePicker showTime format="YYYY-MM-DD HH:mm" style={{ width: '100%' }} /></Form.Item></Col>
          {(selectedStatus === 'COMPLETED' || selectedStatus === 'CANCELLED') && <Col xs={24} sm={12}><Form.Item name="unloaded_at" label="停机 / 结束时间" rules={[
            { required: requiresProductionUnloadTime(selectedStatus, !!selectedLoadedAt), message: selectedStatus === 'CANCELLED' ? '已上模的取消记录必须填写停机时间' : '已完成记录必须填写停机时间' },
            {
              validator: (_, value: Dayjs | null) => {
                if (!value) return Promise.resolve()
                const loadedAt = form.getFieldValue('loaded_at') as Dayjs | undefined
                if (!loadedAt) return Promise.reject(new Error('填写停机时间前必须先填写上模时间'))
                return value.isBefore(loadedAt)
                  ? Promise.reject(new Error('停机时间不能早于上模时间'))
                  : Promise.resolve()
              },
            },
          ]}><DatePicker showTime format="YYYY-MM-DD HH:mm" style={{ width: '100%' }} /></Form.Item></Col>}
        </Row>}

        <div className="production-form-section">胶料与结算单价</div>
        <Row gutter={14}>
          <Col xs={24} sm={12}><Form.Item name="compound_size" label="胶料尺寸"><Input placeholder="例如 长300×厚4" /></Form.Item></Col>
          <Col xs={12} sm={6}><Form.Item name="large_strip_weight_g" label="大条条重(g)" extra="成型前排料"><InputNumber min={0} precision={2} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={12} sm={6}><Form.Item name="large_strip_count" label="大条数量"><InputNumber min={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={12} sm={6}><Form.Item name="small_strip_weight_g" label="小条条重(g)" extra="补料，没有可留空"><InputNumber min={0} precision={2} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={12} sm={6}><Form.Item name="small_strip_count" label="小条数量"><InputNumber min={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={24}><Typography.Text type="secondary">大条和小条只用于计算成型前一模排料总重，不代表成品重量，也不进入库存。</Typography.Text></Col>
          <Col xs={12}><Form.Item name="unit_price" label="成品单价(元/件)"><InputNumber min={0} precision={4} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={12}><Form.Item name="material_unit_price" label="材料单价(元/kg)"><InputNumber min={0} precision={4} style={{ width: '100%' }} /></Form.Item></Col>
        </Row>
        <Form.Item name="notes" label="备注"><Input.TextArea rows={3} maxLength={500} showCount /></Form.Item>
      </Form>
    </Drawer>
  )
}
