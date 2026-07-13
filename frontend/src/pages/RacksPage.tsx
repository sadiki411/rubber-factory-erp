import { AppstoreOutlined, SearchOutlined } from '@ant-design/icons'
import { Alert, App, Button, Card, Col, Empty, Input, Row, Select, Skeleton, Space, Statistic, Typography } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { moldApi, rackApi, toList } from '../api/client'
import { PageTitle } from '../components/PageTitle'
import { RackDiagram } from '../components/RackDiagram'
import { MoldFormDrawer } from '../components/MoldFormDrawer'
import type { MoldAsset, RackSlot, RackZone } from '../types'
import { moldCode, moldLocation } from '../types'

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
  const racksQuery = useQuery({ queryKey: ['racks'], queryFn: async () => toList(await rackApi.list()) })
  const defaultRack = racksQuery.data?.find((rack) => rack.code === navigationState?.rackCode) || racksQuery.data?.[0]
  const effectiveSelectedId = selectedId ?? defaultRack?.id

  const layoutQuery = useQuery({
    queryKey: ['racks', effectiveSelectedId, 'layout'],
    queryFn: () => rackApi.layout(effectiveSelectedId!),
    enabled: !!effectiveSelectedId,
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

  const current = useMemo(() => racksQuery.data?.find((rack) => rack.id === effectiveSelectedId), [racksQuery.data, effectiveSelectedId])
  const layoutCounts = useMemo(() => {
    const slots = layoutQuery.data?.levels.flatMap((level) => level.zones.flatMap((zone) => zone.slots)) || []
    return {
      occupied: slots.filter((slot) => !!slot.mold).length,
      active: slots.filter((slot) => slot.active).length,
    }
  }, [layoutQuery.data])

  return (
    <div className="page-container">
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
            onMoldClick={(id) => navigate(`/molds/${id}`)}
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
    </div>
  )
}
