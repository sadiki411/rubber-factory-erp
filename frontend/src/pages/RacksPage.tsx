import { AppstoreOutlined, PrinterOutlined, SearchOutlined } from '@ant-design/icons'
import { Alert, App, Button, Card, Col, Empty, Form, Input, Modal, QRCode, Row, Select, Skeleton, Space, Statistic, Typography } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { moldApi, rackApi, slotApi, toList } from '../api/client'
import { PageTitle } from '../components/PageTitle'
import { RackDiagram } from '../components/RackDiagram'
import { MoldFormDrawer } from '../components/MoldFormDrawer'
import { OperationDrawer, type MoldAction } from '../components/OperationDrawer'
import { RackMoldActionsDrawer } from '../components/RackMoldActionsDrawer'
import { useMoldDeletion } from '../hooks/useMoldDeletion'
import type { MoldAsset, RackSlot, RackZone } from '../types'
import { moldCode, moldLocation } from '../types'
import { Code128Barcode } from '../components/Code128Barcode'
import { moldRackLocationDetailUrl } from '../moldRack'

type PrintLayout = 'A4' | 'THERMAL'
type PrintScope = 'ALL' | 'RACK' | 'CUSTOM'

function slotLabelDescription(slot: RackSlot) {
  return [
    slot.rack_code,
    slot.level_no ? `第${slot.level_no}层` : '',
    slot.zone_label || slot.zone_code,
    slot.position_no ? `第${slot.position_no}位` : '',
    slot.stack_level > 1 ? `叠放第${slot.stack_level}层` : '',
  ].filter(Boolean).join(' · ')
}

