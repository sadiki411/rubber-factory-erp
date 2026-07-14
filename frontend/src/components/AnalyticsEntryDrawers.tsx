import { DollarOutlined, FormOutlined } from '@ant-design/icons'
import { Alert, App, Button, Col, DatePicker, Drawer, Form, Input, InputNumber, Row, Select, Space } from 'antd'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import dayjs, { type Dayjs } from 'dayjs'
import { useEffect } from 'react'
import { analyticsApi } from '../api/client'
import { reworkQuantitiesValid, shipmentQuantitiesMatch, shipmentQuantityAllowed } from '../quality'
import type { Machine, ManualFinancialEntry, ManualPerformanceEntry, ManualPerformanceEntryType, QualityEmployee } from '../types'

interface CommonProps {
  open: boolean
  machines: Machine[]
  onClose: () => void
}

interface PerformanceProps extends CommonProps {
  entry?: ManualPerformanceEntry
  employees: QualityEmployee[]
}

export function ManualPerformanceDrawer({ open, entry, machines, employees, onClose }: PerformanceProps) {
  const [form] = Form.useForm<Record<string, any>>()
  const entryType = Form.useWatch<ManualPerformanceEntryType>('entry_type', form) || 'PRODUCTION'
  const queryClient = useQueryClient()
  const { message } = App.useApp()

  useEffect(() => {
    if (!open) return
    form.resetFields()
    form.setFieldsValue(entry ? {
      ...entry,
      entry_date: dayjs(entry.entry_date),
      machine_id: entry.machine_id || entry.machine?.id,
      quality_employee_id: entry.quality_employee_id || entry.quality_employee?.id,
    } : {
      entry_date: dayjs(), entry_type: 'PRODUCTION', produced_mold_count: 0, production_hours: 0,
      inspection_quantity: 0, qualified_quantity: 0, defective_quantity: 0, shipped_quantity: 0,
      returned_quantity: 0, reworked_quantity: 0, recovered_quantity: 0, scrap_quantity: 0, rework_hours: 0,
    })
  }, [entry, form, open])

  const mutation = useMutation({
    mutationFn: (body: Record<string, unknown>) => entry
      ? analyticsApi.updateManualEntry(entry.id, body)
      : analyticsApi.createManualEntry(body),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['analytics'] })
      message.success(entry ? '手工绩效记录已更新' : '手工绩效记录已保存')
      onClose()
    },
    onError: (error: Error) => message.error(error.message),
  })

  const submit = async () => {
    const values = await form.validateFields()
    mutation.mutate({
      ...values,
      entry_date: (values.entry_date as Dayjs).format('YYYY-MM-DD'),
      machine_id: values.machine_id || null,
      quality_employee_id: values.quality_employee_id || null,
      order_no: String(values.order_no || '').trim(),
      staff_name: String(values.staff_name || '').trim(),
      notes: String(values.notes || '').trim(),
    })
  }

  const selectEmployee = (id?: number) => {
    const employee = employees.find((item) => item.id === id)
    if (employee) form.setFieldValue('staff_name', employee.name)
  }

  const changeEntryType = (value: ManualPerformanceEntryType) => {
    form.setFieldsValue({
      machine_id: value === 'PRODUCTION' && entryType === 'PRODUCTION' ? form.getFieldValue('machine_id') : undefined,
      quality_employee_id: value !== 'PRODUCTION' && value === entryType ? form.getFieldValue('quality_employee_id') : undefined,
      produced_mold_count: value === 'PRODUCTION' ? form.getFieldValue('produced_mold_count') || 0 : 0,
      production_hours: value === 'PRODUCTION' ? form.getFieldValue('production_hours') || 0 : 0,
      inspection_quantity: value === 'QUALITY' ? form.getFieldValue('inspection_quantity') || 0 : 0,
      qualified_quantity: value === 'QUALITY' ? form.getFieldValue('qualified_quantity') || 0 : 0,
      defective_quantity: value === 'QUALITY' ? form.getFieldValue('defective_quantity') || 0 : 0,
      shipped_quantity: value === 'QUALITY' ? form.getFieldValue('shipped_quantity') || 0 : 0,
      returned_quantity: value === 'REWORK' ? form.getFieldValue('returned_quantity') || 0 : 0,
      reason_category: value === 'REWORK' ? form.getFieldValue('reason_category') : undefined,
      reworked_quantity: value === 'REWORK' ? form.getFieldValue('reworked_quantity') || 0 : 0,
      recovered_quantity: value === 'REWORK' ? form.getFieldValue('recovered_quantity') || 0 : 0,
      scrap_quantity: value === 'REWORK' ? form.getFieldValue('scrap_quantity') || 0 : 0,
      rework_hours: value === 'REWORK' ? form.getFieldValue('rework_hours') || 0 : 0,
    })
  }

  return (
    <Drawer
      open={open}
      onClose={onClose}
      size={680}
      title={<Space><FormOutlined />{entry ? '编辑绩效补录' : '补录绩效'}</Space>}
      footer={<Space className="drawer-footer-actions"><Button onClick={onClose}>取消</Button><Button type="primary" loading={mutation.isPending} onClick={() => void submit()}>保存补录</Button></Space>}
    >
      <Alert className="analytics-entry-alert" type="info" showIcon title="手工记录会与系统自动数据合并分析，并始终标记为“手工补录”。" />
      <Form form={form} layout="vertical" requiredMark="optional">
        <Row gutter={14}>
          <Col xs={24} sm={12}><Form.Item name="entry_date" label="绩效日期" rules={[{ required: true, message: '请选择日期' }]}><DatePicker style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="entry_type" label="记录类型" rules={[{ required: true }]}><Select onChange={changeEntryType} options={[{ value: 'PRODUCTION', label: '生产' }, { value: 'QUALITY', label: '品检 / 出货' }, { value: 'REWORK', label: '退回 / 返工' }]} /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="staff_name" label="人员姓名" rules={[{ required: true, whitespace: true, message: '请选择或填写人员姓名' }]}><Input placeholder="可直接填写姓名" /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="order_no" label="订单号（可选）"><Input placeholder="相同订单号会进入订单汇总" /></Form.Item></Col>
        </Row>

        {entryType === 'PRODUCTION' && (
          <>
            <Form.Item name="machine_id" label="机台" rules={[{ required: true, message: '生产补录必须选择机台' }]}><Select showSearch optionFilterProp="label" options={machines.filter((item) => item.is_active !== false).map((item) => ({ value: item.id, label: `${item.code} · ${item.name}` }))} /></Form.Item>
            <Row gutter={14}>
              <Col xs={12}><Form.Item name="produced_mold_count" label="生产模数" dependencies={['production_hours']} rules={[{ validator: (_, value) => Number(value || 0) > 0 || Number(form.getFieldValue('production_hours') || 0) > 0 ? Promise.resolve() : Promise.reject(new Error('生产模数或填报工时至少填写一项')) }]}><InputNumber min={0} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
              <Col xs={12}><Form.Item name="production_hours" label="填报工时" dependencies={['produced_mold_count']}><InputNumber min={0} precision={2} suffix="小时" style={{ width: '100%' }} /></Form.Item></Col>
            </Row>
          </>
        )}

        {entryType === 'QUALITY' && (
          <>
            <Form.Item name="quality_employee_id" label="品检员工" rules={[{ required: true, message: '品检补录必须选择品检员工' }]}><Select showSearch optionFilterProp="label" onChange={selectEmployee} options={employees.filter((item) => item.is_active && ['INSPECTOR', 'BOTH'].includes(item.role)).map((item) => ({ value: item.id, label: `${item.employee_no} · ${item.name}${item.team ? ` · ${item.team}` : ''}` }))} /></Form.Item>
            <Row gutter={14}>
              <Col xs={12} sm={6}><Form.Item name="inspection_quantity" label="质检数量" rules={[{ required: true, type: 'number', min: 1, message: '质检数量必须大于0' }]}><InputNumber min={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
              <Col xs={12} sm={6}><Form.Item name="qualified_quantity" label="合格数量" rules={[{ required: true }]}><InputNumber min={0} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
              <Col xs={12} sm={6}><Form.Item name="defective_quantity" label="不良数量" dependencies={['inspection_quantity', 'qualified_quantity']} rules={[{ required: true }, { validator: (_, value) => shipmentQuantitiesMatch(Number(form.getFieldValue('inspection_quantity')), Number(form.getFieldValue('qualified_quantity')), Number(value)) ? Promise.resolve() : Promise.reject(new Error('质检数量必须等于合格 + 不良')) }]}><InputNumber min={0} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
              <Col xs={12} sm={6}><Form.Item name="shipped_quantity" label="出货数量" dependencies={['qualified_quantity']} rules={[{ required: true }, { validator: (_, value) => shipmentQuantityAllowed(Number(value), Number(form.getFieldValue('qualified_quantity'))) ? Promise.resolve() : Promise.reject(new Error('出货数量不能超过合格数量')) }]}><InputNumber min={0} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
            </Row>
          </>
        )}

        {entryType === 'REWORK' && (
          <>
            <Form.Item name="quality_employee_id" label="返工员工" rules={[{ required: true, message: '返工补录必须选择返工员工' }]}><Select showSearch optionFilterProp="label" onChange={selectEmployee} options={employees.filter((item) => item.is_active && ['REWORKER', 'BOTH'].includes(item.role)).map((item) => ({ value: item.id, label: `${item.employee_no} · ${item.name}${item.team ? ` · ${item.team}` : ''}` }))} /></Form.Item>
            <Form.Item name="reason_category" label="原因分类"><Select options={[{ value: 'APPEARANCE', label: '外观' }, { value: 'DIMENSION', label: '尺寸' }, { value: 'MATERIAL', label: '材料' }, { value: 'MIXED', label: '混料 / 混装' }, { value: 'PACKAGING', label: '包装' }, { value: 'OTHER', label: '其他' }]} /></Form.Item>
            <Row gutter={14}>
              <Col xs={12} sm={6}><Form.Item name="returned_quantity" label="退回数量" rules={[{ required: true, type: 'number', min: 1, message: '退回数量必须大于0' }]}><InputNumber min={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
              <Col xs={12} sm={6}><Form.Item name="reworked_quantity" label="返工数量" rules={[{ required: true }]}><InputNumber min={0} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
              <Col xs={12} sm={6}><Form.Item name="recovered_quantity" label="返工合格" rules={[{ required: true }]}><InputNumber min={0} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
              <Col xs={12} sm={6}><Form.Item name="scrap_quantity" label="报废数量" dependencies={['returned_quantity', 'reworked_quantity', 'recovered_quantity']} rules={[{ required: true }, { validator: (_, value) => reworkQuantitiesValid(Number(form.getFieldValue('returned_quantity')), Number(form.getFieldValue('reworked_quantity')), Number(form.getFieldValue('recovered_quantity')), Number(value)) ? Promise.resolve() : Promise.reject(new Error('须满足：合格 + 报废 ≤ 返工 ≤ 退回')) }]}><InputNumber min={0} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
              <Col xs={12}><Form.Item name="rework_hours" label="返工工时"><InputNumber min={0} precision={2} suffix="小时" style={{ width: '100%' }} /></Form.Item></Col>
            </Row>
          </>
        )}
        <Form.Item name="notes" label="备注"><Input.TextArea rows={3} maxLength={500} showCount /></Form.Item>
      </Form>
    </Drawer>
  )
}

interface FinancialProps extends CommonProps {
  entry?: ManualFinancialEntry
}

export function ManualFinancialDrawer({ open, entry, machines, onClose }: FinancialProps) {
  const [form] = Form.useForm<Record<string, any>>()
  const direction = Form.useWatch<'INCOME' | 'EXPENSE'>('direction', form) || 'INCOME'
  const queryClient = useQueryClient()
  const { message } = App.useApp()

  useEffect(() => {
    if (!open) return
    form.resetFields()
    form.setFieldsValue(entry ? { ...entry, occurred_on: dayjs(entry.occurred_on), machine_id: entry.machine_id || entry.machine?.id } : { occurred_on: dayjs(), direction: 'INCOME', category: 'SALES' })
  }, [entry, form, open])

  const mutation = useMutation({
    mutationFn: (body: Record<string, unknown>) => entry
      ? analyticsApi.updateFinancialEntry(entry.id, body)
      : analyticsApi.createFinancialEntry(body),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['analytics'] })
      message.success(entry ? '收支记录已更新' : '收支记录已保存')
      onClose()
    },
    onError: (error: Error) => message.error(error.message),
  })

  const submit = async () => {
    const values = await form.validateFields()
    mutation.mutate({
      ...values,
      occurred_on: (values.occurred_on as Dayjs).format('YYYY-MM-DD'),
      machine_id: values.machine_id || null,
      staff_name: String(values.staff_name || '').trim(),
      order_no: String(values.order_no || '').trim(),
      description: String(values.description || '').trim(),
      notes: String(values.notes || '').trim(),
    })
  }

  return (
    <Drawer open={open} onClose={onClose} size={620} title={<Space><DollarOutlined />{entry ? '编辑收支记录' : '记录收支'}</Space>} footer={<Space className="drawer-footer-actions"><Button onClick={onClose}>取消</Button><Button type="primary" loading={mutation.isPending} onClick={() => void submit()}>保存收支</Button></Space>}>
      <Alert className="analytics-entry-alert" type="info" showIcon title="自动利润按生产结算时间统计；这里的手工收支按发生日期统计，两类来源分开显示后再合计。" />
      <Form form={form} layout="vertical" requiredMark="optional">
        <Row gutter={14}>
          <Col xs={24} sm={12}><Form.Item name="occurred_on" label="发生日期" rules={[{ required: true }]}><DatePicker style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={12} sm={6}><Form.Item name="direction" label="方向" rules={[{ required: true }]}><Select onChange={(value) => { const category = form.getFieldValue('category'); if (value === 'INCOME' && ['MATERIAL', 'LABOR', 'ENERGY'].includes(category)) form.setFieldValue('category', 'SALES'); if (value === 'EXPENSE' && category === 'SALES') form.setFieldValue('category', 'OTHER') }} options={[{ value: 'INCOME', label: '收入' }, { value: 'EXPENSE', label: '支出' }]} /></Form.Item></Col>
          <Col xs={12} sm={6}><Form.Item name="category" label="分类" rules={[{ required: true }]}><Select options={(direction === 'INCOME' ? [{ value: 'SALES', label: '销售' }, { value: 'OTHER', label: '其他' }, { value: 'ADJUSTMENT', label: '调整' }] : [{ value: 'MATERIAL', label: '材料' }, { value: 'LABOR', label: '人工' }, { value: 'ENERGY', label: '能耗' }, { value: 'OTHER', label: '其他' }, { value: 'ADJUSTMENT', label: '调整' }])} /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="amount" label="金额" rules={[{ required: true, type: 'number', min: 0.01, message: '金额必须大于0' }]}><InputNumber min={0.01} precision={2} prefix="¥" style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="machine_id" label="机台（可选）"><Select allowClear showSearch optionFilterProp="label" options={machines.filter((item) => item.is_active !== false).map((item) => ({ value: item.id, label: `${item.code} · ${item.name}` }))} /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="staff_name" label="关联人员（可选）"><Input /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="order_no" label="订单号（可选）"><Input /></Form.Item></Col>
        </Row>
        <Form.Item name="description" label="收支说明" rules={[{ required: true, whitespace: true, message: '请填写收支说明' }]}><Input placeholder="例如 某订单销售收入、7月电费分摊" /></Form.Item>
        <Form.Item name="notes" label="备注"><Input.TextArea rows={3} maxLength={500} showCount /></Form.Item>
      </Form>
    </Drawer>
  )
}
