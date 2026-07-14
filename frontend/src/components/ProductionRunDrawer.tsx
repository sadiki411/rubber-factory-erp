import { CalculatorOutlined, ClockCircleOutlined } from '@ant-design/icons'
import { Alert, App, Button, Col, DatePicker, Drawer, Form, Input, InputNumber, Row, Select, Space } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import dayjs, { type Dayjs } from 'dayjs'
import { useEffect } from 'react'
import { moldApi, productionApi, toList } from '../api/client'
import { productionStationGroupLabel, productionStationNumber, requiresProductionUnloadTime } from '../production'
import type { ProductionMold, ProductionRun, ProductionRunStatus, ProductionStation } from '../types'
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

export function ProductionRunDrawer({ open, run, station, mountedMold, initialStatus = 'RUNNING', onClose, onSuccess }: Props) {
  const [form] = Form.useForm()
  const selectedStatus = Form.useWatch('status', form)
  const selectedStationId = Form.useWatch<number>('station_id', form)
  const selectedLoadedAt = Form.useWatch('loaded_at', form)
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

  useEffect(() => {
    if (!open) return
    if (run) {
      form.resetFields()
      form.setFieldsValue({
        ...run,
        station_id: run.station?.id,
        mold_id: run.mold?.id,
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

  const selectedStation = stationsQuery.data?.find((item) => item.id === selectedStationId)
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
    const payload = {
      ...values,
      loaded_at: values.loaded_at ? values.loaded_at.toISOString() : null,
      expected_change_at: values.expected_change_at ? values.expected_change_at.toISOString() : null,
      material_changed_at: values.material_changed_at ? values.material_changed_at.toISOString() : null,
      unloaded_at: values.unloaded_at ? values.unloaded_at.toISOString() : null,
      mold_id: values.mold_id || null,
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

        <Row gutter={14}>
          <Col xs={24} sm={12}><Form.Item name="order_no" label="订单编号" rules={[{ required: true, message: '请输入订单编号' }]}><Input placeholder="例如 04-A001-2604100001" /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="operator" label="默认作业员（可选）"><Input placeholder="录入日报时可自动带出，仍可修改" /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="specification" label="规格" rules={[{ required: true, message: '请输入产品规格' }]}><Input /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="material" label="材质 / 胶料配方" rules={[{ required: true, message: '请输入材质' }]}><Input placeholder="例如 N7200" /></Form.Item></Col>
        </Row>

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
          <Col xs={12} sm={8}><Form.Item name="order_quantity" label="订单数量" rules={[{ required: true, message: '请输入订单数量' }]}><InputNumber min={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
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
          <Col xs={24} sm={12}><Form.Item name="compound_size" label="胶料尺寸"><Input placeholder="例如 450×5" /></Form.Item></Col>
          <Col xs={12} sm={6}><Form.Item name="strip_weight_kg" label="条重(kg)"><InputNumber min={0} precision={3} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={12} sm={6}><Form.Item name="strips_per_batch" label="每批条数"><InputNumber min={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={12}><Form.Item name="unit_price" label="成品单价(元/件)"><InputNumber min={0} precision={4} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={12}><Form.Item name="material_unit_price" label="材料单价(元/kg)"><InputNumber min={0} precision={4} style={{ width: '100%' }} /></Form.Item></Col>
        </Row>
        <Form.Item name="notes" label="备注"><Input.TextArea rows={3} maxLength={500} showCount /></Form.Item>
      </Form>
    </Drawer>
  )
}
