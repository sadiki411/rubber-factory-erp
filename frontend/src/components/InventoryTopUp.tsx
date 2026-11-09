import { Alert, App, Button, Card, Col, Descriptions, Empty, Form, Input, InputNumber, Modal, Pagination, Row, Select, Space, Spin, Typography } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import dayjs from 'dayjs'
import { ApiError, inventoryApi, qualityApi, toList } from '../api/client'
import { useReadOnly } from '../access'
import type { InventoryLocation } from '../types'

const QUALITY_LABELS = { WAITING: '待检', PASSED: '已检合格', FAILED: '不合格', HOLD: '冻结' }

export function InventoryTopUpHistory({ containerId }: { containerId: number }) {
  const [page, setPage] = useState(1)
  const history = useQuery({ queryKey: ['inventory', 'top-up-history', containerId, page], queryFn: () => inventoryApi.topUpHistory(containerId, page) })
  if (history.isError) return <Alert type="error" title="追加记录读取失败" action={<Button onClick={() => void history.refetch()}>重试追加记录</Button>} />
  if (history.isPending) return <Spin tip="正在读取追加记录"><div style={{ minHeight: 60 }} /></Spin>
  return <section className="inventory-top-up-history" aria-label="追加入库记录">
    <Typography.Title level={5}>追加入库记录</Typography.Title>
    {history.data.results.length ? <Space orientation="vertical" style={{ width: '100%' }}>
      {history.data.results.map((entry) => <Card key={entry.id} size="small">
        <Typography.Text strong>{entry.received_on} · +{entry.quantity.toLocaleString()} 件</Typography.Text>
        <div>{entry.quantity_before.toLocaleString()} → {entry.quantity_after.toLocaleString()} 件（本次入库时）</div>
        <div>{entry.product_snapshot.specification || entry.product_snapshot.product_name} / {entry.product_snapshot.material || '未填写材质'}</div>
        <div>{QUALITY_LABELS[entry.quality_status]} · 本次品检员：{entry.inspector_name || '未登记'}</div>
        {entry.inspected_on && <div>检验日期：{entry.inspected_on}</div>}
        <Typography.Text type="secondary">{entry.notes} · 登记人：{entry.created_by_name}</Typography.Text>
      </Card>)}
    </Space> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无追加入库记录" />}
    {(history.data.count ?? 0) > 30 && <Pagination size="small" current={page} total={history.data.count} pageSize={30} showSizeChanger={false} onChange={setPage} />}
  </section>
}

export function InventoryTopUpModal({ target, onClose }: { target?: InventoryLocation; onClose: () => void }) {
  const readOnly = useReadOnly()
  const [form] = Form.useForm()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const requestId = useRef('')
  const submitted = useRef<Record<string, unknown> | undefined>(undefined)
  const quantity = Form.useWatch<number>('quantity', form)
  const qualityStatus = Form.useWatch<string>('quality_status', form)
  const container = target?.container
  const employees = useQuery({ queryKey: ['inventory', 'inspectors'], queryFn: async () => toList(await qualityApi.listEmployees({ page_size: 1000 })), enabled: !!target })
  const mutation = useMutation({
    retry: false,
    mutationFn: (body: Record<string, unknown>) => inventoryApi.topUp(container!.id, body),
    onSuccess: async (result) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['inventory'] }),
        queryClient.invalidateQueries({ queryKey: ['orders'] }),
        queryClient.invalidateQueries({ queryKey: ['operations'] }),
      ])
      message.success(`${result.replayed ? '该次追加已登记，未重复增加' : `本次已追加 ${result.top_up_receipt.quantity} 件`}；当前库存 ${result.quantity} 件`)
      onClose()
    },
    onError: async (error: Error) => {
      if (error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 409) submitted.current = undefined
      await queryClient.invalidateQueries({ queryKey: ['inventory'] })
      message.error(error.message)
    },
  })
  useEffect(() => {
    if (!target) return
    requestId.current = crypto.randomUUID()
    submitted.current = undefined
    form.resetFields()
    form.setFieldsValue({ received_on: dayjs().format('YYYY-MM-DD') })
    mutation.reset()
    // A new dialog is a new receipt; never reset its request key on refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, form])

  const conflict = mutation.error instanceof ApiError && mutation.error.status === 409
  const uncertainSubmission = mutation.isError && !(mutation.error instanceof ApiError && mutation.error.status >= 400 && mutation.error.status < 500 && !conflict)

  const submit = async () => {
    if (!container || !target || readOnly || mutation.isPending) return
    if (!container.updated_at) { message.error('库存资料版本未完整读取，请关闭窗口刷新后再试。'); return }
    if (submitted.current) { mutation.mutate(submitted.current); return }
    const values = await form.validateFields().catch(() => undefined)
    if (!values) return
    const body = {
      ...values,
      inspector_id: values.inspector_id ?? null,
      inspected_on: values.inspected_on || null,
      request_id: requestId.current,
      expected_updated_at: container.updated_at,
      expected_quantity: container.quantity,
      expected_location_id: target.id,
      product_identity: { id: container.product_id, product_code: container.product_code, product_name: container.product_name, specification: container.specification, material: container.material },
    }
    submitted.current = body
    mutation.mutate(body)
  }
  return <Modal className="inventory-modal inventory-top-up-modal" title={`追加入库${container ? ` · ${container.container_code}` : ''}`} open={!!target} width={640}
    onCancel={() => { if (!mutation.isPending) onClose() }} mask={{ closable: !mutation.isPending }} closable={!mutation.isPending}
    onOk={() => void submit()} confirmLoading={mutation.isPending} okButtonProps={{ disabled: readOnly || conflict }}
    okText={uncertainSubmission ? '重试本次提交' : '确认追加'} cancelText="关闭并核对库存">
    {container && <>
      <Descriptions size="small" column={1} bordered>
        <Descriptions.Item label="库位">{target?.code}</Descriptions.Item>
        <Descriptions.Item label="产品">{container.product_code} · {container.product_name}</Descriptions.Item>
        <Descriptions.Item label="规格 / 材质">{container.specification} / {container.material}</Descriptions.Item>
        <Descriptions.Item label="原筐检验状态">{QUALITY_LABELS[container.quality_status]}</Descriptions.Item>
      </Descriptions>
      <Alert style={{ margin: '12px 0' }} type="info" showIcon title="只追加同产品、同检验状态的货" description="本次日期和品检员单独保存，不覆盖原入库记录。填写本次增加的件数，不是追加后的总数。" />
      {mutation.isError && <Alert type={conflict ? 'warning' : 'error'} showIcon
        title={mutation.error.message} description={conflict ? '请关闭窗口刷新并核对库存后再操作，不要直接重复增加。' : uncertainSubmission ? '请勿另建一笔重复入库，可用原内容重试本次提交。' : '请更正本次填写内容后再提交。'} style={{ marginBottom: 12 }} />}
      <Form form={form} layout="vertical" disabled={readOnly || mutation.isPending || uncertainSubmission}>
        <Row gutter={12}>
          <Col xs={24} sm={12}><Form.Item name="quantity" label="本次增加数量（件）" rules={[{ required: true, message: '请输入本次增加件数' }]}><InputNumber min={1} max={2147483647} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="received_on" label="本次入库日期" rules={[{ required: true, message: '请选择本次入库日期' }]}><Input type="date" /></Form.Item></Col>
        </Row>
        <div className="inventory-top-up-preview" aria-live="polite">原有 {container.quantity.toLocaleString()} ＋ 本次 {Number(quantity || 0).toLocaleString()} ＝ 追加后 {(container.quantity + Number(quantity || 0)).toLocaleString()} 件</div>
        <Form.Item name="quality_status" label="确认本次检验状态" rules={[{ required: true, message: '请确认本次与原筐的检验状态一致' }]}><Select classNames={{ popup: { root: 'inventory-select-popup' } }} placeholder="核对本次货物的真实检验状态" options={[{ value: container.quality_status, label: QUALITY_LABELS[container.quality_status] }]} /></Form.Item>
        <Row gutter={12}>
          <Col xs={24} sm={12}><Form.Item name="inspector_id" label="本次品检员" rules={[{ required: qualityStatus === 'PASSED', message: '请选择本次品检员，不自动沿用原记录' }]}><Select allowClear showSearch optionFilterProp="label" loading={employees.isFetching} classNames={{ popup: { root: 'inventory-select-popup' } }} placeholder="本次单独登记" options={(employees.data || []).filter((employee) => employee.is_active).map((employee) => ({ value: employee.id, label: employee.name }))} /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="inspected_on" label="本次检验日期（可选）"><Input type="date" /></Form.Item></Col>
          <Col xs={24}><Form.Item name="bag_count" label="追加后袋数（可选）" extra="件数以上面的填写为准；不填则保留原袋数，每袋数量不自动改写。"><InputNumber min={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
        </Row>
        <Form.Item name="notes" label="本次备注（可选）"><Input.TextArea rows={2} maxLength={400} showCount /></Form.Item>
      </Form>
    </>}
  </Modal>
}
