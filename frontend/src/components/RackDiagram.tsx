import { LockOutlined, StopOutlined, SwapOutlined } from '@ant-design/icons'
import { Button, Empty, Select, Skeleton, Space, Switch, Tag, Tooltip, Typography } from 'antd'
import type { RackLayout, RackSlot, RackZone } from '../types'

interface Props {
  layout?: RackLayout
  loading?: boolean
  highlightMoldId?: number
  onMoldClick?: (moldId: number) => void
  onEmptySlotClick?: (slot: RackSlot) => void
  onCapacityChange?: (zone: RackZone, capacity: number) => void
  onStackingChange?: (zone: RackZone, enabled: boolean) => void
}

function SlotCell({ slot, highlightMoldId, onMoldClick, onEmptySlotClick }: Pick<Props, 'highlightMoldId' | 'onMoldClick' | 'onEmptySlotClick'> & { slot?: RackSlot }) {
  if (!slot || !slot.active) {
    const reason = slot?.blocking_reason || (slot ? '禁放' : '未启用')
    return (
      <div className="rack-slot disabled" title={reason}>
        <span className="disabled-slot-label">{slot?.blocking_reason ? '禁放' : reason}</span>
        {slot?.blocking_reason && <span className="slot-blocking-reason">{slot.blocking_reason}</span>}
      </div>
    )
  }
  const occupied = !!slot.mold
  const highlighted = occupied && slot.mold?.id === highlightMoldId
  return (
    <button
      type="button"
      className={`rack-slot ${occupied ? 'occupied' : 'empty'} ${highlighted ? 'highlighted' : ''}`}
      onClick={() => {
        if (occupied && slot.mold) onMoldClick?.(slot.mold.id)
        else onEmptySlotClick?.(slot)
      }}
      disabled={occupied ? !onMoldClick : !onEmptySlotClick}
      aria-label={`${slot.display_code}${occupied ? ` ${slot.mold?.asset_code}` : ' 空位'}`}
    >
      <span className="slot-code">{slot.display_code}</span>
      {occupied ? (
        <>
          <strong>{slot.mold?.asset_code}</strong>
          <span>{slot.mold?.model_code || slot.mold?.product_name || '已占用'}</span>
        </>
      ) : <span className="empty-label">空位 · 点击放入</span>}
    </button>
  )
}

function ZoneCells({ zone, highlightMoldId, onMoldClick, onEmptySlotClick }: { zone: RackZone } & Pick<Props, 'highlightMoldId' | 'onMoldClick' | 'onEmptySlotClick'>) {
  const positions = Array.from({ length: zone.current_capacity }, (_, index) => index + 1)
  const stacks = zone.supports_stacking && zone.stacking_enabled ? 2 : 1
  return (
    <div className="zone-cells" style={{ gridTemplateColumns: `repeat(${zone.current_capacity}, minmax(112px, 1fr))` }}>
      {positions.map((position) => (
        <div className={`slot-stack stack-${stacks}`} key={position}>
          {Array.from({ length: stacks }, (_, index) => stacks - index).map((stackLevel) => {
            const slot = zone.slots.find((item) => item.position_no === position && item.stack_level === stackLevel)
            return <SlotCell key={stackLevel} slot={slot} highlightMoldId={highlightMoldId} onMoldClick={onMoldClick} onEmptySlotClick={onEmptySlotClick} />
          })}
        </div>
      ))}
    </div>
  )
}

function InactiveZone({ zone }: { zone: RackZone }) {
  const reason = zone.blocking_reason
    || zone.slots.find((slot) => slot.blocking_reason)?.blocking_reason
    || '此区域不用于放置模具'
  return (
    <div className="inactive-zone-body">
      <StopOutlined />
      <strong>禁放 / 杂物区</strong>
      <span>{reason}</span>
    </div>
  )
}

export function RackDiagram({ layout, loading, highlightMoldId, onMoldClick, onEmptySlotClick, onCapacityChange, onStackingChange }: Props) {
  if (loading) return <Skeleton active paragraph={{ rows: 8 }} />
  if (!layout || !layout.levels?.length) {
    return <Empty description="此货架还没有配置结构" />
  }
  const levels = [...layout.levels].sort((a, b) => b.level_no - a.level_no)
  const maximumPositions = Math.max(
    ...levels.map((level) => level.zones.reduce((total, zone) => total + Math.max(zone.current_capacity || 1, 1), 0)),
    1,
  )
  const frameMinWidth = Math.max(760, 64 + maximumPositions * 112)
  return (
    <div className="rack-scroll-region">
      <div className="rack-frame" style={{ minWidth: frameMinWidth }}>
        <div className="rack-topbar">
          <div>
            <Typography.Title level={4}>{layout.rack.code} · {layout.rack.name}</Typography.Title>
            <Typography.Text type="secondary">正面视图（最高层在上方）</Typography.Text>
          </div>
          {layout.rack.locked && <Tag icon={<LockOutlined />}>结构已锁定</Tag>}
        </div>
        {levels.map((level) => (
          <div className="rack-level" key={level.id || level.level_no}>
            <div className="level-label">L{String(level.level_no).padStart(2, '0')}</div>
            <div className="level-zones">
              {level.zones.map((zone) => (
                <section className={`rack-zone ${zone.is_active === false ? 'inactive' : ''}`} key={zone.id || zone.code}>
                  <div className="zone-header">
                    <span>{zone.name || zone.code}</span>
                    {zone.is_active === false ? <Tag>禁放</Tag> : (
                      <Space size={10} className="zone-controls">
                        {onCapacityChange && zone.allowed_capacities?.length > 1 && (
                          <Tooltip title="此区域为空时才可切换容量">
                            <Space size={4}>
                              <SwapOutlined />
                              <Select
                                size="small"
                                aria-label={`切换${zone.name || zone.code}容量`}
                                value={zone.current_capacity}
                                options={zone.allowed_capacities.map((value) => ({ value, label: `${value} 位` }))}
                                onChange={(value) => onCapacityChange(zone, value)}
                                popupMatchSelectWidth={false}
                              />
                            </Space>
                          </Tooltip>
                        )}
                        {zone.supports_stacking && (
                          <Tooltip title={zone.stacking_enabled ? '已显示S2上叠层和S1下层' : '开启后显示S2上叠层'}>
                            <Space size={5} className="stacking-control">
                              <span>叠放</span>
                              <Switch
                                size="small"
                                checked={zone.stacking_enabled}
                                disabled={!onStackingChange}
                                aria-label={`切换${zone.name || zone.code}叠放`}
                                onChange={(enabled) => onStackingChange?.(zone, enabled)}
                              />
                            </Space>
                          </Tooltip>
                        )}
                      </Space>
                    )}
                  </div>
                  {zone.is_active === false
                    ? <InactiveZone zone={zone} />
                    : <ZoneCells zone={zone} highlightMoldId={highlightMoldId} onMoldClick={onMoldClick} onEmptySlotClick={onEmptySlotClick} />}
                </section>
              ))}
            </div>
          </div>
        ))}
        <div className="rack-base">
          <Button type="text" size="small">{layout.rack.code} 正面</Button>
        </div>
      </div>
    </div>
  )
}
