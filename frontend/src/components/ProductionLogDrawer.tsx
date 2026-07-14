import { CheckCircleOutlined, EditOutlined, FieldTimeOutlined, HomeOutlined, PlusOutlined, ToolOutlined } from '@ant-design/icons'
import { Alert, App, Button, Col, DatePicker, Descriptions, Drawer, Form, Input, InputNumber, Popconfirm, Progress, Row, Space, Table, Tag, Tooltip, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import dayjs from 'dayjs'
import { useEffect, useState } from 'react'
import { ApiError, productionApi } from '../api/client'
import { canCreateProductionDailyLog, canSettleProductionRun, defaultProductionLogDate, formatProductionDate, isProductionLogDateAllowed, productionStationGroupLabel, productionStationNumber } from '../production'
import type { ProductionDailyLog, ProductionRun } from '../types'
import { ProductionSettlement } from './ProductionSettlement'

const STATUS_META = {
  PLANNED: { text: '待上机', color: 'blue' },
  RUNNING: { text: '生产中', color: 'processing' },
  COMPLETED: { text: '已完成', color: 'success' },
  CANCELLED: { text: '已取消', color: 'default' },
} as const

function numberText(value: number | string | null | undefined, digits = 2) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed.toLocaleString('zh-CN', { maximumFractionDigits: digits }) : '-'
}

interface Props {
  open: boolean
  run?: ProductionRun
  onClose: () => void
  onRunChange: (run: ProductionRun) => void
  onEdit: (run: ProductionRun) => void
  onRequestCompleteAndPutaway?: (run: ProductionRun) => void
}

