import { ExperimentOutlined } from '@ant-design/icons'
import { Alert, App, Button, Drawer, Form, Input, Select, Space, Typography } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { ApiError, moldApi, toList } from '../api/client'
import { productionStationGroupLabel, productionStationNumber } from '../production'
import type { MoldAsset, ProductionBoardStation } from '../types'
import { moldCode, moldLocation, moldModelOf } from '../types'

interface Props {
  open: boolean
  station?: ProductionBoardStation
  continueToProduction?: boolean
  onClose: () => void
  onAfterOpenChange?: (open: boolean) => void
  onSuccess?: (mold: MoldAsset) => void
}

export function QuickMountDrawer({ open, station, continueToProduction = false, onClose, onAfterOpenChange, onSuccess }: Props) {
  const [form] = Form.useForm()
  const queryClient = useQueryClient()
  const { message, modal } = App.useApp()
  const activeStation = station
  const moldsQuery = useQuery({
    queryKey: ['molds', 'quick-mount', 'in-stock'],
    queryFn: async () => toList(await moldApi.list({ status: 'IN_STOCK', page_size: 1000 })),
    enabled: open,
  })

  useEffect(() => {
    if (open) form.resetFields()
  }, [form, open, activeStation?.id])

  const mutation = useMutation({
    mutationFn: ({ moldId, note, confirmWarnings }: { moldId: number; note: string; confirmWarnings: boolean }) => (
      moldApi.action(moldId, 'load-machine', {
        machine_id: activeStation!.machine!.id,
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
      message.success(continueToProduction
        ? `模具 ${moldCode(result)} 已上机，请继续登记生产订单`
        : `模具 ${moldCode(result)} 已快速上机；未创建生产订单，可直接试模或稍后登记生产`)
      onClose()
      onSuccess?.(result)
    },
  })

  const submit = async (confirmWarnings = false) => {
    if (!activeStation?.machine) {
      message.error('该站位尚未关联标准机台，不能执行快速上机')
      return
    }
    try {
      const values = await form.validateFields()
      await mutation.mutateAsync({
        moldId: Number(values.mold_id),
        note: String(values.note || '').trim(),
        confirmWarnings,
      })
    } catch (error) {
      if (!(error instanceof ApiError)) {
        message.error((error as Error).message || '快速上机失败，请刷新后重试')
        return
      }
      const warnings = error.data?.warnings
      if (!confirmWarnings && error.status === 409 && Array.isArray(warnings) && warnings.length) {
        modal.confirm({
          title: '需要确认叠放风险',
          content: <div>{warnings.map((warning: string) => <Typography.Paragraph key={warning}>{warning}</Typography.Paragraph>)}</div>,
          okText: '已检查，继续上机',
          cancelText: '返回检查',
          onOk: () => submit(true),
        })
      } else {
        message.error(error.message)
      }
    }
  }

  const stationLabel = activeStation
    ? `${productionStationGroupLabel(activeStation.group)}-${productionStationNumber(activeStation)}号机台`
    : '机台'
  const plannedMoldId = activeStation?.run?.status === 'PLANNED' ? activeStation.run.mold_id : undefined
  const selectableMolds = (moldsQuery.data || []).filter((mold) => !plannedMoldId || mold.id === plannedMoldId)
  return (
    <Drawer
      open={open}
      onClose={onClose}
      afterOpenChange={onAfterOpenChange}
      size={520}
      title={<Space><ExperimentOutlined /><span>{stationLabel} · {continueToProduction ? '选择模具并上机' : '快速上机 / 试模'}</span></Space>}
      className="quick-mount-drawer"
      footer={
        <Space className="drawer-footer-actions">
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" loading={mutation.isPending} disabled={!activeStation?.machine} onClick={() => submit(false)}>
            {continueToProduction ? '确认上机并继续' : '确认快速上机'}
          </Button>
        </Space>
      }
    >
      <Alert
        type={activeStation?.machine ? 'info' : 'error'}
        showIcon
        title={activeStation?.machine
          ? plannedMoldId
            ? '该机台已有待上机计划，只能先上机计划关联的模具；如需试其他模具，请先修改或取消计划。'
            : (continueToProduction ? '此步只完成模具上机，下一步再登记生产订单。' : '快速上机不会生成生产订单，适合试模、临时调整或先上机后补录。')
          : '该站位未关联标准机台，请先完成机台配置。'}
      />
      <Form form={form} layout="vertical" requiredMark="optional" className="quick-mount-form">
        <Form.Item name="mold_id" label="在库模具" rules={[{ required: true, message: '请选择需要上机的模具' }]}>
          <Select
            showSearch
            optionFilterProp="label"
            loading={moldsQuery.isLoading}
            placeholder="按模具编号、型号或产品名称搜索"
            notFoundContent={moldsQuery.isLoading ? '正在读取模具...' : plannedMoldId ? '计划模具当前不在库，请先检查模具状态' : '没有可上机的在库模具'}
            options={selectableMolds.map((mold) => {
              const model = moldModelOf(mold)
              return {
                value: mold.id,
                label: `${moldCode(mold)} · ${model?.code || '-'} · ${model?.product_name || '-'} · ${moldLocation(mold)}`,
              }
            })}
          />
        </Form.Item>
        <Form.Item name="note" label="操作备注">
          <Input.TextArea rows={3} maxLength={300} showCount placeholder={continueToProduction ? '例如：正式生产，订单随后登记' : '例如：试模、换胶料验证'} />
        </Form.Item>
      </Form>
    </Drawer>
  )
}
