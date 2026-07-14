import { CalendarOutlined, ExperimentOutlined, PlusOutlined } from '@ant-design/icons'
import { Button, Drawer, Typography } from 'antd'
import { productionStationGroupLabel, productionStationNumber } from '../production'
import type { ProductionBoardStation } from '../types'

interface Props {
  open: boolean
  station?: ProductionBoardStation
  onClose: () => void
  onQuickMount: (continueToProduction: boolean) => void
  onCreatePlan: () => void
}

export function StationStartDrawer({ open, station, onClose, onQuickMount, onCreatePlan }: Props) {
  const hasPlan = station?.run?.status === 'PLANNED'
  const stationLabel = station
    ? `${productionStationGroupLabel(station.group)}-${productionStationNumber(station)}号机台`
    : '机台'

  return (
    <Drawer
      open={open}
      onClose={onClose}
      size={520}
      title={`${stationLabel} · 选择上机方式`}
      className="station-start-drawer"
    >
      <Typography.Paragraph type="secondary">
        生产计划不是上机的必经步骤。试模可只改变模具位置；正式生产也可以先把模具上机，再直接登记生产订单。
      </Typography.Paragraph>
      {hasPlan && station?.run?.mold_id && (
        <Typography.Paragraph type="warning">
          当前计划已关联模具 {station.run.mold_model_code || station.run.mold_code || station.run.mold_id}；快速上机时只能选择该模具。
        </Typography.Paragraph>
      )}
      <div className="station-start-actions">
        <Button type="primary" icon={<ExperimentOutlined />} onClick={() => onQuickMount(false)}>
          快速上机 / 试模
          <small>{hasPlan ? '选择该计划模具；试模后可归位并保留计划' : '选择任一在库模具，只记录模具已到本机台'}</small>
        </Button>
        {!hasPlan && (
          <Button icon={<PlusOutlined />} onClick={() => onQuickMount(true)}>
            上机后登记生产
            <small>先选择模具上机，随后填写订单与生产资料</small>
          </Button>
        )}
        <Button icon={<CalendarOutlined />} onClick={onCreatePlan}>
          {hasPlan ? '查看待上机计划' : '新增待上机计划'}
          <small>{hasPlan ? `查看 ${station?.run?.order_no || ''} 并确认正式上机` : '提前安排订单，实际上机时再确认'}</small>
        </Button>
      </div>
    </Drawer>
  )
}