export function RacksPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const navigationState = location.state as { rackCode?: string; highlightMoldId?: number } | null
  const [selectedId, setSelectedId] = useState<number>()
  const [highlightMoldId, setHighlightMoldId] = useState<number | undefined>(navigationState?.highlightMoldId)
  const [search, setSearch] = useState('')
  const [targetSlot, setTargetSlot] = useState<RackSlot>()
  const [managedMoldId, setManagedMoldId] = useState<number>()
  const [managedAction, setManagedAction] = useState<MoldAction>()
  const [editingManagedMold, setEditingManagedMold] = useState(false)
  const [printOpen, setPrintOpen] = useState(false)
  const [printLayout, setPrintLayout] = useState<PrintLayout>('THERMAL')
  const [printScope, setPrintScope] = useState<PrintScope>('ALL')
  const [printRack, setPrintRack] = useState<string>()
  const [printSlotIds, setPrintSlotIds] = useState<number[]>([])
  const [labelSlots, setLabelSlots] = useState<RackSlot[]>([])
  const { confirmDelete, deleting } = useMoldDeletion()
  const racksQuery = useQuery({ queryKey: ['racks'], queryFn: async () => toList(await rackApi.list()) })
  const defaultRack = racksQuery.data?.find((rack) => rack.code === navigationState?.rackCode) || racksQuery.data?.[0]
  const effectiveSelectedId = selectedId ?? defaultRack?.id

  const layoutQuery = useQuery({
    queryKey: ['racks', effectiveSelectedId, 'layout'],
    queryFn: () => rackApi.layout(effectiveSelectedId!),
    enabled: !!effectiveSelectedId,
  })
  const slotsQuery = useQuery({
    queryKey: ['slots', 'all-for-print'],
    queryFn: async () => toList(await slotApi.list()),
    enabled: printOpen || labelSlots.length > 0,
  })
  const managedMoldQuery = useQuery({
    queryKey: ['mold', managedMoldId],
    queryFn: () => moldApi.detail(managedMoldId!),
    enabled: !!managedMoldId,
  })
  const refreshRackData = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['racks'] }),
      queryClient.invalidateQueries({ queryKey: ['slots'] }),
    ])
  }
  const capacityMutation = useMutation({
    mutationFn: ({ zone, capacity }: { zone: RackZone; capacity: number }) => rackApi.switchCapacity(effectiveSelectedId!, zone.id, capacity),
    onSuccess: async () => {
      await refreshRackData()
      message.success('容量模式已切换')
    },
    onError: (error: Error) => message.error(error.message),
  })
  const stackingMutation = useMutation({
    mutationFn: ({ zone, enabled }: { zone: RackZone; enabled: boolean }) => rackApi.switchStacking(effectiveSelectedId!, zone.id, enabled),
    onSuccess: async (_, variables) => {
      await refreshRackData()
      message.success(variables.enabled ? '叠放层已开启' : '叠放层已隐藏')
    },
    onError: (error: Error) => message.error(error.message),
  })

  const findMold = async (value: string) => {
    const keyword = value.trim()
    if (!keyword) return
    try {
      const matches = toList(await moldApi.list({ q: keyword, page_size: 20 }))
      const exact = matches.find((item) => moldCode(item).toLowerCase() === keyword.toLowerCase()) || matches[0]
      if (!exact) return message.warning('没有找到匹配的模具')
      if (exact.status !== 'IN_STOCK' || !exact.slot) return message.info(`${moldCode(exact)} 当前${moldLocation(exact)}，不在货架内`)
      const rack = racksQuery.data?.find((item) => item.code === exact.slot?.rack_code || exact.slot?.display_code.startsWith(item.code))
      if (!rack) return message.warning('已找到模具，但无法识别其货架')
      setSelectedId(rack.id)
      setHighlightMoldId(exact.id)
      message.success(`已定位到 ${exact.slot.display_code}`)
    } catch (error) {
      message.error((error as Error).message)
    }
  }

  const allSlots = useMemo(() => (slotsQuery.data || []).slice().sort((a, b) => a.display_code.localeCompare(b.display_code, undefined, { numeric: true })), [slotsQuery.data])
  const printRackCodes = useMemo(() => [...new Set(allSlots.map((slot) => slot.rack_code).filter((code): code is string => !!code))], [allSlots])
  const startLabelPrint = () => {
    let selected = allSlots
    if (printScope === 'RACK') selected = allSlots.filter((slot) => slot.rack_code === printRack)
    if (printScope === 'CUSTOM') selected = allSlots.filter((slot) => printSlotIds.includes(slot.id))
    if (!selected.length) {
      message.warning('请至少选择一个需要打印的库位。')
      return
    }
    setLabelSlots(selected)
    setPrintOpen(false)
    const previousStyle = document.getElementById('mold-rack-print-page-style')
    previousStyle?.remove()
    const style = document.createElement('style')
    style.id = 'mold-rack-print-page-style'
    style.textContent = printLayout === 'THERMAL' ? '@page { size: 60mm 40mm; margin: 0; }' : '@page { size: A4 portrait; margin: 8mm; }'
    document.head.appendChild(style)
    window.addEventListener('afterprint', () => style.remove(), { once: true })
    window.setTimeout(() => window.print(), 120)
  }

  const current = useMemo(() => racksQuery.data?.find((rack) => rack.id === effectiveSelectedId), [racksQuery.data, effectiveSelectedId])
  const layoutCounts = useMemo(() => {
    const slots = layoutQuery.data?.levels.flatMap((level) => level.zones.flatMap((zone) => zone.slots)) || []
    return {
      occupied: slots.filter((slot) => !!slot.mold).length,
      active: slots.filter((slot) => slot.active).length,
    }
  }, [layoutQuery.data])

  return (
    <div className="page-container mold-rack-page">
      <PageTitle title="货架总览" description="按实际货架结构查看模具位置；浅色格为空位，绿色格为已占用。" />
      <Card className="rack-toolbar-card">
        <div className="rack-toolbar">
          <Select
            value={effectiveSelectedId}
            loading={racksQuery.isLoading}
            placeholder="选择货架"
            onChange={(value) => { setSelectedId(value); setHighlightMoldId(undefined) }}
            options={(racksQuery.data || []).map((rack) => ({ value: rack.id, label: `${rack.code} · ${rack.name}${rack.configured === false || rack.is_configured === false ? '（待配置）' : ''}` }))}
          />
          <Input.Search
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            onSearch={findMold}
            prefix={<SearchOutlined />}
            enterButton="在货架中定位"
            placeholder="输入模具编号或型号"
          />
          <Button icon={<PrinterOutlined />} onClick={() => setPrintOpen(true)}>打印库位标签</Button>
        </div>
      </Card>

      {current && (
        <Row gutter={[12, 12]} className="rack-stats">
          <Col xs={12} sm={8}><Card><Statistic title="当前货架" value={current.code} prefix={<AppstoreOutlined />} /></Card></Col>
          <Col xs={12} sm={8}><Card><Statistic title="已放模具" value={current.occupied_count ?? layoutCounts.occupied} suffix="副" /></Card></Col>
          <Col xs={24} sm={8}><Card><Statistic title="启用库位" value={current.active_slot_count ?? layoutCounts.active} suffix="个" /></Card></Col>
        </Row>
      )}

      <Card className="rack-diagram-card">
        {layoutQuery.isError ? (
          <Alert type="error" showIcon title="货架布局读取失败" description={(layoutQuery.error as Error).message} />
        ) : current && (current.configured === false || current.is_configured === false) ? (
          <Empty description={`${current.code} 尚未配置`}><Button type="primary" onClick={() => navigate('/rack-config')}>前往配置</Button></Empty>
        ) : layoutQuery.isLoading ? <Skeleton active /> : (
          <RackDiagram
            layout={layoutQuery.data}
            highlightMoldId={highlightMoldId}
            onMoldClick={(id) => {
              setManagedMoldId(id)
              setManagedAction(undefined)
              setEditingManagedMold(false)
            }}
            onEmptySlotClick={setTargetSlot}
            onCapacityChange={(zone, capacity) => capacityMutation.mutate({ zone, capacity })}
            onStackingChange={(zone, enabled) => stackingMutation.mutate({ zone, enabled })}
          />
        )}
      </Card>
      <Space className="rack-legend" wrap>
        <span><i className="legend-dot empty" />空闲库位</span>
        <span><i className="legend-dot occupied" />已放模具</span>
        <span><i className="legend-dot highlighted" />查找目标</span>
        <Typography.Text type="secondary">容量模式仅在对应区域完全为空时可切换；“叠放”关闭时隐藏S2上层。</Typography.Text>
      </Space>
      <MoldFormDrawer
        open={!!targetSlot}
        initialSlot={targetSlot}
        onClose={() => setTargetSlot(undefined)}
        onSuccess={(mold: MoldAsset) => {
          setHighlightMoldId(mold.id)
          setTargetSlot(undefined)
        }}
      />
      <RackMoldActionsDrawer
        open={!!managedMoldId && !managedAction && !editingManagedMold}
        mold={managedMoldQuery.data}
        loading={managedMoldQuery.isLoading}
        error={managedMoldQuery.error as Error | null}
        deleting={deleting}
        onClose={() => setManagedMoldId(undefined)}
        onEdit={() => setEditingManagedMold(true)}
        onMove={() => setManagedAction('move')}
        onLoadMachine={() => setManagedAction('load-machine')}
        onRelease={() => setManagedAction('send-out')}
        onDelete={() => managedMoldQuery.data && confirmDelete(managedMoldQuery.data, { onSuccess: () => setManagedMoldId(undefined) })}
        onViewDetails={() => managedMoldId && navigate(`/molds/${managedMoldId}`)}
      />
      <MoldFormDrawer
        open={editingManagedMold}
        mold={managedMoldQuery.data}
        onClose={() => setEditingManagedMold(false)}
        onSuccess={() => {
          setEditingManagedMold(false)
          setManagedMoldId(undefined)
        }}
      />
      <OperationDrawer
        open={!!managedAction}
        mold={managedMoldQuery.data}
        action={managedAction}
        onClose={() => setManagedAction(undefined)}
        onSuccess={() => {
          setManagedAction(undefined)
          setManagedMoldId(undefined)
        }}
      />

      <div className={`mold-rack-label-sheet print-${printLayout.toLowerCase()}`} aria-hidden="true">
        {labelSlots.map((slot) => <div className="mold-rack-label" key={slot.id}>
          <div className="mold-rack-label-codes"><Code128Barcode value={slot.display_code} /><QRCode type="svg" value={moldRackLocationDetailUrl(slot.id)} bordered={false} /></div>
          <b>{slot.display_code}</b>
          <span>{slotLabelDescription(slot)}</span>
        </div>)}
      </div>

      <Modal className="mold-rack-print-modal" title="打印模具架60×40mm库位标签" open={printOpen} onCancel={() => setPrintOpen(false)} onOk={startLabelPrint} okText="打开打印窗口">
        <Alert type="info" showIcon message="每个具体库位一张标签，含库位编码、Code 128条形码和扫码后打开模具架库位详情的二维码。二维码绑定库位本身，不随模具更换而变化。" style={{ marginBottom: 16 }} />
        <Form layout="vertical">
          <Form.Item label="打印设备"><Select value={printLayout} onChange={setPrintLayout} options={[{ value: 'THERMAL', label: 'TSC TTP-244CE 热敏打印机 · 每页一张60×40mm' }, { value: 'A4', label: 'A4打印机 · 自动排列多张60×40mm标签' }]} /></Form.Item>
          <Form.Item label="打印范围"><Select value={printScope} onChange={setPrintScope} options={[{ value: 'ALL', label: `全部库位（${allSlots.length}张）` }, { value: 'RACK', label: '按货架打印' }, { value: 'CUSTOM', label: '勾选库位补打' }]} /></Form.Item>
          {printScope === 'RACK' && <Form.Item label="选择货架" required><Select value={printRack} onChange={setPrintRack} loading={slotsQuery.isLoading} placeholder="请选择货架" options={printRackCodes.map((code) => ({ value: code, label: `${code}（${allSlots.filter((slot) => slot.rack_code === code).length}张）` }))} /></Form.Item>}
          {printScope === 'CUSTOM' && <Form.Item label="选择需要补打的库位" required><Select mode="multiple" showSearch optionFilterProp="label" value={printSlotIds} onChange={setPrintSlotIds} loading={slotsQuery.isLoading} placeholder="可选择一个或多个库位" options={allSlots.map((slot) => ({ value: slot.id, label: `${slot.display_code} · ${slotLabelDescription(slot)}` }))} /></Form.Item>}
        </Form>
      </Modal>
    </div>
  )
}
