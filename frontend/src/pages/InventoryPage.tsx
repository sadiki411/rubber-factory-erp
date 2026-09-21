import { InboxOutlined, MinusCircleOutlined, PlusOutlined, PrinterOutlined, SearchOutlined, SwapOutlined } from '@ant-design/icons'
import { Alert, App, Button, Card, Col, Empty, Form, Input, InputNumber, Modal, Row, Select, Space, Statistic, Table, Tag, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { inventoryApi, orderApi, toList } from '../api/client'
import { qualityApi } from '../api/client'
import { PageTitle } from '../components/PageTitle'
import type { InventoryLocation, InventoryQualityStatus, MaterialRemainder, Order, QualityEmployee } from '../types'

const QUALITY_META: Record<InventoryQualityStatus, { label: string; color: string }> = {
  WAITING: { label: '待检', color: 'warning' },
  PASSED: { label: '已检合格', color: 'success' },
  FAILED: { label: '不合格', color: 'error' },
  HOLD: { label: '冻结', color: 'default' },
}
const EMPTY_LOCATIONS: InventoryLocation[] = []

function qualityTag(value?: InventoryQualityStatus) {
  const meta = value ? QUALITY_META[value] : undefined
  return <Tag color={meta?.color}>{meta?.label || value || '空位'}</Tag>
}

export function InventoryPage() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [query, setQuery] = useState('')
  const [labelSize, setLabelSize] = useState<'100x50' | '70x30' | '50x30'>('100x50')
  const [receiptOpen, setReceiptOpen] = useState(false)
  const [outboundOpen, setOutboundOpen] = useState(false)
  const [remainderOpen, setRemainderOpen] = useState(false)
  const [remainderUseTarget, setRemainderUseTarget] = useState<MaterialRemainder>()
  const [moveTarget, setMoveTarget] = useState<{ containerId: number; containerCode: string; containerType: 'BAG' | 'BASKET'; currentLocationId: number }>()
  const [qualityTarget, setQualityTarget] = useState<InventoryLocation['container']>()
  const [receiptForm] = Form.useForm()
  const [outboundForm] = Form.useForm()
  const [qualityForm] = Form.useForm()
  const [remainderForm] = Form.useForm()
  const [remainderUseForm] = Form.useForm()
  const [moveForm] = Form.useForm()
  const summaryQuery = useQuery({ queryKey: ['inventory', 'summary'], queryFn: inventoryApi.summary })
  const locationsQuery = useQuery({
    queryKey: ['inventory', 'locations'],
    queryFn: async () => toList(await inventoryApi.locations({ active: true })),
  })
  const containersQuery = useQuery({
    queryKey: ['inventory', 'containers'],
    queryFn: async () => toList(await inventoryApi.containers({ active: true })),
  })
  const productsQuery = useQuery({
    queryKey: ['inventory', 'products', query],
    queryFn: async () => toList(await inventoryApi.products({ q: query || undefined, page_size: 1000 })),
  })
  const ordersQuery = useQuery({
    queryKey: ['inventory', 'orders'],
    queryFn: async () => toList(await orderApi.list({ status: 'OPEN', page_size: 1000 })),
    enabled: outboundOpen,
  })
  const employeesQuery = useQuery({
    queryKey: ['inventory', 'inspectors'],
    queryFn: async () => toList(await qualityApi.listEmployees({ page_size: 1000 })),
    enabled: !!qualityTarget || receiptOpen,
  })
  const remaindersQuery = useQuery({
    queryKey: ['inventory', 'material-remainders'],
    queryFn: async () => toList(await inventoryApi.materialRemainders()),
  })
  const bootstrapMutation = useMutation({
    mutationFn: inventoryApi.bootstrap,
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ['inventory'] })
      message.success(`已生成 ${result.created} 个固定库位`)
    },
    onError: (error: Error) => message.error(error.message),
  })
  const receiptMutation = useMutation({
    mutationFn: inventoryApi.receipt,
    onSuccess: async () => {
      setReceiptOpen(false)
      receiptForm.resetFields()
      await queryClient.invalidateQueries({ queryKey: ['inventory'] })
      message.success('库存已直接入库，未关联订单或流程卡')
    },
    onError: (error: Error) => message.error(error.message),
  })
  const outboundMutation = useMutation({
    mutationFn: inventoryApi.outbound,
    onSuccess: async () => {
      setOutboundOpen(false)
      outboundForm.resetFields()
      await queryClient.invalidateQueries({ queryKey: ['inventory'] })
      message.success('库存出库已确认，请在品检出货页面单独扫描流程卡')
    },
    onError: (error: Error) => message.error(error.message),
  })
  const qualityMutation = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Record<string, unknown> }) => inventoryApi.setQuality(id, body),
    onSuccess: async () => {
      setQualityTarget(undefined)
      qualityForm.resetFields()
      await queryClient.invalidateQueries({ queryKey: ['inventory'] })
      message.success('库存质量状态已更新，并保留状态变更流水')
    },
    onError: (error: Error) => message.error(error.message),
  })
  const remainderMutation = useMutation({
    mutationFn: inventoryApi.createMaterialRemainder,
    onSuccess: async () => {
      setRemainderOpen(false)
      remainderForm.resetFields()
      await queryClient.invalidateQueries({ queryKey: ['inventory', 'material-remainders'] })
      message.success('胶料余料已登记到冰箱记录')
    },
    onError: (error: Error) => message.error(error.message),
  })
  const remainderUseMutation = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Record<string, unknown> }) => inventoryApi.useMaterialRemainder(id, body),
    onSuccess: async () => {
      setRemainderUseTarget(undefined)
      remainderUseForm.resetFields()
      await queryClient.invalidateQueries({ queryKey: ['inventory', 'material-remainders'] })
      message.success('胶料使用记录已保存')
    },
    onError: (error: Error) => message.error(error.message),
  })
  const moveMutation = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Record<string, unknown> }) => inventoryApi.moveContainer(id, body),
    onSuccess: async () => {
      setMoveTarget(undefined)
      moveForm.resetFields()
      await queryClient.invalidateQueries({ queryKey: ['inventory'] })
      message.success('库存容器已移库，并保留移库流水')
    },
    onError: (error: Error) => message.error(error.message),
  })

  const locations = locationsQuery.data || EMPTY_LOCATIONS
  const finishedLocations = useMemo(() => locations.filter((location) => location.rack_type !== 'FRIDGE'), [locations])
  const visibleLocations = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return finishedLocations
    return finishedLocations.filter((location) => {
      const item = location.container
      return location.code.toLowerCase().includes(needle)
        || item?.product_code?.toLowerCase().includes(needle)
        || item?.product_name?.toLowerCase().includes(needle)
        || item?.specification?.toLowerCase().includes(needle)
        || item?.material?.toLowerCase().includes(needle)
    })
  }, [finishedLocations, query])
  const grouped = useMemo(() => {
    const result = new Map<string, InventoryLocation[]>()
    visibleLocations.forEach((location) => {
      const rows = result.get(location.rack_code) || []
      rows.push(location)
      result.set(location.rack_code, rows)
    })
    return [...result.entries()]
  }, [visibleLocations])
  const containerOptions = (containersQuery.data || []).filter((item) => item.batch.quality_status === 'PASSED')
    .map((item) => ({ value: item.id, label: `${item.container_code} · ${item.batch.product.specification || item.batch.product.product_name} · 可出 ${item.quantity}` }))
  const orderOptions = (ordersQuery.data || []).map((order: Order) => ({ value: order.id, label: `${order.order_no}${order.item_no ? ` / ${order.item_no}` : ''} · ${order.specification}` }))
  const moveLocationOptions = moveTarget ? finishedLocations.filter((location) => location.id !== moveTarget.currentLocationId && !location.container && (moveTarget.containerType === 'BASKET' ? location.allows_basket : location.allows_bag)).map((location) => ({ value: location.id, label: `${location.code}${location.allows_basket ? ' · 可放筐/袋' : ' · 袋位'}` })) : []

  const locationColumns: TableColumnsType<InventoryLocation> = [
    { title: '库位', dataIndex: 'code', width: 140 },
    { title: '容器', key: 'container', width: 130, render: (_, row) => row.container?.container_code || '空位' },
    { title: '产品 / 规格', key: 'product', width: 230, render: (_, row) => row.container ? <span>{row.container.product_name || row.container.product_code || '-'}<br /><Typography.Text type="secondary">{row.container.specification || '-'} · {row.container.material || '-'}</Typography.Text></span> : '-' },
    { title: '数量', key: 'quantity', width: 100, render: (_, row) => row.container?.quantity ?? '-' },
    { title: '质量状态', key: 'quality', width: 110, render: (_, row) => qualityTag(row.container?.quality_status) },
    { title: '最近品检员', key: 'inspector', width: 120, render: (_, row) => row.container?.inspector_name || '未登记' },
    { title: '操作', key: 'actions', width: 170, render: (_, row) => row.container ? <Space size={0}><Button type="link" size="small" onClick={() => { setQualityTarget(row.container); qualityForm.setFieldsValue({ quality_status: row.container?.quality_status, inspector_id: undefined, inspected_on: new Date().toISOString().slice(0, 10) }) }}>改质量状态</Button><Button type="link" size="small" onClick={() => { setMoveTarget({ containerId: row.container!.id, containerCode: row.container!.container_code, containerType: row.container!.container_type, currentLocationId: row.id }); moveForm.resetFields() }}>移库</Button></Space> : null },
  ]

  const submitReceipt = async () => {
    const values = await receiptForm.validateFields()
    const productId = values.product_id
    const body: Record<string, unknown> = {
      ...values,
      product_id: productId || undefined,
      product: productId ? undefined : {
        product_code: values.product_code || '',
        product_name: values.product_name || '',
        specification: values.specification || '',
        material: values.material || '',
        unit_weight_g: values.unit_weight_g || undefined,
      },
    }
    delete body.product_code
    delete body.product_name
    delete body.specification
    delete body.material
    delete body.unit_weight_g
    receiptMutation.mutate(body)
  }

  const submitOutbound = async () => {
    const values = await outboundForm.validateFields()
    outboundMutation.mutate({
      outbound_no: values.outbound_no || undefined,
      order_id: values.order_id || undefined,
      shipment_ref: values.shipment_ref || '',
      note: values.note || '',
      lines: values.lines,
    })
  }

  const submitQuality = async () => {
    if (!qualityTarget) return
    const values = await qualityForm.validateFields()
    qualityMutation.mutate({ id: qualityTarget.id, body: values })
  }

  const submitRemainder = async () => {
    const values = await remainderForm.validateFields()
    remainderMutation.mutate({
      material: values.material,
      weight_kg: values.weight_kg,
      stored_on: values.stored_on || undefined,
      fridge_code: 'F01-冰箱',
      note: values.note || '',
    })
  }

  const submitRemainderUse = async () => {
    if (!remainderUseTarget) return
    const values = await remainderUseForm.validateFields()
    remainderUseMutation.mutate({ id: remainderUseTarget.id, body: { weight_kg: values.weight_kg, note: values.note || '' } })
  }

  const submitMove = async () => {
    if (!moveTarget) return
    const values = await moveForm.validateFields()
    moveMutation.mutate({ id: moveTarget.containerId, body: { location_id: values.location_id, reason: values.reason || '' } })
  }

  const remainderColumns: TableColumnsType<MaterialRemainder> = [
    { title: '胶料材质', dataIndex: 'material', width: 180 },
    { title: '原始重量(kg)', dataIndex: 'weight_kg', width: 125 },
    { title: '剩余重量(kg)', dataIndex: 'remaining_weight_kg', width: 125, render: (value) => <Typography.Text strong>{value}</Typography.Text> },
    { title: '入冰箱时间', dataIndex: 'stored_on', width: 180, render: (value) => new Date(value).toLocaleString('zh-CN', { hour12: false }) },
    { title: '记录人', dataIndex: 'created_by_name', width: 110 },
    { title: '备注', dataIndex: 'note', ellipsis: true },
    { title: '操作', key: 'actions', width: 100, render: (_, row) => Number(row.remaining_weight_kg) > 0 ? <Button type="link" size="small" onClick={() => { setRemainderUseTarget(row); remainderUseForm.resetFields() }}>登记使用</Button> : <Typography.Text type="secondary">已用完</Typography.Text> },
  ]

  return (
    <div className="page-container inventory-page">
      <PageTitle
        title="成品库存"
        description="库存直接入库、人工选取出库；流程卡只在品检出货页面单独扫描，不会重复扣减库存。"
        extra={<Space wrap className="inventory-actions"><Select aria-label="标签尺寸" value={labelSize} onChange={setLabelSize} options={[{ value: '100x50', label: '标签 100×50 mm' }, { value: '70x30', label: '标签 70×30 mm' }, { value: '50x30', label: '标签 50×30 mm' }]} /><Button icon={<PrinterOutlined />} onClick={() => window.print()}>打印库位标签</Button><Button icon={<SwapOutlined />} onClick={() => setOutboundOpen(true)}>库存出库</Button><Button type="primary" icon={<InboxOutlined />} onClick={() => setReceiptOpen(true)}>直接入库</Button></Space>}
      />

      <Row gutter={[12, 12]} className="inventory-stats">
        <Col xs={12} sm={6}><Card><Statistic title="库存总量" value={summaryQuery.data?.total_quantity ?? 0} suffix="件" /></Card></Col>
        <Col xs={12} sm={6}><Card><Statistic title="已检可用" value={summaryQuery.data?.available_quantity ?? 0} suffix="件" /></Card></Col>
        <Col xs={12} sm={6}><Card><Statistic title="待检库存" value={summaryQuery.data?.waiting_inspection_quantity ?? 0} suffix="件" /></Card></Col>
        <Col xs={12} sm={6}><Card><Statistic title="占用库位" value={`${summaryQuery.data?.occupied_locations ?? 0} / ${summaryQuery.data?.location_count ?? 0}`} /></Card></Col>
      </Row>

      {!finishedLocations.length && !locationsQuery.isLoading ? <Card><Empty description="还没有初始化库存固定库位"><Button type="primary" onClick={() => bootstrapMutation.mutate()} loading={bootstrapMutation.isPending}>初始化 K01-K09 库位</Button></Empty></Card> : null}

      <Card className="inventory-map-card">
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Input allowClear prefix={<SearchOutlined />} placeholder="搜索位置、产品、规格、材质或容器编号" value={query} onChange={(event) => setQuery(event.target.value)} />
          <div className="inventory-map-grid">
            {grouped.map(([rack, items]) => {
              const sorted = [...items].sort((a, b) => (b.level_no || 0) - (a.level_no || 0) || (a.position_no || 0) - (b.position_no || 0))
              return <Card key={rack} size="small" title={`${rack} · ${items[0]?.rack_type === 'SMALL' ? '小货架（袋位）' : '大货架（筐/袋位）'}`} className="inventory-rack-card">
                <div className="inventory-location-grid">
                  {sorted.map((location) => <div key={location.id} className={`inventory-location-tile ${location.container ? 'occupied' : 'empty'} ${location.container?.quality_status === 'WAITING' ? 'waiting' : ''}`}>
                    <strong>{location.code}</strong>
                    {location.container ? <><span>{location.container.product_code || location.container.product_name || '未命名产品'}</span><span className="inventory-tile-quantity">{location.container.quantity} 件</span>{qualityTag(location.container.quality_status)}</> : <Typography.Text type="secondary">空位</Typography.Text>}
                  </div>)}
                </div>
              </Card>
            })}
          </div>
        </Space>
      </Card>

      <Card title="当前容器明细" className="inventory-table-card">
        <Table rowKey="id" size="small" loading={containersQuery.isLoading} dataSource={finishedLocations} columns={locationColumns} pagination={{ pageSize: 20, showSizeChanger: true }} scroll={{ x: 850 }} />
      </Card>

      <Card
        title="冰箱胶料余料"
        className="inventory-table-card"
        extra={<Button type="primary" size="small" onClick={() => { remainderForm.setFieldsValue({ stored_on: new Date().toISOString().slice(0, 16) }); setRemainderOpen(true) }}>登记余料</Button>}
      >
        <Alert type="info" showIcon message="这里只记录订单剩余胶料的重量和使用流水，不占用成品货架库位。" style={{ marginBottom: 12 }} />
        <Table rowKey="id" size="small" loading={remaindersQuery.isLoading} dataSource={remaindersQuery.data || []} columns={remainderColumns} pagination={{ pageSize: 8 }} scroll={{ x: 900 }} locale={{ emptyText: '暂无冰箱胶料记录' }} />
      </Card>

      <div className={`inventory-label-sheet label-${labelSize}`} aria-hidden="true">
        {finishedLocations.map((location) => <div className="inventory-label" key={location.id}><strong>成品库存库位</strong><b>{location.code}</b></div>)}
      </div>

      <Modal title="库存直接入库" open={receiptOpen} onCancel={() => setReceiptOpen(false)} onOk={() => void submitReceipt()} confirmLoading={receiptMutation.isPending} width={720} okText="确认入库">
        <Alert type="info" showIcon message="本表单不需要订单或流程卡；库存批次由系统生成，可手工填写现场批次号。" style={{ marginBottom: 16 }} />
        <Form form={receiptForm} layout="vertical" initialValues={{ quality_status: 'WAITING', container_type: 'BAG', bag_count: 1 }}>
          <Form.Item label="已有产品" name="product_id"><Select allowClear showSearch optionFilterProp="label" placeholder="不选则填写临时产品资料" options={(productsQuery.data || []).map((item) => ({ value: item.id, label: `${item.product_code || item.product_name || '-'} · ${item.specification}` }))} /></Form.Item>
          <Row gutter={12}><Col span={12}><Form.Item label="产品编号" name="product_code"><Input /></Form.Item></Col><Col span={12}><Form.Item label="产品名称" name="product_name"><Input /></Form.Item></Col></Row>
          <Row gutter={12}><Col span={12}><Form.Item label="规格" name="specification"><Input /></Form.Item></Col><Col span={12}><Form.Item label="材质" name="material"><Input /></Form.Item></Col></Row>
          <Row gutter={12}><Col span={8}><Form.Item label="成品单重(g)" name="unit_weight_g"><InputNumber min={0} precision={5} style={{ width: '100%' }} /></Form.Item></Col><Col span={8}><Form.Item label="容器类型" name="container_type" rules={[{ required: true }]}><Select options={[{ value: 'BAG', label: '袋' }, { value: 'BASKET', label: '筐' }]} /></Form.Item></Col><Col span={8}><Form.Item label="库位" name="location_id" rules={[{ required: true, message: '请选择库位' }]}><Select showSearch optionFilterProp="label" options={finishedLocations.filter((item) => !item.container).map((item) => ({ value: item.id, label: `${item.code}${item.allows_basket ? ' · 可放筐/袋' : ' · 袋位'}` }))} /></Form.Item></Col></Row>
          <Row gutter={12}><Col span={8}><Form.Item label="总数量（件）" name="quantity" rules={[{ required: true, type: 'number', min: 1 }]}><InputNumber min={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col><Col span={8}><Form.Item label="筐内/容器袋数" name="bag_count"><InputNumber min={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col><Col span={8}><Form.Item label="每袋数量" name="pieces_per_bag"><InputNumber min={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col></Row>
          <Row gutter={12}><Col span={8}><Form.Item label="质量状态" name="quality_status" rules={[{ required: true }]}><Select options={Object.entries(QUALITY_META).map(([value, meta]) => ({ value, label: meta.label }))} /></Form.Item></Col><Col span={8}><Form.Item label="品检员（已检时必填）" name="inspector_id"><Select allowClear showSearch optionFilterProp="label" loading={employeesQuery.isLoading} options={(employeesQuery.data || []).map((employee: QualityEmployee) => ({ value: employee.id, label: `${employee.employee_no} · ${employee.name}` }))} /></Form.Item></Col><Col span={8}><Form.Item label="库存批次号" name="batch_no"><Input placeholder="留空自动生成" /></Form.Item></Col></Row>
          <Form.Item label="来源说明" name="source_note"><Input.TextArea rows={2} placeholder="例如：前批生产剩余、现场期初盘点" /></Form.Item>
        </Form>
      </Modal>

      <Modal title="库存出库" open={outboundOpen} onCancel={() => setOutboundOpen(false)} onOk={() => void submitOutbound()} confirmLoading={outboundMutation.isPending} width={620} okText="确认出库">
        <Alert type="warning" showIcon message="这里只扣减已检库存；确认后请到品检出货页面单独扫描流程卡。" style={{ marginBottom: 16 }} />
        <Form form={outboundForm} layout="vertical" initialValues={{ lines: [{}] }}>
          <Form.List name="lines">
            {(fields, { add, remove }) => <Space direction="vertical" size={8} style={{ width: '100%' }}>
              {fields.map(({ key, name, ...restField }) => <Row gutter={8} key={key} align="middle">
                <Col flex="1"><Form.Item {...restField} label={name === 0 ? '出库容器' : undefined} name={[name, 'container_id']} rules={[{ required: true, message: '请选择容器' }]}><Select showSearch optionFilterProp="label" options={containerOptions} /></Form.Item></Col>
                <Col flex="145px"><Form.Item {...restField} label={name === 0 ? '出库数量（件）' : undefined} name={[name, 'quantity']} rules={[{ required: true, type: 'number', min: 1 }]}><InputNumber min={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
                <Col flex="32px">{fields.length > 1 ? <Button type="text" danger icon={<MinusCircleOutlined />} aria-label="移除出库行" onClick={() => remove(name)} /> : null}</Col>
              </Row>)}
              <Button type="dashed" block icon={<PlusOutlined />} onClick={() => add()}>添加另一个容器</Button>
            </Space>}
          </Form.List>
          <Row gutter={12}><Col span={12}><Form.Item label="关联订单（可选）" name="order_id"><Select allowClear showSearch optionFilterProp="label" loading={ordersQuery.isLoading} options={orderOptions} /></Form.Item></Col><Col span={12}><Form.Item label="出库单号" name="outbound_no"><Input placeholder="留空自动生成" /></Form.Item></Col></Row>
          <Form.Item label="装车/出货参考号" name="shipment_ref"><Input /></Form.Item>
          <Form.Item label="备注" name="note"><Input.TextArea rows={2} /></Form.Item>
        </Form>
      </Modal>

      <Modal title={`更新库存质量状态${qualityTarget ? ` · ${qualityTarget.container_code}` : ''}`} open={!!qualityTarget} onCancel={() => setQualityTarget(undefined)} onOk={() => void submitQuality()} confirmLoading={qualityMutation.isPending} okText="保存状态">
        <Alert type="info" showIcon message="待检库存通过检验后才会进入可用库存；这里不需要订单或流程卡。" style={{ marginBottom: 16 }} />
        <Form form={qualityForm} layout="vertical">
          <Form.Item label="质量状态" name="quality_status" rules={[{ required: true }]}><Select options={Object.entries(QUALITY_META).map(([value, meta]) => ({ value, label: meta.label }))} /></Form.Item>
          <Form.Item label="品检员" name="inspector_id" rules={[{ required: true, message: '请选择品检员' }]}><Select showSearch optionFilterProp="label" loading={employeesQuery.isLoading} options={(employeesQuery.data || []).map((employee: QualityEmployee) => ({ value: employee.id, label: `${employee.employee_no} · ${employee.name}` }))} /></Form.Item>
          <Form.Item label="检验日期" name="inspected_on" rules={[{ required: true, message: '请选择检验日期' }]}><Input type="date" /></Form.Item>
        </Form>
      </Modal>

      <Modal title={`库存移库${moveTarget ? ` · ${moveTarget.containerCode}` : ''}`} open={!!moveTarget} onCancel={() => setMoveTarget(undefined)} onOk={() => void submitMove()} confirmLoading={moveMutation.isPending} okText="确认移库">
        <Alert type="info" showIcon message="移库只改变固定库位，不改变产品、批次、质量状态或库存数量。" style={{ marginBottom: 16 }} />
        <Form form={moveForm} layout="vertical">
          <Form.Item label="目标库位" name="location_id" rules={[{ required: true, message: '请选择目标库位' }]}><Select showSearch optionFilterProp="label" options={moveLocationOptions} /></Form.Item>
          <Form.Item label="移库原因" name="reason"><Input.TextArea rows={2} placeholder="例如：货架整理、腾挪库位" /></Form.Item>
        </Form>
      </Modal>

      <Modal title="登记冰箱胶料余料" open={remainderOpen} onCancel={() => setRemainderOpen(false)} onOk={() => void submitRemainder()} confirmLoading={remainderMutation.isPending} okText="保存记录">
        <Form form={remainderForm} layout="vertical">
          <Row gutter={12}><Col span={12}><Form.Item label="胶料材质" name="material" rules={[{ required: true, message: '请填写胶料材质' }]}><Input placeholder="例如：PP、ABS" /></Form.Item></Col><Col span={12}><Form.Item label="重量(kg)" name="weight_kg" rules={[{ required: true, type: 'number', min: 0.001, message: '请输入大于0的重量' }]}><InputNumber min={0.001} precision={3} style={{ width: '100%' }} /></Form.Item></Col></Row>
          <Form.Item label="入冰箱时间" name="stored_on"><Input type="datetime-local" /></Form.Item>
          <Form.Item label="备注" name="note"><Input.TextArea rows={2} placeholder="可填写来源订单或颜色等补充信息" /></Form.Item>
        </Form>
      </Modal>

      <Modal title={`登记胶料使用${remainderUseTarget ? ` · ${remainderUseTarget.material}` : ''}`} open={!!remainderUseTarget} onCancel={() => setRemainderUseTarget(undefined)} onOk={() => void submitRemainderUse()} confirmLoading={remainderUseMutation.isPending} okText="保存使用记录">
        <Alert type="info" showIcon message={`当前剩余 ${remainderUseTarget?.remaining_weight_kg ?? 0} kg`} style={{ marginBottom: 16 }} />
        <Form form={remainderUseForm} layout="vertical">
          <Form.Item label="使用重量(kg)" name="weight_kg" rules={[{ required: true, type: 'number', min: 0.001, message: '请输入大于0的重量' }]}><InputNumber min={0.001} max={Number(remainderUseTarget?.remaining_weight_kg || 0)} precision={3} style={{ width: '100%' }} /></Form.Item>
          <Form.Item label="使用说明" name="note"><Input.TextArea rows={2} /></Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
