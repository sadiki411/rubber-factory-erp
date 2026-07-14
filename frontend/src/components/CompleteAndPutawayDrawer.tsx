import { HomeOutlined } from '@ant-design/icons'
import { Alert, App, Button, Drawer, Form, Input, Select, Space, Typography } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import dayjs from 'dayjs'
import { useEffect } from 'react'
import { ApiError, productionApi, slotApi, toList } from '../api/client'
import { productionStationGroupLabel, productionStationNumber } from '../production'
import type { ProductionRun } from '../types'

interface Props {
  open: boolean
  run?: ProductionRun
  onClose: () => void
  onSuccess?: (run: ProductionRun) => void
}

export function CompleteAndPutawayDrawer({ open, run, onClose, onSuccess }: Props) {
  const [form] = Form.useForm()
  const queryClient = useQueryClient()
  const { message, modal } = App.useApp()
  const slotsQuery = useQuery({
    queryKey: ['slots', 'available'],
    queryFn: async () => toList(await slotApi.list(true)),
    enabled: open,
  })

  useEffect(() => {
    if (open) form.resetFields()
  }, [form, open, run?.id])

  const mutation = useMutation({
    mutationFn: ({ slotId, note, confirmWarnings, unloadedAt }: { slotId: number; note: string; confirmWarnings: boolean; unloadedAt: string }) => (
      productionApi.completeAndPutaway(run!.id, {
        slot_id: slotId,
        unloaded_at: unloadedAt,
        note,
        confirm_warnings: confirmWarnings,
      })
    ),
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
      message.success(`生产已结束，模具 ${result.mold?.model_code || result.mold?.asset_code || ''} 已下机归位`)
      onSuccess?.(result)
      onClose()
    },
  })

  const submit = async (confirmWarnings = false, unloadedAt = dayjs().toISOString()) => {
    try {
      const values = await form.validateFields()
      await mutation.mutateAsync({
        slotId: Number(values.slot_id),
        note: String(values.note || '').trim(),
        confirmWarnings,
        unloadedAt,
      })
    } catch (error) {
      if (!(error instanceof ApiError)) {
        message.error(`${(error as Error).message || '网络异常'}；请刷新确认状态后重试。`)
        return
      }
      const warnings = error.data?.warnings
      if (!confirmWarnings && error.status === 409 && Array.isArray(warnings) && warnings.length) {
        modal.confirm({
          title: '需要确认叠放风险',
          content: <div>{warnings.map((warning: string) => <Typography.Paragraph key={warning}>{warning}</Typography.Paragraph>)}</div>,
          okText: '已检查，结束生产并归位',
          cancelText: '返回检查',
          onOk: () => submit(true, unloadedAt),
        })
      } else {
        message.error(`${error.message}；本次操作未完成，生产与模具位置均保持原状。`)
      }
    }
  }

  const stationLabel = run
    ? `${productionStationGroupLabel(run.station.group)}-${productionStationNumber(run.station)}号机台`
    : '生产订单'
  return (
    <Drawer
      open={open}
      onClose={onClose}
      size={520}
      title={<Space><HomeOutlined /><span>{stationLabel} · 结束生产并下机归位</span></Space>}
      className="complete-putaway-drawer"
      footer={
        <Space className="drawer-footer-actions">
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" icon={<HomeOutlined />} loading={mutation.isPending} disabled={!run?.mold} onClick={() => submit(false)}>
            确认结束并归位
          </Button>
        </Space>
      }
    >
      <Alert
        type="info"
        showIcon
        title="选择库位并确认后，系统会一次完成结束生产、释放机台和模具归位。"
        description="在最终成功前取消、校验失败或叠放确认未通过，生产状态与模具位置都不会改变。"
      />
      <div className="operation-subject">
        <Typography.Text type="secondary">当前生产 / 模具</Typography.Text>
        <Typography.Title level={4}>{run?.order_no || '-'}</Typography.Title>
        <Typography.Text>{run?.mold ? `${run.mold.model_code} · ${run.mold.asset_code}` : '未关联模具，不能执行下机归位'}</Typography.Text>
      </div>
      <Form form={form} layout="vertical" requiredMark="optional">
        <Form.Item name="slot_id" label="目标库位" rules={[{ required: true, message: '请选择模具归位库位' }]}>
          <Select
            showSearch
            optionFilterProp="label"
            loading={slotsQuery.isLoading}
            placeholder="搜索库位编码"
            options={(slotsQuery.data || []).map((slot) => ({ value: slot.id, label: slot.display_code }))}
          />
        </Form.Item>
        <Form.Item name="note" label="操作备注">
          <Input.TextArea rows={3} maxLength={300} showCount placeholder="可填写停机、下机或归位说明" />
        </Form.Item>
      </Form>
    </Drawer>
  )
}
