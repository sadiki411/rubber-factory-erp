import { HomeOutlined, PauseCircleOutlined, PlayCircleOutlined } from '@ant-design/icons'
import { Alert, App, Button, Checkbox, Col, Drawer, Form, Input, InputNumber, Radio, Row, Select, Space, Statistic, Typography } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import dayjs from 'dayjs'
import { useEffect, useMemo } from 'react'
import { ApiError, moldApi, productionApi, slotApi, toList } from '../api/client'
import type { MoldAsset, ProductionRun } from '../types'
import { moldModelOf } from '../types'

interface Props {
  open: boolean
  run?: ProductionRun
  onClose: () => void
  onSuccess?: (run: ProductionRun) => void
}

type PauseMode = 'ON_MACHINE' | 'UNLOADED'

function stationLabel(group?: string, position?: number, code?: string) {
  if (group && position) return `${group}组 · ${position}号机台`
  return code ? `${code}号机台` : '未命名机台'
}

export function ProductionPauseResumeDrawer({ open, run, onClose, onSuccess }: Props) {
  const [form] = Form.useForm<Record<string, any>>()
  const { message, modal } = App.useApp()
  const queryClient = useQueryClient()
  const pausing = run?.status === 'RUNNING'
  const unloaded = run?.status === 'PAUSED_UNLOADED'
  const pauseMode = Form.useWatch<PauseMode>('mode', form) || 'ON_MACHINE'
  const moldId = Form.useWatch<number>('mold_id', form)
  const cavities = Number(Form.useWatch<number>('cavities', form) || run?.cavities || 0)

  const slotsQuery = useQuery({
    queryKey: ['slots', 'available'],
    queryFn: async () => toList(await slotApi.list(true)),
    enabled: open && pausing && pauseMode === 'UNLOADED' && Boolean(run?.mold),
  })
  const stationsQuery = useQuery({
    queryKey: ['production', 'stations'],
    queryFn: async () => toList(await productionApi.stations()),
    enabled: open && unloaded,
  })
  const moldsQuery = useQuery({
    queryKey: ['molds', 'production-resume-options'],
    queryFn: async () => toList(await moldApi.list({ status: 'IN_STOCK', page_size: 1000 })),
    enabled: open && unloaded,
  })

  const remainingPieces = Math.max(0, Number(run?.order_remaining_quantity ?? run?.order_quantity ?? 0))
  const suggestedMoldCount = useMemo(
    () => cavities > 0 ? Math.max(1, Math.ceil(remainingPieces / cavities)) : undefined,
    [cavities, remainingPieces],
  )

  useEffect(() => {
    if (!open || !run) return
    form.resetFields()
    if (pausing) {
      form.setFieldsValue({ mode: 'ON_MACHINE' })
      return
    }
    form.setFieldsValue({
      station_id: run.station?.id,
      mold_id: run.mold?.id,
      cavities: run.mold?.default_cavities || run.cavities,
    })
  }, [form, open, pausing, run])

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['production'] }),
      queryClient.invalidateQueries({ queryKey: ['orders'] }),
      queryClient.invalidateQueries({ queryKey: ['molds'] }),
      queryClient.invalidateQueries({ queryKey: ['mold'] }),
      queryClient.invalidateQueries({ queryKey: ['racks'] }),
      queryClient.invalidateQueries({ queryKey: ['slots'] }),
      queryClient.invalidateQueries({ queryKey: ['machines'] }),
      queryClient.invalidateQueries({ queryKey: ['analytics'] }),
    ])
  }

  const mutation = useMutation({
    mutationFn: ({ values, confirmWarnings }: { values: Record<string, any>; confirmWarnings: boolean }) => {
      if (!run) throw new Error('未选择生产任务')
      if (pausing) {
        return productionApi.pauseRun(run.id, {
          mode: values.mode,
          slot_id: values.mode === 'UNLOADED' && run.mold ? values.slot_id : null,
          paused_at: dayjs().toISOString(),
          note: String(values.note || '').trim(),
          confirm_warnings: confirmWarnings,
        })
      }
      return productionApi.resumeRun(run.id, {
        station_id: unloaded ? values.station_id || null : undefined,
        mold_id: unloaded ? values.mold_id || null : undefined,
        cavities: unloaded ? values.cavities || undefined : undefined,
        planned_mold_count: unloaded ? values.planned_mold_count || suggestedMoldCount : undefined,
        save_cavities_as_mold_default: Boolean(values.save_cavities_as_mold_default),
        loaded_at: unloaded && values.station_id && values.mold_id ? dayjs().toISOString() : undefined,
        note: String(values.note || '').trim(),
        confirm_warnings: confirmWarnings,
      })
    },
    onSuccess: async (result) => {
      await refresh()
      onSuccess?.(result)
      if (pausing) {
        message.success(pauseMode === 'UNLOADED' ? '已暂停生产，模具已下机归位' : '已暂停生产，模具继续保留在机台')
      } else if (result.status === 'RUNNING') {
        message.success(`已建立第${result.segment_no || '-'}生产段并恢复上机`)
      } else {
        message.success(`已建立第${result.segment_no || '-'}生产段，机台或模具可稍后补录再上机`)
      }
      onClose()
    },
    onError: (error: Error) => message.error(error.message),
  })

  const submit = async (confirmWarnings = false) => {
    try {
      const values = await form.validateFields()
      await mutation.mutateAsync({ values, confirmWarnings })
    } catch (error) {
      if (!(error instanceof ApiError)) return
      const warnings = error.data?.warnings
      if (!confirmWarnings && error.status === 409 && Array.isArray(warnings) && warnings.length) {
        modal.confirm({
          title: '需要确认库位叠放风险',
          content: <div>{warnings.map((warning: string) => <Typography.Paragraph key={warning}>{warning}</Typography.Paragraph>)}</div>,
          okText: '已现场确认，继续操作',
          cancelText: '返回检查',
          onOk: () => submit(true),
        })
      }
    }
  }

  const selectMold = (id?: number) => {
    const mold = moldsQuery.data?.find((item: MoldAsset) => item.id === id)
    if (mold?.default_cavities) form.setFieldValue('cavities', mold.default_cavities)
  }

  const title = pausing ? '暂停当前生产' : run?.status === 'PAUSED_ON_MACHINE' ? '恢复生产' : '建立接续生产段'
  return (
    <Drawer
      open={open}
      onClose={onClose}
      size={620}
      title={title}
      footer={<Space className="drawer-footer-actions"><Button onClick={onClose}>取消</Button><Button type="primary" icon={pausing ? <PauseCircleOutlined /> : <PlayCircleOutlined />} loading={mutation.isPending} onClick={() => void submit()}>{pausing ? '确认暂停' : '确认恢复'}</Button></Space>}
    >
      <div className="operation-subject">
        <Typography.Text type="secondary">订单 / 产品</Typography.Text>
        <Typography.Title level={4}>{run?.order_no}{run?.order_item_no ? ` / ${run.order_item_no}` : ''}</Typography.Title>
        <Typography.Text>{run?.specification} · {run?.material || '材质未记录'}</Typography.Text>
      </div>
      <Form form={form} layout="vertical" requiredMark="optional">
        {pausing ? <>
          <Alert type="info" showIcon title="暂停不会清除已经登记的模数" description="以后恢复时会保留原生产段，并按这个订单剩余件数建立新的生产段。" />
          <Form.Item name="mode" label="这次怎么暂停" rules={[{ required: true }]}>
            <Radio.Group className="production-pause-options">
              <Radio.Button value="ON_MACHINE"><PauseCircleOutlined /> 暂时停机，不下模</Radio.Button>
              <Radio.Button value="UNLOADED"><HomeOutlined /> 暂停并下机归位</Radio.Button>
            </Radio.Group>
          </Form.Item>
          {pauseMode === 'UNLOADED' && run?.mold && <Form.Item name="slot_id" label="模具归位库位" rules={[{ required: true, message: '请选择模具归位库位' }]}>
            <Select showSearch optionFilterProp="label" loading={slotsQuery.isLoading} placeholder="搜索库位编码" options={(slotsQuery.data || []).map((slot) => ({ value: slot.id, label: slot.display_code }))} />
          </Form.Item>}
        </> : run?.status === 'PAUSED_ON_MACHINE' ? (
          <Alert type="success" showIcon title="模具仍在原机台，可直接继续" description="恢复后沿用原机台、原模具和当前生产段，历史交接读数不会改变。" />
        ) : <>
          <Alert type="info" showIcon title="原生产段保留，新建接续生产段" description="系统按同一唯一订单已完成的件数计算剩余量；换用不同孔数的模具时，会重新建议本段目标模数。机台和模具暂不确定时可以留空，稍后补录。" />
          <Row gutter={12}>
            <Col xs={24} sm={12}><Form.Item name="station_id" label="接着在哪台做（选填）"><Select allowClear showSearch optionFilterProp="label" loading={stationsQuery.isLoading} placeholder="未确定可留空" options={(stationsQuery.data || []).map((station) => ({ value: station.id, label: stationLabel(station.group, station.position_no, station.code) }))} /></Form.Item></Col>
            <Col xs={24} sm={12}><Form.Item name="mold_id" label="接着用哪副模具（选填）"><Select allowClear showSearch optionFilterProp="label" loading={moldsQuery.isLoading} onChange={selectMold} placeholder="未确定可留空" options={(moldsQuery.data || []).map((mold) => ({ value: mold.id, label: `${mold.asset_code} · ${moldModelOf(mold)?.code || '-'} · ${moldModelOf(mold)?.product_name || '-'}` }))} /></Form.Item></Col>
          </Row>
          <Row gutter={12}>
            <Col xs={12}><Form.Item name="cavities" label="本段有效孔数" rules={[{ required: true, message: '请输入本段孔数' }]}><InputNumber min={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={12}><Form.Item name="planned_mold_count" label="本段目标模数"><InputNumber min={1} precision={0} placeholder={suggestedMoldCount ? `建议 ${suggestedMoldCount}` : undefined} style={{ width: '100%' }} /></Form.Item></Col>
          </Row>
          {suggestedMoldCount && <div className="production-ledger-target"><Statistic title={`订单剩余 ${remainingPieces.toLocaleString('zh-CN')} 件，系统建议`} value={suggestedMoldCount} suffix="模" /></div>}
          {moldId && <Form.Item name="save_cavities_as_mold_default" valuePropName="checked"><Checkbox>把本次孔数保存为这副实物模具的默认孔数</Checkbox></Form.Item>}
        </>}
        <Form.Item name="note" label="暂停 / 恢复说明"><Input.TextArea rows={3} maxLength={500} showCount placeholder="选填，例如：先换急单，后续继续" /></Form.Item>
      </Form>
    </Drawer>
  )
}
