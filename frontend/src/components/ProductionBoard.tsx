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
  PLANNED: { label: '待上模', color: 'blue' },
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
  const reminder = station.reminder_status || (run ? 'NORMAL' : 'IDLE')
  const meta = REMINDER_META[reminder]
  const machineNumber = productionStationNumber(station)
  return (
    <button
      type="button"
      className={`production-station ${reminder.toLowerCase()}`}
      aria-label={`${GROUP_LABELS[station.group]} ${machineNumber}号机台，${run ? `${run.order_no}，${meta.label}` : `${meta.label}，登记上模`}`}
      onClick={onClick}
    >
      <div className="production-station-head">
        <span className="station-number"><strong>{machineNumber}</strong><small>号台</small></span>
        <Tag color={meta.color}>{meta.label}</Tag>
      </div>
      {run ? (
        <>
          <strong className="station-order">{run.order_no}</strong>
          <span className="station-product">{run.specification || '未填写规格'} · {run.material || '未填写材质'}</span>
          <div className="station-time-grid">
            <span>上模</span><b>{formatProductionDate(run.loaded_at, 'MM-DD HH:mm')}</b>
            <span>换模</span><b>{formatProductionDate(run.expected_change_at, 'MM-DD HH:mm')}</b>
          </div>
          <div className="station-countdown"><ClockCircleOutlined /> {remainingText(station, now)}</div>
        </>
      ) : (
        <div className="station-empty">
          <PlusOutlined />
          <span>登记上模</span>
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
      extra={<Typography.Text type="secondary">每组2台，共6台；点击机台登记上模、补录日报或下机</Typography.Text>}
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
                  <span className="group-running-count">运行 {stations.filter((station) => !!station.run).length} / {MACHINES_PER_GROUP} 台</span>
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
