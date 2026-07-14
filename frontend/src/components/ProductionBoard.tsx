import { ClockCircleOutlined, LinkOutlined, PlusOutlined, ToolOutlined } from '@ant-design/icons'
import { Card, Tag, Typography } from 'antd'
import dayjs from 'dayjs'
import { useEffect, useMemo, useState } from 'react'
import { formatProductionDate, productionStationNumber } from '../production'
import type { ProductionBoard as ProductionBoardData, ProductionBoardStation, ProductionReminderStatus, ProductionStationGroup } from '../types'

const GROUP_LABELS: Record<ProductionStationGroup, string> = { A: '一组机台', B: '二组机台', C: '三组机台' }
const MACHINES_PER_GROUP = 2

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
  const run = station.run
  const mountedMolds = station.mounted_molds || []
  const reminder = station.reminder_status || (run ? 'NORMAL' : mountedMolds.length ? 'MOUNTED' : 'IDLE')
  const meta = REMINDER_META[reminder]
  const machineNumber = productionStationNumber(station)
  const plannedRun = run?.status === 'PLANNED'
  const runMoldIsMounted = !!run?.mold_id && mountedMolds.some((mold) => mold.id === run.mold_id)
  const visibleMountedMolds = plannedRun && run?.mold_id
    ? mountedMolds.filter((mold) => mold.id !== run.mold_id)
    : mountedMolds
  const ariaState = run
    ? `${run.order_no}，${meta.label}`
    : mountedMolds.length
      ? `${meta.label}，模具型号 ${mountedMolds.map((mold) => mold.model_code).join('、')}，登记生产信息`
      : `${meta.label}，登记上机`
  return (
    <button
      type="button"
      className={`production-station ${reminder.toLowerCase()}`}
      aria-label={`${GROUP_LABELS[station.group]} ${machineNumber}号机台，${ariaState}`}
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
              {plannedRun && run.mold_model_code && (
                <span className="station-mold">
                  <b>{run.mold_model_code}</b>
                  <small>{run.mold_code} · {run.mold_product_name} · {runMoldIsMounted ? '已在机台，待确认' : '计划模具'}</small>
                </span>
              )}
              {visibleMountedMolds.map((mold) => (
                <span className="station-mold" key={mold.id}>
                  <b>{mold.model_code}</b>
                  <small>{plannedRun ? '现场：' : ''}{mold.asset_code} · {mold.product_name}</small>
                </span>
              ))}
              {!plannedRun && mountedMolds.length === 0 && run.mold_model_code && (
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
          <span className="station-mounted-action"><ToolOutlined /> 点击登记生产信息</span>
        </div>
      ) : (
        <div className="station-empty">
          <PlusOutlined />
          <span>登记上机</span>
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
      title={<span><ToolOutlined /> 三组双联机台实时看板</span>}
      extra={<Typography.Text type="secondary">每组2台，共6台；台账上机后会同步显示模具型号</Typography.Text>}
    >
      <div className="production-board-scroll">
        <div className="production-board">
          {groups.map((group) => {
            const stations = [...group.stations].sort((left, right) => left.position_no - right.position_no)
            const pairLabel = stations.map((station) => `${productionStationNumber(station)}号机台`).join('与')
            return (
              <section className="production-group" key={group.group}>
                <header>
                  <div>
                    <Typography.Title level={4}>{GROUP_LABELS[group.group]}</Typography.Title>
                    <Typography.Text type="secondary" className="production-group-topology"><LinkOutlined /> {pairLabel}相连</Typography.Text>
                  </div>
                  <span className="group-running-count">占用 {stations.filter((station) => (station.mounted_molds?.length || 0) > 0 || station.run?.status === 'RUNNING').length} / {MACHINES_PER_GROUP} 台</span>
                </header>
                <div className="production-station-pair" role="group" aria-label={`${GROUP_LABELS[group.group]}双联设备：${pairLabel}`}>
                  {stations.map((station) => (
                    <StationCard key={station.id} station={station} now={now} onClick={() => onStationClick(station)} />
                  ))}
                  <span className="station-pair-connector" aria-hidden="true"><span>联</span></span>
                </div>
              </section>
            )
          })}
        </div>
      </div>
    </Card>
  )
}
