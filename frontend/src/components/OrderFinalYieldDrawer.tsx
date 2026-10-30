import { Alert, App, Button, Card, Descriptions, Drawer, Form, Input, InputNumber, Space, Statistic, Typography } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { orderApi } from '../api/client'
import type { Order, OrderFinalYield } from '../types'

const STATUS_TEXT: Record<OrderFinalYield['status'], string> = {
  READY: '可计算', NO_PRODUCTION: '未记录生产数量', GROUP_INCOMPLETE: '合并组订单尚未全部完成',
  PRODUCTION_INCOMPLETE: '关联生产任务尚未结束', SOURCE_MISMATCH: '关联产品资料不一致，请核对',
}

export function OrderYieldCell({ value, onOpen }: { value?: OrderFinalYield | null; onOpen: () => void }) {
  if (!value) return <Typography.Text type="secondary">良品率未读取</Typography.Text>
  return <Space orientation="vertical" size={0}>
    {value.status === 'READY' && value.yield_percent !== null
      ? <Typography.Text type={value.over_100 ? 'warning' : undefined}>{value.combined ? '合并良品率 ' : ''}{value.yield_percent}%</Typography.Text>
      : <Typography.Text type="secondary">{STATUS_TEXT[value.status]}</Typography.Text>}
    <Button type="link" size="small" onClick={onOpen}>查看 / 补充剩余</Button>
  </Space>
}

interface Props { open: boolean; order?: Order; onClose: () => void }
interface Values { remaining_quantity?: number | null; notes?: string }

export function OrderFinalYieldDrawer({ open, order, onClose }: Props) {
  const [form] = Form.useForm<Values>()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['orders', 'final-yield', order?.id],
    queryFn: () => orderApi.finalYield(order!.id),
    enabled: open && Boolean(order), refetchOnMount: 'always', refetchOnWindowFocus: false,
  })
  const data = query.data
  const remaining = Form.useWatch('remaining_quantity', form) ?? 0
  const ready = Boolean(data?.status === 'READY' && data.production_quantity > 0 && !query.isFetching && !query.isError)
  const preview = ready && data ? (data.effective_shipped_quantity + remaining) / data.production_quantity * 100 : null
  useEffect(() => {
    if (!open) return
    form.resetFields()
    if (data) form.setFieldsValue({ remaining_quantity: data.remaining_quantity, notes: data.notes })
  }, [form, open, data])
  const mutation = useMutation({
    mutationFn: (values: Values) => orderApi.saveFinalYield(order!.id, {
      remaining_quantity: values.remaining_quantity ?? 0, notes: values.notes || '', source_token: data!.source_token,
    }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['orders'] })
      message.success('本次剩余数量已保存，良品率已重新计算；仓库库存未变动')
      onClose()
    },
    onError: (error: Error) => message.error(error.message),
  })
  return <Drawer rootClassName="order-final-yield-drawer" open={open} onClose={onClose} size={560} title={`订单良品率 · ${order?.order_no || ''}`}
    footer={<Space className="drawer-footer-actions"><Button onClick={onClose}>关闭</Button><Button onClick={() => void query.refetch()} loading={query.isFetching} disabled={mutation.isPending}>重新读取</Button><Button type="primary" disabled={!ready} loading={mutation.isPending} onClick={() => void form.validateFields().then(values => mutation.mutate(values)).catch(() => {})}>保存剩余并重算</Button></Space>}>
    <Alert type="info" showIcon title="计算口径" description="（净有效出货 + 本次剩余合格成品）÷ 实际生产件数。未填剩余按0；剩余只用于本次良品率，不增加或扣减仓库库存。" />
    {query.isError && <Alert type="error" showIcon title="良品率读取失败" description={(query.error as Error).message} action={<Button onClick={() => void query.refetch()}>重试</Button>} />}
    <Card loading={query.isFetching} size="small" style={{ marginTop: 16 }}>
      {!query.isFetching && !query.isError && data && <>
        {data.status !== 'READY' && <Alert type="warning" showIcon title={STATUS_TEXT[data.status]} description="暂不显示良品率，也不以订单量代替生产数量。" />}
        {data.scope_changed && <Alert type="warning" showIcon title="生产范围已变化" description={`原剩余${data.saved_remaining_quantity}件未沿用，当前按0计算；请重新确认本次剩余。`} />}
        {!data.scope_changed && data.source_changed && <Alert type="warning" showIcon title="生产或出货数量已变化" description="已按当前有效记录重算，请复核已保存的本次剩余数量。" />}
        {preview !== null && <Statistic title={data.combined ? '合并生产良品率' : '良品率'} value={preview} precision={2} suffix="%" />}
        {preview !== null && preview > 100 && <Alert type="warning" showIcon title="结果超过100%，请核对生产、出货和剩余数量" description="系统不会截断比例或自动修改原始数量。" />}
        <Descriptions size="small" column={1} bordered style={{ marginTop: 12 }}>
          <Descriptions.Item label="实际生产件数">{data.production_quantity} 件（有效模数 × 当时孔数）</Descriptions.Item>
          <Descriptions.Item label="净有效出货">{data.effective_shipped_quantity} 件（扣除有效退货）</Descriptions.Item>
          <Descriptions.Item label="本次剩余">{remaining} 件 · {remaining !== data.remaining_quantity ? '待保存' : data.remaining_recorded ? '已人工确认' : '未填写，按0计算'}</Descriptions.Item>
          <Descriptions.Item label="计算范围">{data.orders.length}个订单项次 / {data.run_ids.length}个生产任务{data.combined ? '（整组共享，不拆算）' : ''}</Descriptions.Item>
        </Descriptions>
        <Space orientation="vertical" style={{ marginTop: 12 }}>
          {data.orders.map(row => <Typography.Text key={row.id}>{row.order_no}{row.item_no ? ` / ${row.item_no}` : ''} · {row.specification} / {row.material} · {row.status === 'COMPLETED' ? '已完成' : row.status === 'CANCELLED' ? '已取消' : '进行中'}</Typography.Text>)}
          <Typography.Text type="secondary">生产任务：{data.run_ids.map(id => `#${id}`).join('、') || '未关联'}</Typography.Text>
          {data.confirmed_at && <Typography.Text type="secondary">上次确认：{data.confirmed_by_name} · {new Date(data.confirmed_at).toLocaleString('zh-CN')}</Typography.Text>}
        </Space>
      </>}
    </Card>
    <Form form={form} layout="vertical" disabled={!ready} style={{ marginTop: 16, display: ready ? undefined : 'none' }}>
      <Form.Item name="remaining_quantity" label="本次剩余合格成品数量（选填）" rules={[{ type: 'number', min: 0, max: 2147483647, message: '请输入不小于0的整数' }]}>
        <InputNumber min={0} max={2147483647} precision={0} suffix="件" style={{ width: '100%' }} />
      </Form.Item>
      <Form.Item name="notes" label="说明（选填）"><Input.TextArea maxLength={500} showCount rows={3} /></Form.Item>
    </Form>
  </Drawer>
}
