import { CalendarOutlined, ExportOutlined, HomeOutlined, PlusOutlined, ToolOutlined } from '@ant-design/icons'
import { Button, Card, Drawer, Empty, Space, Typography } from 'antd'
import { productionStationGroupLabel, productionStationNumber } from '../production'
import type { ProductionBoardRun, ProductionBoardStation, ProductionMold } from '../types'

interface Props {
  open: boolean
  station?: ProductionBoardStation
  onClose: () => void
  onCreateProduction: (mold: ProductionMold) => void
  onViewPlan: (run: ProductionBoardRun) => void
  onPutaway: (mold: ProductionMold) => void
  onSendOut: (mold: ProductionMold) => void
}

export function MountedMoldActionsDrawer({ open, station, onClose, onCreateProduction, onViewPlan, onPutaway, onSendOut }: Props) {
  const molds = station?.mounted_molds || []
  const plannedRun = station?.run?.status === 'PLANNED' ? station.run : undefined
  return (
    <Drawer
      open={open}
      onClose={onClose}
      size={520}
      title={station ? `${productionStationGroupLabel(station.group)}-${productionStationNumber(station)}号机台 · 已上机模具` : '已上机模具'}
      className="mounted-mold-actions-drawer"
    >
      <Typography.Paragraph type="secondary">
        {plannedRun
          ? '该模具已按待上机计划临时上机。可确认计划开始生产，或在试模结束后归位；归位不会删除原计划。'
          : '可继续登记生产订单；需要释放机台时，请选择具体模具执行“下机并归位”。'}
      </Typography.Paragraph>
      {molds.length ? (
        <div className="mounted-mold-action-list">
          {molds.map((mold) => (
            <Card key={mold.id} size="small">
              <div className="mounted-mold-action-heading">
                <div>
                  <Typography.Title level={4}>{mold.model_code}</Typography.Title>
                  <Typography.Text type="secondary">{mold.asset_code} · {mold.product_name || '-'}</Typography.Text>
                </div>
                <ToolOutlined />
              </div>
              <Space className="mounted-mold-action-buttons">
                {plannedRun ? (
                  <>
                    <Button type="primary" icon={<CalendarOutlined />} onClick={() => onViewPlan(plannedRun)}>查看并确认计划</Button>
                    <Button icon={<HomeOutlined />} onClick={() => onPutaway(mold)}>试模结束并归位</Button>
                  </>
                ) : (
                  <>
                    <Button icon={<PlusOutlined />} onClick={() => onCreateProduction(mold)}>登记生产</Button>
                    <Button type="primary" icon={<HomeOutlined />} onClick={() => onPutaway(mold)}>下机并归位</Button>
                    <Button icon={<ExportOutlined />} onClick={() => onSendOut(mold)}>客户收回</Button>
                  </>
                )}
              </Space>
            </Card>
          ))}
        </div>
      ) : <Empty description="该机台当前没有已上机模具" />}
    </Drawer>
  )
}
