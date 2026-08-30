import { PlusOutlined } from '@ant-design/icons'
import { Alert, App, Button, Col, DatePicker, Drawer, Form, Input, InputNumber, Progress, Row, Select, Space, Statistic, Tag, Typography } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import dayjs, { type Dayjs } from 'dayjs'
import { useEffect, useState } from 'react'
import { productionApi } from '../api/client'
import type { ProductionDailyLog, ProductionEmployee, ProductionRun } from '../types'

interface Props {
  open: boolean
  run?: ProductionRun
  log?: ProductionDailyLog
  initialValues?: Record<string, any>
  keepOpenAfterSave?: boolean
  onClose: () => void
  onSaved?: () => void | Promise<void>
}

function currentShift(): 'DAY' | 'NIGHT' {
  const hour = dayjs().hour()
  return hour >= 8 && hour < 20 ? 'DAY' : 'NIGHT'
}

function latestCumulative(run?: ProductionRun) {
  return Math.max(0, ...(run?.daily_logs || [])
    .filter((item) => !item.is_cancelled && item.counter_segment === run?.counter_segment)
    .map((item) => Number(item.cumulative_mold_count || 0)))
}

export function ProductionCounterDrawer({ open, run, log, initialValues, keepOpenAfterSave = false, onClose, onSaved }: Props) {
  const [form] = Form.useForm<Record<string, any>>()
  const { message, modal } = App.useApp()
  const queryClient = useQueryClient()
  const [newEmployeeName, setNewEmployeeName] = useState('')

  const employeesQuery = useQuery({
    queryKey: ['production', 'employees'],
    queryFn: () => productionApi.listEmployees({ active: true }),
    enabled: open,
  })

  useEffect(() => {
    if (!open) return
    form.resetFields()
    form.setFieldsValue(log ? {
      ...log,
      date: log.date ? dayjs(log.date) : null,
      operator_employee_id: typeof log.operator_employee_id === 'number' ? log.operator_employee_id : undefined,
      assistant_operator_ids: log.assistant_operators?.map((item) => item.id) || [],
    } : {
      shift: currentShift(),
      cavities_snapshot: run?.cavities,
      defective_quantity: 0,
      ...initialValues,
      date: initialValues && 'production_date' in initialValues
        ? (initialValues.production_date ? dayjs(initialValues.production_date) : null)
        : initialValues && 'date' in initialValues
          ? (initialValues.date ? dayjs(initialValues.date) : null)
          : dayjs(),
    })
  }, [form, initialValues, log, open, run])

  const closeDrawer = () => {
    setNewEmployeeName('')
    onClose()
  }

  const mutation = useMutation({
    mutationFn: ({ values, confirmWarnings }: { values: Record<string, any>; confirmWarnings: boolean }) => {
      if (!run) throw new Error('未选择生产任务')
      const body = {
        ...values,
        date: values.date ? (values.date as Dayjs).format('YYYY-MM-DD') : null,
        shift: values.shift || '',
        operator_employee_id: values.operator_employee_id || null,
        assistant_operator_ids: values.assistant_operator_ids || [],
        confirm_warnings: confirmWarnings,
      }
      return log
        ? productionApi.updateCounterLog(run.id, log.id, body)
        : productionApi.addCounterLog(run.id, body)
    },
    onSuccess: async (saved) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['production'] }),
        queryClient.invalidateQueries({ queryKey: ['orders'] }),
        queryClient.invalidateQueries({ queryKey: ['analytics'] }),
      ])
      message.success(`${log ? '交接记录已修改' : '交接已记录'}：本段 ${saved.produced_mold_count} 模`)
      await onSaved?.()
      if (!keepOpenAfterSave) closeDrawer()
    },
    onError: (error: any) => {
      const warnings = error?.data?.warnings
      if (Array.isArray(warnings) && warnings.length) {
        modal.confirm({
          title: '发现相似交接记录',
          content: warnings.join('；'),
          okText: '确认不是重复，继续保存',
          cancelText: '返回核对',
          onOk: async () => mutation.mutate({ values: await form.validateFields(), confirmWarnings: true }),
        })
        return
      }
      message.error(error.message)
    },
  })

  const createEmployee = useMutation({
    mutationFn: (name: string) => productionApi.createEmployee({ name, is_active: true }),
    onSuccess: async (employee: ProductionEmployee) => {
      await queryClient.invalidateQueries({ queryKey: ['production', 'employees'] })
      form.setFieldValue('operator_employee_id', employee.id)
      setNewEmployeeName('')
      message.success(`已新增人员：${employee.name}`)
    },
    onError: (error: Error) => message.error(error.message),
  })

  const submit = async () => mutation.mutate({ values: await form.validateFields(), confirmWarnings: false })
  const previous = latestCumulative(run)
  const progress = Math.round(Number(run?.progress_percent || 0))

  return (
    <Drawer
      className="production-counter-drawer"
      open={open}
      onClose={closeDrawer}
      size={620}
      title={log ? '修改交接累计读数' : '交接录入'}
      footer={<Space className="drawer-footer-actions"><Button onClick={closeDrawer}>取消</Button><Button type="primary" loading={mutation.isPending} onClick={() => void submit()}>保存交接</Button></Space>}
    >
      {run && <>
        <div className="production-counter-order">
          <div><Typography.Title level={4}>{run.order_no}{run.order_item_no ? ` / ${run.order_item_no}` : ''}</Typography.Title><Typography.Text>{run.specification} · {run.material || '材质未记录'}</Typography.Text></div>
          <Tag color={run.target_reached ? 'success' : 'processing'}>{run.target_reached ? '已达目标' : `还差 ${run.remaining_mold_count || 0} 模`}</Tag>
        </div>
        <Row gutter={10} className="production-counter-kpis">
          <Col span={8}><Statistic title="本段上一读数" value={previous} suffix="模" /></Col>
          <Col span={8}><Statistic title="任务累计" value={run.produced_mold_count || 0} suffix="模" /></Col>
          <Col span={8}><Statistic title="目标" value={run.planned_mold_count} suffix="模" /></Col>
        </Row>
        <Progress percent={Math.max(0, progress)} status={run.target_reached ? 'success' : 'active'} />
      </>}
      <Alert type="info" showIcon title="填写机台当前累计模数，不是本人的增量" description={`系统会自动用“本次读数－上一读数${previous}”算出本人的实际模数。机台计数清零时请先在任务卡点击“计数已清零”。`} />

      <Form form={form} layout="vertical" requiredMark="optional">
        <Form.Item name="cumulative_mold_count" label="机台当前累计模数" rules={[{ required: true, message: '请输入机台当前累计模数' }]}><InputNumber min={1} precision={0} inputMode="numeric" placeholder={`必须大于 ${previous}`} style={{ width: '100%' }} /></Form.Item>
        <Row gutter={12}>
          <Col xs={24} sm={12}><Form.Item name="date" label="生产日期（默认今天，可清空）"><DatePicker allowClear style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="shift" label="班次"><Select allowClear options={[{ value: 'DAY', label: '白班 08:00–20:00' }, { value: 'NIGHT', label: '夜班 20:00–次日08:00' }]} /></Form.Item></Col>
        </Row>
        <Row gutter={12}>
          <Col xs={24} sm={12}><Form.Item name="operator_employee_id" label="主要作业员（可后补）"><Select allowClear showSearch optionFilterProp="label" loading={employeesQuery.isLoading} placeholder="暂时不清楚可留空" options={(employeesQuery.data || []).map((item) => ({ value: item.id, label: item.name }))} /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="assistant_operator_ids" label="协助人员（选填）"><Select mode="multiple" allowClear showSearch optionFilterProp="label" placeholder="一般不填" options={(employeesQuery.data || []).map((item) => ({ value: item.id, label: item.name }))} /></Form.Item></Col>
        </Row>
        <div className="production-quick-employee"><Input value={newEmployeeName} onChange={(event) => setNewEmployeeName(event.target.value)} placeholder="人员名单没有时快速新增" /><Button icon={<PlusOutlined />} loading={createEmployee.isPending} disabled={!newEmployeeName.trim()} onClick={() => createEmployee.mutate(newEmployeeName.trim())}>新增并选中</Button></div>
        <Row gutter={12}>
          <Col xs={12}><Form.Item name="cavities_snapshot" label="本次有效孔数"><InputNumber min={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={12}><Form.Item name="defective_quantity" label="不良数量（选填）"><InputNumber min={0} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
        </Row>
        <Form.Item name="operator" label="人员姓名临时手填（选填）"><Input placeholder="不在人员名单且不想新增时使用" /></Form.Item>
        <Form.Item name="notes" label="备注"><Input.TextArea rows={3} /></Form.Item>
      </Form>
    </Drawer>
  )
}