export function ProductionLogDrawer({ open, run, onClose, onRunChange, onEdit, onRequestCompleteAndPutaway }: Props) {
  const [form] = Form.useForm()
  const { message, modal } = App.useApp()
  const queryClient = useQueryClient()
  const [editingLog, setEditingLog] = useState<ProductionDailyLog>()
  const enteredMoldCount = Form.useWatch('produced_mold_count', form)

  useEffect(() => {
    if (open) {
      form.resetFields()
      form.setFieldsValue({ date: defaultProductionLogDate(run?.loaded_at, run?.unloaded_at), operator: run?.operator })
    }
  }, [form, open, run?.id, run?.loaded_at, run?.operator, run?.unloaded_at])

  const closeDrawer = () => {
    setEditingLog(undefined)
    onClose()
  }

  const logMutation = useMutation({
    mutationFn: (values: Record<string, unknown>) => {
      const payload: Partial<ProductionDailyLog> = {
        date: (values.date as dayjs.Dayjs).format('YYYY-MM-DD'),
        operator: String(values.operator || '').trim(),
        produced_mold_count: Number(values.produced_mold_count),
        notes: String(values.notes || '').trim(),
      }
      return editingLog
        ? productionApi.updateLog(run!.id, editingLog.id, payload)
        : productionApi.addLog(run!.id, payload)
    },
    onSuccess: async (result, values) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['production'] }),
        queryClient.invalidateQueries({ queryKey: ['analytics'] }),
      ])
      const wasEditing = !!editingLog
      const invalidatedSettlement = !!run?.is_settled && !result.is_settled
      setEditingLog(undefined)
      form.resetFields()
      form.setFieldsValue({ date: defaultProductionLogDate(result.loaded_at, result.unloaded_at), operator: String(values.operator || '').trim() })
      onRunChange(result)
      if (invalidatedSettlement) message.warning('生产模数已变化，原完工结算已撤销，请重新结算。')
      else message.success(wasEditing ? '生产日报已修改' : '生产日报已保存')
    },
    onError: (error: Error) => message.error(error.message),
  })
  const completeMutation = useMutation({
    mutationFn: () => productionApi.completeRun(run!.id, { unloaded_at: dayjs().toISOString() }),
    onSuccess: async (result) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['production'] }),
        queryClient.invalidateQueries({ queryKey: ['analytics'] }),
      ])
      onRunChange(result)
      message.success('生产已停止；模具仍在机台，请在看板或模具台账执行“下机并归位”')
    },
    onError: (error: Error) => message.error(error.message),
  })
  const materialChangeMutation = useMutation({
    mutationFn: () => productionApi.updateRun(run!.id, { material_changed_at: dayjs().toISOString() }),
    onSuccess: async (result) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['production'] }),
        queryClient.invalidateQueries({ queryKey: ['analytics'] }),
      ])
      onRunChange(result)
      message.success('已记录当前换料时间')
    },
    onError: (error: Error) => message.error(error.message),
  })
  const startMutation = useMutation({
    mutationFn: ({ loadedAt, confirmWarnings }: { loadedAt: string; confirmWarnings: boolean }) => productionApi.startRun(run!.id, {
      loaded_at: loadedAt,
      confirm_warnings: confirmWarnings,
    }),
    onSuccess: async (result) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['production'] }),
        queryClient.invalidateQueries({ queryKey: ['molds'] }),
        queryClient.invalidateQueries({ queryKey: ['mold'] }),
        queryClient.invalidateQueries({ queryKey: ['racks'] }),
        queryClient.invalidateQueries({ queryKey: ['slots'] }),
        queryClient.invalidateQueries({ queryKey: ['machines'] }),
        queryClient.invalidateQueries({ queryKey: ['analytics'] }),
      ])
      onRunChange(result)
      message.success(`模具 ${result.mold?.model_code || result.mold?.asset_code || ''} 已确认上机，生产已开始`)
    },
  })

  const startProduction = async (confirmWarnings = false, loadedAt = dayjs().toISOString()) => {
    try {
      await startMutation.mutateAsync({ loadedAt, confirmWarnings })
    } catch (error) {
      const warnings = error instanceof ApiError ? error.data?.warnings : undefined
      if (!confirmWarnings && error instanceof ApiError && error.status === 409 && Array.isArray(warnings) && warnings.length) {
        modal.confirm({
          title: '确认叠放风险后上机？',
          content: <div>{warnings.map((warning: string) => <Typography.Paragraph key={warning}>{warning}</Typography.Paragraph>)}</div>,
          okText: '已检查叠放风险，确认上机',
          cancelText: '返回检查',
          onOk: () => startProduction(true, loadedAt),
        })
      } else {
        message.error((error as Error).message)
      }
    }
  }

  const columns: TableColumnsType<ProductionDailyLog> = [
    { title: '日期', dataIndex: 'date', width: 105 },
    { title: '作业员', dataIndex: 'operator', width: 110 },
    { title: '生产模数', dataIndex: 'produced_mold_count', width: 105 },
    { title: '备注', dataIndex: 'notes', ellipsis: true, render: (value) => value || '-' },
    {
      title: '操作', key: 'action', width: 70, fixed: 'right',
      render: (_, log) => (
        run?.status === 'PLANNED' || (run?.status === 'CANCELLED' && !run.loaded_at)
          ? '-'
          : <Button type="link" size="small" onClick={() => { setEditingLog(log); form.setFieldsValue({ ...log, date: dayjs(log.date) }) }}>修改</Button>
      ),
    },
  ]

  const status = run ? STATUS_META[run.status] : STATUS_META.PLANNED
  const progress = Math.min(100, Math.max(0, Number(run?.progress_percent || 0)))
  const canCreateDailyLog = !!run && canCreateProductionDailyLog(run.status, !!run.loaded_at)
  const showDailyLogForm = canCreateDailyLog || !!editingLog
  const canSettle = !!run && canSettleProductionRun(run.status, !!run.loaded_at)
  const willInvalidateSettlement = !!run?.is_settled && (!editingLog || Number(enteredMoldCount) !== Number(editingLog.produced_mold_count))
  return (
    <Drawer
      open={open}
      onClose={closeDrawer}
      size={760}
      title={run ? `${productionStationGroupLabel(run.station.group)}-${productionStationNumber(run.station)}号机台 · ${run.order_no}` : '生产记录'}
      extra={run && <Button icon={<EditOutlined />} onClick={() => onEdit(run)}>编辑资料</Button>}
      footer={run && (
        <Space className="drawer-footer-actions">
          <Button onClick={closeDrawer}>关闭</Button>
          {run.status === 'PLANNED' && (run.mold ? (
            <Popconfirm
              title="确认该模具上机并开始生产？"
              description={`模具 ${run.mold.model_code} 将上到 ${productionStationNumber(run.station)}号机台，并以当前时间记录上机时间。`}
              okText="确认上机"
              cancelText="取消"
              onConfirm={() => startProduction()}
            >
              <Button type="primary" icon={<ToolOutlined />} loading={startMutation.isPending}>确认上机</Button>
            </Popconfirm>
          ) : (
            <>
              <Typography.Text type="danger">请先编辑资料并关联模具</Typography.Text>
              <Tooltip title="待上机计划必须关联具体模具">
                <span><Button type="primary" icon={<ToolOutlined />} disabled>确认上机</Button></span>
              </Tooltip>
            </>
          ))}
          {run.status === 'RUNNING' && (
            <>
              <Button icon={<FieldTimeOutlined />} loading={materialChangeMutation.isPending} onClick={() => materialChangeMutation.mutate()}>记录当前换料时间</Button>
              <Popconfirm title="确认停机并结束本次生产？" description="系统只结束本次生产并记录停机时间，模具仍保持上机状态；释放机台还需另行执行“下机并归位”。" okText="确认停机" cancelText="取消" onConfirm={() => completeMutation.mutate()}>
                <Button type="primary" icon={<CheckCircleOutlined />} loading={completeMutation.isPending}>停机 / 结束生产</Button>
              </Popconfirm>
              {run.mold && onRequestCompleteAndPutaway && (
                <Button icon={<HomeOutlined />} onClick={() => onRequestCompleteAndPutaway(run)}>结束生产并下机归位</Button>
              )}
            </>
          )}
        </Space>
      )}
    >
      {run && (
        <>
          <div className="production-run-summary">
            <div className="run-summary-head">
              <div><Typography.Text type="secondary">规格 / 材质</Typography.Text><Typography.Title level={4}>{run.specification} · {run.material}</Typography.Title></div>
              <Tag color={status.color}>{status.text}</Tag>
            </div>
            <Descriptions size="small" column={{ xs: 2, sm: 3 }}>
              <Descriptions.Item label="模具型号">{run.mold?.model_code || '-'}</Descriptions.Item>
              <Descriptions.Item label="模具编号">{run.mold?.asset_code || '-'}</Descriptions.Item>
              <Descriptions.Item label="上模时间">{formatProductionDate(run.loaded_at)}</Descriptions.Item>
              <Descriptions.Item label="预计换模">{formatProductionDate(run.expected_change_at)}</Descriptions.Item>
              <Descriptions.Item label="最近换料">{formatProductionDate(run.material_changed_at)}</Descriptions.Item>
              <Descriptions.Item label="停机时间">{formatProductionDate(run.unloaded_at)}</Descriptions.Item>
              <Descriptions.Item label="计划模数">{numberText(run.planned_mold_count, 0)}</Descriptions.Item>
              <Descriptions.Item label="已产模数">{numberText(run.produced_mold_count, 0)}</Descriptions.Item>
              <Descriptions.Item label="欠模数">{numberText(run.remaining_mold_count, 0)}</Descriptions.Item>
              <Descriptions.Item label="实际工时">{numberText(run.actual_hours)} 小时</Descriptions.Item>
              <Descriptions.Item label="结算状态"><Tag color={run.is_settled ? 'success' : 'warning'}>{run.is_settled ? '已结算' : '待结算'}</Tag></Descriptions.Item>
              <Descriptions.Item label="实际良品 / 不良">{run.is_settled ? `${numberText(run.actual_good_quantity, 0)} / ${numberText(run.actual_defective_quantity, 0)}` : '待完工结算'}</Descriptions.Item>
              <Descriptions.Item label="结算利润">{run.is_settled ? <strong className={Number(run.profit) < 0 ? 'negative-value' : 'profit-value'}>¥{numberText(run.profit)}</strong> : '待结算'}</Descriptions.Item>
            </Descriptions>
            <div className="run-progress-line"><span>订单进度</span><Progress percent={Math.round(progress)} size="small" status={progress >= 100 ? 'success' : 'active'} /></div>
          </div>

          <div className="section-heading production-log-heading">
            <div><Typography.Title level={4}>每日人员生产记录</Typography.Title><Typography.Text type="secondary">同一天可按不同作业员分别登记；交接班各填本人实际完成模数，月末自动汇总绩效。</Typography.Text></div>
            <Typography.Text type="secondary">共 {run.daily_logs?.length || 0} 条</Typography.Text>
          </div>
          <Table rowKey="id" size="small" dataSource={run.daily_logs || []} columns={columns} pagination={false} scroll={{ x: 560 }} locale={{ emptyText: '还没有人员模数记录' }} />

          {showDailyLogForm && (
            <div className="production-log-form">
              <div className="production-form-section">{editingLog ? <EditOutlined /> : <PlusOutlined />} {editingLog ? `修改 ${editingLog.date} · ${editingLog.operator}` : '登记作业员当天模数'}</div>
              <Alert
                type={willInvalidateSettlement ? 'warning' : 'info'}
                showIcon
                title={willInvalidateSettlement
                  ? '生产模数发生变化会自动撤销原结算；保存后需要重新完工结算。'
                  : '这里只记录每个人每天完成的模数；每一模只能计入一名作业员，禁止多人重复登记同一模次。良品、不良、材料、人工和能耗在订单完工后统一结算。'}
              />
              <Form form={form} layout="vertical" requiredMark="optional">
                <Row gutter={12}>
                  <Col xs={24} sm={8}><Form.Item name="date" label="生产日期" rules={[
                    { required: true },
                    {
                      validator: (_, value) => !value || isProductionLogDateAllowed(value, run.loaded_at, run.unloaded_at)
                        ? Promise.resolve()
                        : Promise.reject(new Error('生产日期必须在上模至下机（或今天）之间')),
                    },
                  ]}><DatePicker disabledDate={(current) => !isProductionLogDateAllowed(current, run.loaded_at, run.unloaded_at)} style={{ width: '100%' }} /></Form.Item></Col>
                  <Col xs={24} sm={8}><Form.Item name="operator" label="作业员" rules={[{ required: true, whitespace: true, message: '请输入作业员' }]}><Input maxLength={50} placeholder="例如 张三" /></Form.Item></Col>
                  <Col xs={24} sm={8}><Form.Item name="produced_mold_count" label="生产模数" rules={[{ required: true, message: '请输入生产模数' }]}><InputNumber min={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
                </Row>
                <Form.Item name="notes" label="备注"><Input.TextArea rows={2} maxLength={500} showCount placeholder="可记录交接、停机或异常情况" /></Form.Item>
                <Space>
                  {editingLog && <Button onClick={() => { setEditingLog(undefined); form.resetFields(); form.setFieldsValue({ date: defaultProductionLogDate(run.loaded_at, run.unloaded_at), operator: run.operator }) }}>取消修改</Button>}
                  <Button type="primary" icon={<FieldTimeOutlined />} loading={logMutation.isPending} onClick={() => form.validateFields().then((values) => logMutation.mutate(values))}>{editingLog ? '保存修改' : '保存日报'}</Button>
                </Space>
              </Form>
            </div>
          )}

          {!canSettle && run.status !== 'PLANNED' && (
            <Alert className="production-settlement-pending" type="info" showIcon title="订单完工后再统一登记良品、不良、材料、人工、能耗及其他成本，并由系统计算收入和利润。" />
          )}
          {canSettle && <ProductionSettlement run={run} onRunChange={onRunChange} />}
        </>
      )}
    </Drawer>
  )
}
