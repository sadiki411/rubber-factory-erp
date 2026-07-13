import { ExportOutlined, HomeOutlined, SwapOutlined, ToolOutlined } from '@ant-design/icons'
import { App, Button, Drawer, Form, Input, Modal, Select, Space, Typography } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import type { ReactNode } from 'react'
import { ApiError, masterApi, moldApi, slotApi, toList } from '../api/client'
import type { Machine, MoldAsset } from '../types'
import { moldCode } from '../types'

export type MoldAction = 'putaway' | 'move' | 'load-machine' | 'send-out'

const actionMeta: Record<MoldAction, { title: string; submit: string; icon: ReactNode }> = {
  putaway: { title: '模具归位', submit: '确认归位', icon: <HomeOutlined /> },
  move: { title: '库内移位', submit: '确认移位', icon: <SwapOutlined /> },
  'load-machine': { title: '安排上机', submit: '确认上机', icon: <ToolOutlined /> },
  'send-out': { title: '客户收回', submit: '确认客户收回', icon: <ExportOutlined /> },
}

interface Props {
  mold?: MoldAsset
  action?: MoldAction
  open: boolean
  onClose: () => void
  onSuccess?: (mold: MoldAsset) => void
}

export function OperationDrawer({ mold, action = 'putaway', open, onClose, onSuccess }: Props) {
  const [form] = Form.useForm()
  const queryClient = useQueryClient()
  const { message } = App.useApp()
  const meta = actionMeta[action]

  useEffect(() => {
    if (open) form.resetFields()
  }, [form, open, action, mold?.id])

  const slotsQuery = useQuery({
    queryKey: ['slots', 'available'],
    queryFn: async () => toList(await slotApi.list(true)),
    enabled: open && (action === 'putaway' || action === 'move'),
  })
  const machinesQuery = useQuery({
    queryKey: ['machines'],
    queryFn: async () => toList(await masterApi<Machine>('machines').list()),
    enabled: open && action === 'load-machine',
  })
  const mutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) => moldApi.action(mold!.id, action, payload),
    onSuccess: async (result) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['molds'] }),
        queryClient.invalidateQueries({ queryKey: ['mold'] }),
        queryClient.invalidateQueries({ queryKey: ['racks'] }),
        queryClient.invalidateQueries({ queryKey: ['slots'] }),
      ])
      message.success(`${meta.title}成功`)
      onSuccess?.(result)
      onClose()
    },
  })

  const submit = async (confirmWarnings = false) => {
    try {
      const values = await form.validateFields()
      await mutation.mutateAsync({ ...values, confirm_warnings: confirmWarnings })
    } catch (error) {
      if (error instanceof ApiError) message.error(error.message)
    }
  }

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      await mutation.mutateAsync({ ...values, confirm_warnings: false })
    } catch (error) {
      if (!(error instanceof ApiError)) return
      const warnings = error.data?.warnings
      if (error.status === 409 && Array.isArray(warnings) && warnings.length) {
        Modal.confirm({
          title: '需要确认叠放风险',
          content: <div>{warnings.map((warning: string) => <p key={warning}>{warning}</p>)}</div>,
          okText: '已检查，继续',
          cancelText: '返回检查',
          onOk: () => submit(true),
        })
      } else {
        message.error(error.message)
      }
    }
  }

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={<Space>{meta.icon}<span>{meta.title}</span></Space>}
      size={480}
      className="operation-drawer"
      footer={
        <Space className="drawer-footer-actions">
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" loading={mutation.isPending} onClick={handleSubmit}>{meta.submit}</Button>
        </Space>
      }
    >
      <div className="operation-subject">
        <Typography.Text type="secondary">当前操作模具</Typography.Text>
        <Typography.Title level={4}>{mold ? moldCode(mold) : '-'}</Typography.Title>
        <Typography.Text>{mold?.mold_model?.code || mold?.model?.code} · {mold?.mold_model?.product_name || mold?.model?.product_name}</Typography.Text>
      </div>
      <Form form={form} layout="vertical" requiredMark="optional">
        {(action === 'putaway' || action === 'move') && (
          <Form.Item name="slot_id" label="目标库位" rules={[{ required: true, message: '请选择目标库位' }]}>
            <Select
              showSearch
              optionFilterProp="label"
              loading={slotsQuery.isLoading}
              placeholder="搜索库位编码"
              options={(slotsQuery.data || []).map((slot) => ({ value: slot.id, label: slot.display_code }))}
            />
          </Form.Item>
        )}
        {action === 'load-machine' && (
          <Form.Item name="machine_id" label="机台" rules={[{ required: true, message: '请选择机台' }]}>
            <Select
              showSearch
              optionFilterProp="label"
              loading={machinesQuery.isLoading}
              placeholder="选择机台"
              options={(machinesQuery.data || []).filter((item) => item.active !== false).map((item) => ({ value: item.id, label: `${item.code} · ${item.name}` }))}
            />
          </Form.Item>
        )}
        {action === 'send-out' && <Typography.Text type="secondary">该操作表示模具由客户收回，无需选择去向。</Typography.Text>}
        <Form.Item name="note" label="操作备注">
          <Input.TextArea rows={4} maxLength={300} showCount placeholder="可填写操作原因或补充说明" />
        </Form.Item>
      </Form>
    </Drawer>
  )
}
