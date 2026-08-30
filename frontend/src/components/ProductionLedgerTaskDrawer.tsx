import { CalculatorOutlined } from '@ant-design/icons'
import { Alert, App, Button, Checkbox, Col, Drawer, Form, Input, InputNumber, Row, Select, Space, Statistic } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo } from 'react'
import { moldApi, orderApi, productionApi, toList } from '../api/client'
import type { MoldAsset, Order, ProductionRun } from '../types'
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

function orderLabel(order: Order) {
  return [order.order_no, order.item_no, order.product_name, order.specification, order.material]
    .filter(Boolean)
    .join(' · ')
}

export function ProductionLedgerTaskDrawer({ open, run, initialDraft, onClose, onSaved }: Props) {
  const [form] = Form.useForm<Record<string, any>>()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const orderId = Form.useWatch<number>('order_id', form)
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

  const selectedOrder = ordersQuery.data?.find((item) => item.id === orderId)
    || (run && run.order?.id === orderId ? run.order : undefined)
  const selectedMold = moldsQuery.data?.find((item) => item.id === moldId)
  const suggestedMolds = useMemo(() => {
    const all = moldsQuery.data || []
    if (!selectedOrder) return all
    const specification = selectedOrder.specification.trim().toLocaleLowerCase()
    const productName = selectedOrder.product_name?.trim().toLocaleLowerCase()
    return [...all].sort((left, right) => {
      const score = (mold: MoldAsset) => {
        const model = moldModelOf(mold)
        const text = `${model?.code || ''} ${model?.product_name || ''}`.toLocaleLowerCase()
        return Number(Boolean(specification && text.includes(specification))) * 2
          + Number(Boolean(productName && text.includes(productName)))
      }
      return score(right) - score(left)
    })
  }, [moldsQuery.data, selectedOrder])

  const suggestedMoldCount = useMemo(() => {
    if (!selectedOrder || cavities < 1) return undefined
    const quantity = Number(selectedOrder.order_quantity || 0)
    if (quantity < 1) return undefined
    if (defectMode === 'QUANTITY') return Math.max(1, Math.ceil((quantity + Math.max(defectQuantity, 0)) / cavities))
    return Math.max(1, Math.ceil((quantity / cavities) * (1 + Math.max(defectRate, 0) / 100)))
  }, [cavities, defectMode, defectQuantity, defectRate, selectedOrder])

  useEffect(() => {
    if (!open) return
    form.resetFields()
    const draftOrder = initialDraft?.order_no
      ? ordersQuery.data?.find((item) => item.order_no.trim().toLocaleLowerCase() === String(initialDraft.order_no).trim().toLocaleLowerCase()
        && (!initialDraft.item_no || item.item_no === String(initialDraft.item_no)))
      : undefined
    form.setFieldsValue(run ? {
      ...run,
      order_id: run.order_id || run.order?.id,
      station_id: run.station_id || run.station?.id,
      mold_id: run.mold_id || run.mold?.id,
      estimated_defect_mode: run.estimated_defect_mode || 'RATE',
    } : {
      ...initialDraft,
      order_id: draftOrder?.id,
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
        order_id: values.order_id,
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

  const selectOrder = (id?: number) => {
    const order = ordersQuery.data?.find((item) => item.id === id)
    if (!order) return
    form.setFieldsValue({
      order_no: order.order_no,
      specification: order.specification,
      material: order.material,
      order_quantity: order.order_quantity,
    })
  }

  const selectMold = (id?: number) => {
    const mold = moldsQuery.data?.find((item) => item.id === id)
    if (mold?.default_cavities) form.setFieldValue('cavities', mold.default_cavities)
  }

  const submit = async () => mutation.mutate(await form.validateFields())

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
        <Form.Item name="order_id" label="订单号 / 项次" rules={[{ required: true, message: '请选择唯一订单（订单号+项次）' }]}>
          <Select showSearch optionFilterProp="label" loading={ordersQuery.isLoading} onChange={selectOrder} placeholder="按订单号、项次、规格或材质搜索" options={(ordersQuery.data || []).map((order) => ({ value: order.id, label: orderLabel(order) }))} />
        </Form.Item>
        {selectedOrder && <Alert className="production-ledger-order-alert" type="success" showIcon title={`${selectedOrder.order_no}${selectedOrder.item_no ? ` / ${selectedOrder.item_no}` : ''} · ${selectedOrder.specification}`} description={`材质 ${selectedOrder.material || '未登记'} · 订单数量 ${selectedOrder.order_quantity.toLocaleString('zh-CN')}件`} />}
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
