import { DeleteOutlined, EditOutlined, ExportOutlined, EyeOutlined, SwapOutlined, ToolOutlined } from '@ant-design/icons'
import { Alert, Button, Descriptions, Drawer, Empty, Skeleton, Space, Typography } from 'antd'
import type { MoldAsset } from '../types'
import { moldCode, moldLocation, moldModelOf } from '../types'
import { StatusTag } from './StatusTag'

interface Props {
  open: boolean
  mold?: MoldAsset
  loading?: boolean
  error?: Error | null
  deleting?: boolean
  onClose: () => void
  onEdit: () => void
  onMove: () => void
  onLoadMachine: () => void
  onRelease: () => void
  onDelete: () => void
  onViewDetails: () => void
}

export function RackMoldActionsDrawer({ open, mold, loading, error, deleting, onClose, onEdit, onMove, onLoadMachine, onRelease, onDelete, onViewDetails }: Props) {
  const model = mold ? moldModelOf(mold) : undefined
  return (
    <Drawer
      open={open}
      onClose={onClose}
      size={480}
      title="管理当前架位的模具"
      className="rack-mold-actions-drawer"
      footer={<Button block onClick={onClose}>关闭</Button>}
    >
      {loading ? <Skeleton active /> : error ? (
        <Alert type="error" showIcon title="无法读取模具资料" description={error.message} />
      ) : !mold ? <Empty description="未找到该模具" /> : (
        <Space orientation="vertical" size={18} style={{ width: '100%' }}>
          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label="模具编号">{moldCode(mold)}</Descriptions.Item>
            <Descriptions.Item label="模具型号">{model?.code || '-'}</Descriptions.Item>
            <Descriptions.Item label="当前位置">{moldLocation(mold)}</Descriptions.Item>
            <Descriptions.Item label="状态"><StatusTag status={mold.status} /></Descriptions.Item>
          </Descriptions>
          <Alert
            type="info"
            showIcon
            title="录错位置请选择“移到其他库位”；模具确实离开工厂请选择“客户收回”；整条资料都是误录时才删除。"
          />
          <Space orientation="vertical" size={10} style={{ width: '100%' }} className="rack-mold-action-buttons">
            <Button size="large" block icon={<EditOutlined />} onClick={onEdit}>编辑编号和模具资料</Button>
            <Button size="large" block icon={<SwapOutlined />} onClick={onMove}>移到其他库位</Button>
            <Button size="large" block icon={<ToolOutlined />} onClick={onLoadMachine}>{mold.status === 'ON_MACHINE' ? '更换机台' : '安排上机'}</Button>
            <Button size="large" block icon={<ExportOutlined />} onClick={onRelease}>客户收回并释放库位</Button>
            <Button size="large" block danger icon={<DeleteOutlined />} loading={deleting} onClick={onDelete}>删除误录记录并清空库位</Button>
            <Button type="link" block icon={<EyeOutlined />} onClick={onViewDetails}>查看完整资料和操作历史</Button>
          </Space>
          <Typography.Text type="secondary">以上操作完成后，货架总览、模具台账和上机看板会同步更新。</Typography.Text>
        </Space>
      )}
    </Drawer>
  )
}
