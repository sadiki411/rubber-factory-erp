import { ArrowLeftOutlined, ExportOutlined, HomeOutlined, InboxOutlined, SwapOutlined, ToolOutlined } from '@ant-design/icons'
import { Alert, App, Button, Card, Descriptions, Empty, Modal, Select, Skeleton, Space, Spin, Tag, Typography } from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { moldApi, slotApi, toList } from '../api/client'
import { MoldFormDrawer } from '../components/MoldFormDrawer'
import { OperationDrawer, type MoldAction } from '../components/OperationDrawer'
import { PageTitle } from '../components/PageTitle'
import { moldCode, moldLocation } from '../types'
import type { RackSlot } from '../types'

interface Props {
  readOnly?: boolean
}

function slotPosition(slot: RackSlot) {
  return [
    slot.rack_code,
    slot.level_no ? `第${slot.level_no}层` : '',
    slot.zone_label || slot.zone_code,
    slot.position_no ? `第${slot.position_no}位` : '',
    slot.stack_level > 1 ? `叠放第${slot.stack_level}层` : '',
  ].filter(Boolean).join(' · ')
}

export function MoldRackLocationPage({ readOnly = false }: Props) {
  const { slotId } = useParams<{ slotId: string }>()
  const navigate = useNavigate()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [putawayPickerOpen, setPutawayPickerOpen] = useState(false)
  const [putawayMoldId, setPutawayMoldId] = useState<number>()
  const [action, setAction] = useState<MoldAction>()
  const [editing, setEditing] = useState(false)
  const [newMoldOpen, setNewMoldOpen] = useState(false)

  const slotQuery = useQuery({
    queryKey: [readOnly ? 'public-slot' : 'slot', slotId],
    queryFn: () => readOnly ? slotApi.publicDetail(slotId!) : slotApi.detail(slotId!),
    enabled: !!slotId,
  })
  const slot = slotQuery.data
  const currentMoldId = slot?.mold?.id
  const actionMoldId = currentMoldId || putawayMoldId
  const moldQuery = useQuery({
    queryKey: ['mold', actionMoldId],
    queryFn: () => moldApi.detail(actionMoldId!),
    enabled: !readOnly && !!actionMoldId,
  })
  const machineMoldsQuery = useQuery({
    queryKey: ['molds', 'ON_MACHINE', 'slot-putaway'],
    queryFn: async () => toList(await moldApi.list({ status: 'ON_MACHINE', page_size: 1000 })),
    enabled: !readOnly && !!slot && !currentMoldId && putawayPickerOpen,
  })

  const currentMold = currentMoldId ? moldQuery.data : undefined
  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['slot', slotId] }),
      queryClient.invalidateQueries({ queryKey: ['slots'] }),
      queryClient.invalidateQueries({ queryKey: ['racks'] }),
      queryClient.invalidateQueries({ queryKey: ['mold', actionMoldId] }),
    ])
  }
  const closeAction = () => {
    setAction(undefined)
    setPutawayMoldId(undefined)
  }
  const beginPutaway = () => {
    if (!slot) return
    if (slot.mold?.id) return message.info('当前库位已有模具，请先移库或出库。')
    setPutawayPickerOpen(true)
  }
  const confirmPutaway = () => {
    if (!putawayMoldId) return message.warning('请选择需要下机归位的模具。')
    setPutawayPickerOpen(false)
    setAction('putaway')
  }

  if (!slotId) return <Alert type="error" message="缺少库位编号" />
  if (slotQuery.isLoading) return <div className="page-container"><Skeleton active /></div>
  if (slotQuery.isError || !slot) return <div className="page-container"><Alert type="error" showIcon title="库位详情读取失败" description={(slotQuery.error as Error)?.message || '库位不存在'} /><Button onClick={() => navigate('/racks')}>返回模具架</Button></div>

  return (
    <div className="page-container mold-rack-location-page">
      <PageTitle
        title={`模具架库位 · ${slot.display_code}`}
        description={readOnly ? '扫码查看固定库位当前信息（只读）。如需操作，请使用东橡 ERP 安卓 App。' : '扫描标签后可查看该固定库位当前模具，并直接进行入库、下机归位、移库、上机或出库操作。'}
        extra={!readOnly && <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/racks')}>返回模具架</Button>}
      />
      <Card className="mold-rack-location-card">
        <Descriptions bordered size="small" column={1}>
          <Descriptions.Item label="库位编号"><Typography.Text copyable strong>{slot.display_code}</Typography.Text></Descriptions.Item>
          <Descriptions.Item label="位置说明">{slotPosition(slot) || '-'}</Descriptions.Item>
          <Descriptions.Item label="库位状态">{slot.is_blocked ? <Tag color="error">禁放{slot.blocking_reason ? ` · ${slot.blocking_reason}` : ''}</Tag> : slot.mold ? <Tag color="success">已有模具</Tag> : <Tag>空库位</Tag>}</Descriptions.Item>
          <Descriptions.Item label="二维码说明">二维码绑定本库位，不随模具更换而变化。</Descriptions.Item>
        </Descriptions>

        {slot.mold && readOnly && <Descriptions bordered size="small" column={1} style={{ marginTop: 16 }}>
          <Descriptions.Item label="模具编号"><Typography.Text strong>{slot.mold.asset_code || '-'}</Typography.Text></Descriptions.Item>
          <Descriptions.Item label="模具型号">{slot.mold.model_code || '-'}</Descriptions.Item>
          <Descriptions.Item label="产品名称">{slot.mold.product_name || '-'}</Descriptions.Item>
          <Descriptions.Item label="当前状态">{slot.mold.status_label || slot.mold.status || '-'}</Descriptions.Item>
        </Descriptions>}

        {slot.mold && !readOnly && (
          <>
            {moldQuery.isLoading ? <Spin style={{ marginTop: 20 }} /> : currentMold ? <Descriptions bordered size="small" column={1} style={{ marginTop: 16 }}>
              <Descriptions.Item label="模具编号"><Typography.Text strong>{moldCode(currentMold)}</Typography.Text></Descriptions.Item>
              <Descriptions.Item label="模具型号">{currentMold.mold_model?.code || currentMold.model?.code || '-'}</Descriptions.Item>
              <Descriptions.Item label="产品名称">{currentMold.mold_model?.product_name || currentMold.model?.product_name || '-'}</Descriptions.Item>
              <Descriptions.Item label="当前状态">{currentMold.status_display || moldLocation(currentMold) || '-'}</Descriptions.Item>
              <Descriptions.Item label="备注">{currentMold.note || '-'}</Descriptions.Item>
            </Descriptions> : <Alert type="warning" showIcon style={{ marginTop: 16 }} message="库位占用模具详情暂时无法读取" description={moldQuery.error instanceof Error ? moldQuery.error.message : undefined} />}
          </>
        )}

        {!readOnly && <Space wrap className="mold-rack-location-actions">
          {!slot.mold && !slot.is_blocked && <Button type="primary" icon={<InboxOutlined />} onClick={() => setNewMoldOpen(true)}>新增模具入库</Button>}
          {!slot.mold && !slot.is_blocked && <Button icon={<HomeOutlined />} onClick={beginPutaway}>机上模具下机归位</Button>}
          {currentMold && <Button icon={<SwapOutlined />} onClick={() => setAction('move')}>修改库位</Button>}
          {currentMold && <Button icon={<ToolOutlined />} onClick={() => setAction('load-machine')}>安排上机</Button>}
          {currentMold && <Button danger icon={<ExportOutlined />} onClick={() => setAction('send-out')}>出库 / 客户收回</Button>}
          {currentMold && <Button onClick={() => setEditing(true)}>编辑模具资料</Button>}
        </Space>}
        {!slot.mold && slot.is_blocked && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={readOnly ? '该库位当前禁放' : '该库位已禁放，不能执行入库'} />}
      </Card>

      {!readOnly && <Modal title={`选择机上模具 · 归位到 ${slot.display_code}`} open={putawayPickerOpen} onCancel={() => setPutawayPickerOpen(false)} onOk={confirmPutaway} okText="下一步">
        <Alert type="info" showIcon message="确认后会直接打开下机归位操作，目标库位已预填为当前扫码库位。" style={{ marginBottom: 16 }} />
        <Select
          showSearch
          optionFilterProp="label"
          value={putawayMoldId}
          onChange={setPutawayMoldId}
          loading={machineMoldsQuery.isLoading}
          placeholder="选择当前在机台上的模具"
          style={{ width: '100%' }}
          options={(machineMoldsQuery.data || []).map((mold) => ({ value: mold.id, label: `${moldCode(mold)} · ${mold.mold_model?.code || mold.model?.code || '-'} · ${mold.machine?.code || '-'}号机台` }))}
        />
      </Modal>}

      {!readOnly && <>
        <MoldFormDrawer open={newMoldOpen} initialSlot={slot} onClose={() => setNewMoldOpen(false)} onSuccess={async () => { setNewMoldOpen(false); await refresh(); message.success('模具已入库到当前库位') }} />
        <MoldFormDrawer open={editing} mold={currentMold} onClose={() => setEditing(false)} onSuccess={async () => { setEditing(false); await refresh() }} />
        <OperationDrawer
          open={!!action && !!moldQuery.data}
          mold={moldQuery.data}
          action={action}
          initialSlotId={!currentMoldId && action === 'putaway' ? slot.id : undefined}
          onClose={closeAction}
          onSuccess={async () => { closeAction(); await refresh() }}
        />
      </>}
    </div>
  )
}

