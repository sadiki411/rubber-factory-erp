import { CalendarOutlined, ToolOutlined } from '@ant-design/icons'
import { Button, Card, Empty, Tag, Typography } from 'antd'
import { useMemo } from 'react'
import { productionStationGroupLabel, productionStationNumber } from '../production'
import type { ProductionBoard, ProductionBoardStation } from '../types'

interface Props {
  board?: ProductionBoard
  loading?: boolean
  onPlanClick: (station: ProductionBoardStation) => void
}

export function ProductionPlanBoard({ board, loading, onPlanClick }: Props) {
  const plannedStations = useMemo(() => (
    (board?.groups || [])
      .flatMap((group) => group.stations)
      .filter((station) => station.run?.status === 'PLANNED')
      .sort((left, right) => left.position_no - right.position_no)
  ), [board?.groups])

  return (
    <Card
      className="production-plan-board"
      loading={loading}
      title={<span><CalendarOutlined /> 待上机计划</span>}
      extra={<Typography.Text type="secondary">确认上机后，模具会同步移出货架并进入实时机台看板</Typography.Text>}
    >
      {plannedStations.length ? (
        <div className="production-plan-grid">
          {plannedStations.map((station) => {
            const run = station.run!
            return (
              <article className="production-plan-card" key={run.id}>
                <div className="production-plan-head">
                  <span><ToolOutlined /> {productionStationGroupLabel(station.group)}-{productionStationNumber(station)}号机台</span>
                  <Tag color="blue">待上机</Tag>
                </div>
                <Typography.Title level={4}>{run.mold_model_code || '未关联模具型号'}</Typography.Title>
                <Typography.Text strong>{run.order_no}</Typography.Text>
                <Typography.Text type="secondary">{run.mold_code || '未填写模具编号'} · {run.specification || '未填写规格'} · {run.material || '未填写材质'}</Typography.Text>
                <div className="production-plan-footer">
                  <span>计划 {Number(run.planned_mold_count || 0).toLocaleString('zh-CN')} 模</span>
                  <Button type="primary" onClick={() => onPlanClick(station)}>查看并确认上机</Button>
                </div>
              </article>
            )
          })}
        </div>
      ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无待上机计划" />}
    </Card>
  )
}
