import { Alert, App, Button, Card, Col, Descriptions, Drawer, Form, Input, InputNumber, Row, Space, Statistic, Typography } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { productionApi } from '../api/client'
import type { ProductionRun } from '../types'

interface Props {
  open: boolean
  run?: ProductionRun
  onClose: () => void
  onSuccess?: (run: ProductionRun) => void
}

export function ProductionFinalYieldDrawer({ open, run, onClose, onSuccess }: Props) {
  const [form] = Form.useForm<Record<string, any>>()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const remaining = Number(Form.useWatch('remaining_quantity', form) || 0)
  const finalYieldQuery = useQuery({
    queryKey: ['production', 'final-yield', run?.id],
    queryFn: () => productionApi.finalYield(run!.id),
    enabled: open && Boolean(run?.id),
  })
  const snapshot = finalYieldQuery.data
  const productionQuantity = Number(snapshot?.production_quantity ?? run?.theoretical_quantity ?? 0)
  const shippedQuantity = Number(snapshot?.effective_shipped_quantity ?? 0)
  const yieldPercent = productionQuantity > 0
    ? ((shippedQuantity + remaining) / productionQuantity) * 100
    : 0

  useEffect(() => {
    if (!open) return
    form.resetFields()
    if (snapshot) form.setFieldsValue({ remaining_quantity: snapshot.remaining_quantity || 0, notes: snapshot.notes || '' })
  }, [form, open, snapshot])

  const mutation = useMutation({
    mutationFn: (values: Record<string, any>) => productionApi.saveFinalYield(run!.id, {
      remaining_quantity: Number(values.remaining_quantity || 0),
      notes: values.notes || '',
    }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['production'] }),
        queryClient.invalidateQueries({ queryKey: ['orders'] }),
        queryClient.invalidateQueries({ queryKey: ['analytics'] }),
      ])
      message.success('最终良率已保存')
      onSuccess?.(run!)
      onClose()
    },
    onError: (error: Error) => message.error(error.message),
  })

  return (
    <Drawer
      open={open}
      onClose={onClose}
      size={560}
      title={run ? `确认最终良率 · ${run.order_no}` : '确认最终良率'}
      footer={<Space className="drawer-footer-actions"><Button onClick={onClose}>取消</Button><Button type="primary" loading={mutation.isPending} disabled={!run || run.status !== 'COMPLETED'} onClick={() => form.validateFields().then((values) => mutation.mutate(values))}>保存计算结果</Button></Space>}
    >
      {run && run.status !== 'COMPLETED' && <Alert type="warning" showIcon title="请先结束生产任务" description="最终良率只能在生产任务结束后确认。" />}
      <Alert
        type="info"
        showIcon
        title="最终良率计算口径"
        description="有效出货数量由系统读取已确认出货并扣除有效退货；剩余数量由你人工确认。本次剩余数量只用于这次良率计算，暂不建立库存。"
      />
      <Card size="small" loading={finalYieldQuery.isLoading}>
        <Descriptions size="small" column={1} bordered>
          <Descriptions.Item label="实际生产件数">{productionQuantity} 件（生产模数 × 孔数）</Descriptions.Item>
          <Descriptions.Item label="有效出货数量">{shippedQuantity} 件</Descriptions.Item>
          <Descriptions.Item label="计算分子">{shippedQuantity} + {remaining} = {shippedQuantity + remaining} 件</Descriptions.Item>
        </Descriptions>
        <Row gutter={12} style={{ marginTop: 16 }}>
          <Col xs={12}><Statistic title="当前最终良率" value={yieldPercent} precision={2} suffix="%" /></Col>
          <Col xs={12}><Statistic title="人工确认剩余" value={remaining} suffix="件" /></Col>
        </Row>
      </Card>
      <Form form={form} layout="vertical" requiredMark="optional" style={{ marginTop: 16 }}>
        <Form.Item name="remaining_quantity" label="人工确认实际剩余数量" rules={[{ required: true, message: '请输入实际剩余数量，没有剩余请填写0' }, { type: 'number', min: 0, message: '剩余数量不能小于0' }]}>
          <InputNumber min={0} precision={0} style={{ width: '100%' }} addonAfter="件" />
        </Form.Item>
        <Form.Item name="notes" label="本次计算说明"><Input.TextArea rows={3} maxLength={500} showCount placeholder="例如：该合并订单已出货完成，余料按现场清点数量确认" /></Form.Item>
      </Form>
      <Typography.Text type="secondary">公式：（有效出货数量 + 人工确认剩余数量）÷ 实际生产件数 × 100%。</Typography.Text>
    </Drawer>
  )
}
