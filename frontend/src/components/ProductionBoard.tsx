import { ClockCircleOutlined, LinkOutlined, PlusOutlined, ToolOutlined } from '@ant-design/icons'
import { Card, Tag, Typography } from 'antd'
import dayjs from 'dayjs'
import { useEffect, useMemo, useState } from 'react'
import { formatProductionDate, productionStationGroupLabel, productionStationNumber } from '../production'
import type { ProductionBoard as ProductionBoardData, ProductionBoardStation, ProductionReminderStatus } from '../types'

const REMINDER_META: Record<ProductionReminderStatus, { label: string; color: string }> = {
  IDLE: { label: '空闲', color: 'default' },
  MOUNTED: { label: '已上机', color: 'cyan' },
  PLANNED: { label: '待上机', color: 'blue' },
  NORMAL: { label: '生产中', color: 'processing' },
  DUE_SOON: { label: '即将换模', color: 'warning' },
  OVERDUE: { label: '已超时', color: 'error' },
}

function remainingText(station: ProductionBoardStation, now: dayjs.Dayjs) {
  const target = station.run?.expected_change_at
  const parsedTarget = target ? dayjs(target) : undefined
  const minutes = parsedTarget?.isValid() ? parsedTarget.diff(now, 'minute') : station.minutes_to_change
  if (minutes === null || minutes === undefined || !Number.isFinite(minutes)) return '-'
  const absolute = Math.abs(minutes)
  const hours = Math.floor(absolute / 60)
  const rest = absolute % 60
  const text = hours ? `${hours}小时${rest}分` : `${rest}分钟`
  return minutes < 0 ? `超时 ${text}` : `剩余 ${text}`
}

function StationCard({ station, now, onClick }: { station: ProductionBoardStation; now: dayjs.Dayjs; onClick: () => void }) {
  // 待上机计划在独立计划看板展示，不占用实时机台卡片。
  const hasPlan = station.run?.status === 'PLANNED'
  const run = station.run?.status === 'PLANNED' ? undefined : station.run
  const mountedMolds = station.mounted_molds || []
  const reminder = run
    ? station.reminder_status || 'NORMAL'
    : mountedMolds.length
      ? 'MOUNTED'
      : 'IDLE'
  const meta = REMINDER_META[reminder]
  const machineNumber = productionStationNumber(station)
  const groupLabel = `${productionStationGroupLabel(station.group)}机台`
  const ariaState = run
    ? `${run.order_no}，${meta.label}`
    : mountedMolds.length
      ? `${meta.label}，模具型号 ${mountedMolds.map((mold) => mold.model_code).join('、')}，管理生产或下机`
      : hasPlan
        ? `${meta.label}，已有待上机计划`
        : `${meta.label}，选择上机方式`
  return (
    <button
      type="button"
      className={`production-station ${reminder.toLowerCase()}`}
      aria-label={`${groupLabel} ${machineNumber}号机台，${ariaState}`}
      onClick={onClick}
    >
      <div className="production-station-head">
        <span className="station-number"><strong>{machineNumber}</strong><small>号台</small></span>
        <Tag color={meta.color}>{meta.label}</Tag>
      </div>
      {run ? (
        <>
          <strong className="station-order">{run.order_no}</strong>
          {(mountedMolds.length > 0 || run.mold_model_code) && (
            <div className="station-mold-list">
              {mountedMolds.map((mold) => (
                <span className="station-mold" key={mold.id}>
                  <b>{mold.model_code}</b>
                  <small>{mold.asset_code} · {mold.product_name}</small>
                </span>
              ))}
              {mountedMolds.length === 0 && run.mold_model_code && (
                <span className="station-mold">
                  <b>{run.mold_model_code}</b>
                  <small>{run.mold_code} · {run.mold_product_name}</small>
                </span>
              )}
            </div>
          )}
          <span className="station-product">{run.specification || '未填写规格'} · {run.material || '未填写材质'}</span>
          <div className="station-time-grid">
            <span>上模</span><b>{formatProductionDate(run.loaded_at, 'MM-DD HH:mm')}</b>
            <span>换模</span><b>{formatProductionDate(run.expected_change_at, 'MM-DD HH:mm')}</b>
            <span>换料</span><b>{formatProductionDate(run.material_changed_at, 'MM-DD HH:mm')}</b>
          </div>
          <div className="station-countdown"><ClockCircleOutlined /> {remainingText(station, now)}</div>
        </>
      ) : mountedMolds.length > 0 ? (
        <div className="station-mounted">
          <strong className="station-order">{mountedMolds.length}副模具已上机</strong>
          <div className="station-mold-list">
            {mountedMolds.map((mold) => (
              <span className="station-mold" key={mold.id}>
                <b>{mold.model_code}</b>
                <small>{mold.asset_code} · {mold.product_name}</small>
              </span>
            ))}
          </div>
          <span className="station-mounted-action"><ToolOutlined /> 点击管理生产 / 下机</span>
        </div>
      ) : (
        <div className="station-empty">
          <PlusOutlined />
          <span>{hasPlan ? '机台空闲 · 已有待上机计划' : '选择上机方式'}</span>
        </div>
      )}
    </button>
  )
}

interface Props {
  board?: ProductionBoardData
  loading?: boolean
  onStationClick: (station: ProductionBoardStation) => void
}

export function ProductionBoard({ board, loading, onStationClick }: Props) {
  const [now, setNow] = useState(dayjs())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(dayjs()), 30_000)
    return () => window.clearInterval(timer)
  }, [])

  const groups = useMemo(() => board?.groups || [], [board?.groups])
  return (
    <Card
      className="production-board-card"
      loading={loading}
      title={<span><ToolOutlined /> 机台实时看板</span>}
      extra={<Typography.Text type="secondary">当前 {groups.length} 个分组、共 {groups.reduce((sum, group) => sum + group.stations.length, 0)} 台；台账上机后同步显示模具型号</Typography.Text>}
    >
      <div className="production-board-scroll">
        <div className="production-board">
          {groups.map((group) => {
            const stations = [...group.stations].sort((left, right) => left.position_no - right.position_no)
            const stationLabels = stations.map((station) => `${productionStationNumber(station)}号机台`)
            const groupLabel = `${productionStationGroupLabel(group.group)}机台`
            const topologyLabel = stations.length === 2 ? `${stationLabels[0]}与${stationLabels[1]}相连` : `共${stations.length}台：${stationLabels.join('、')}`
            return (
              <section className="production-group" key={group.group}>
                <header>
                  <div>
                    <Typography.Title level={4}>{groupLabel}</Typography.Title>
                    <Typography.Text type="secondary" className="production-group-topology"><LinkOutlined /> {topologyLabel}</Typography.Text>
                  </div>
                  <span className="group-running-count">占用 {stations.filter((station) => (station.mounted_molds?.length || 0) > 0 || station.run?.status === 'RUNNING').length} / {stations.length} 台</span>
                </header>
                <div className={`production-station-pair ${stations.length === 2 ? 'paired' : 'multi'}`} role="group" aria-label={`${groupLabel}设备：${stationLabels.join('、')}`}>
                  {stations.map((station) => (
                    <StationCard key={station.id} station={station} now={now} onClick={() => onStationClick(station)} />
                  ))}
                  {stations.length === 2 && <span className="station-pair-connector" aria-hidden="true"><span>联</span></span>}
                </div>
              </section>
            )
          })}
        </div>
      </div>
    </Card>
  )
}
