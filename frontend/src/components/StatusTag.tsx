import { ClockCircleOutlined, ExportOutlined, HomeOutlined } from '@ant-design/icons'
import { Tag } from 'antd'
import type { MoldStatus } from '../types'
import { STATUS_META } from '../types'

const icons = {
  IN_STOCK: <HomeOutlined />,
  ON_MACHINE: <ClockCircleOutlined />,
  OUTSOURCED: <ExportOutlined />,
}

export function StatusTag({ status }: { status: MoldStatus }) {
  const meta = STATUS_META[status] || { text: status, color: 'default' }
  return <Tag color={meta.color} icon={icons[status]}>{meta.text}</Tag>
}
