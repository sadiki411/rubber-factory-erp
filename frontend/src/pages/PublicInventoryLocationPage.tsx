import { Alert, Button, Card, Descriptions, Empty, Skeleton, Tag, Typography } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { inventoryApi } from '../api/client'
import { PageTitle } from '../components/PageTitle'
import { PublicLocationHistory } from '../components/PublicLocationHistory'

export function PublicInventoryLocationPage() {
  const { locationCode = '' } = useParams<{ locationCode: string }>()
  const locationQuery = useQuery({
    queryKey: ['public-inventory-location', locationCode],
    queryFn: () => inventoryApi.publicLocation(locationCode),
    enabled: !!locationCode,
    retry: 1,
  })
  const location = locationQuery.data
  if (locationQuery.isPending) return <div className="page-container"><Skeleton active /></div>
  if (locationQuery.isError || !location) return <div className="page-container"><Alert type="error" showIcon title="库位详情读取失败" description={(locationQuery.error as Error)?.message || '库位不存在'} /><Button onClick={() => void locationQuery.refetch()}>重试</Button></div>
  const container = location.container
  return <div className="page-container public-inventory-location-page">
    <PageTitle title={`库存库位 · ${location.code}`} description="扫码查看当前库存和库位历史（只读）。如需操作，请使用东橡 ERP 安卓 App。" />
    <Card>
      <Descriptions bordered size="small" column={1}>
        <Descriptions.Item label="库位编号"><Typography.Text copyable strong>{location.code}</Typography.Text></Descriptions.Item>
        <Descriptions.Item label="位置说明">{location.label || '—'}</Descriptions.Item>
        <Descriptions.Item label="库位状态">{!location.is_active ? <Tag color="warning">已停用</Tag> : container ? <Tag color="success">已有库存</Tag> : <Tag>空库位</Tag>}</Descriptions.Item>
      </Descriptions>
      {container ? <Descriptions bordered size="small" column={1} style={{ marginTop: 16 }}>
        <Descriptions.Item label="产品编号">{container.product_code || '—'}</Descriptions.Item>
        <Descriptions.Item label="产品名称">{container.product_name || '—'}</Descriptions.Item>
        <Descriptions.Item label="规格 / 材质">{[container.specification, container.material].filter(Boolean).join(' / ') || '—'}</Descriptions.Item>
        <Descriptions.Item label="当前数量"><Typography.Text strong>{container.quantity.toLocaleString()} 件</Typography.Text></Descriptions.Item>
        <Descriptions.Item label="质量状态">{container.quality_status_label}</Descriptions.Item>
        <Descriptions.Item label="库存批次">{container.batch_no}</Descriptions.Item>
        <Descriptions.Item label="容器">{container.container_code} · {container.container_type_label} · {container.bag_count} 袋{container.pieces_per_bag ? `，每袋 ${container.pieces_per_bag} 件` : ''}</Descriptions.Item>
      </Descriptions> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该库位当前没有库存，可在下方查看历史记录" />}
    </Card>
    <PublicLocationHistory kind="inventory" locationKey={location.code} />
  </div>
}
