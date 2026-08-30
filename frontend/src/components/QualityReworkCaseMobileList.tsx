import { Button, Card, Empty, List, Space, Tag, Typography } from 'antd'
import { EditOutlined, PlusOutlined } from '@ant-design/icons'
import { formatQualityDate, qualityNumber } from '../quality'
import type { QualityReworkCase } from '../types'

const STATUS_META: Record<string, { label: string; color: string }> = {
  WAITING_REWORK: { label: '待返工', color: 'warning' },
  RESHIPPED: { label: '已重新出货', color: 'success' },
  OPEN: { label: '待返工', color: 'warning' },
  PROCESSING: { label: '返工中', color: 'processing' },
  WAITING_REINSPECTION: { label: '待复检', color: 'blue' },
  COMPLETED: { label: '已完成', color: 'success' },
  SCRAPPED: { label: '已报废', color: 'default' },
  CANCELLED: { label: '已取消', color: 'default' },
}

const REASON_META: Record<string, string> = {
  APPEARANCE: '外观',
  STICKING: '粘皮',
  DIMENSION: '尺寸',
  MATERIAL: '材质',
  MIXED: '混料 / 混装',
  PACKAGING: '包装',
  OTHER: '其他',
}

interface Props {
  items: QualityReworkCase[]
  loading?: boolean
  emptyText?: string
  onOpen: (item: QualityReworkCase) => void
  onAddAttempt?: (item: QualityReworkCase) => void
}

function statusTag(item: QualityReworkCase) {
  const meta = STATUS_META[item.status] || { label: item.status, color: 'default' }
  return <Tag color={meta.color}>{meta.label}</Tag>
}

/**
 * Mobile-only record view.  The source order and product facts deliberately
 * come before workflow metadata so a workshop operator never has to swipe a
 * wide table merely to identify the returned batch.
 */
export function QualityReworkCaseMobileList({ items, loading, emptyText = '暂无退货返工记录', onOpen, onAddAttempt }: Props) {
  return <List
    className="mobile-record-list quality-rework-mobile-list"
    loading={loading}
    dataSource={items}
    locale={{ emptyText: <Empty description={emptyText} /> }}
    renderItem={(item) => {
      const source = item.source
      const canAddAttempt = item.return_round == null && item.origin === 'CUSTOMER_RETURN' && !['CANCELLED', 'SCRAPPED'].includes(item.status)
      const returnRound = item.return_round || item.attempt_count || item.attempts?.length || 1
      const cardNo = item.active_process_card_no || item.process_card_no || item.process_card?.card_no || source?.lines?.find((line) => line.card_no)?.card_no
      return <List.Item>
        <Card
          className="mobile-record-card quality-rework-mobile-card"
        >
          <div className="record-card-heading quality-rework-mobile-heading">
            <div>
              <Tag color="orange">{item.return_label || `第 ${returnRound} 次退货返工`}</Tag>
              <Typography.Title level={4}>{item.case_no}</Typography.Title>
              <Typography.Text type="secondary">{formatQualityDate(item.opened_on)}</Typography.Text>
            </div>
            {statusTag(item)}
          </div>

          <div className="quality-rework-mobile-identity">
            <span><small>订单 / 项次</small><strong>{source ? `${source.order_no || '未关联订单'}${source.item_no ? ` / ${source.item_no}` : ''}` : `内部返工 · 流程卡 #${item.process_card_id || '-'}`}</strong></span>
            <span><small>产品</small><strong>{source?.product_name || '未填写产品'}</strong></span>
            <span><small>流程卡</small><strong>{cardNo || '待绑定流程卡'}</strong></span>
          </div>

          <div className="quality-rework-mobile-grid">
            <span><small>规格</small><b>{source?.specification || '-'}</b></span>
            <span><small>材质</small><b>{source?.material || '-'}</b></span>
            <span><small>原出货单</small><b>{source?.shipment_no || '-'}</b></span>
            <span><small>退回物理批号</small><b>{source ? `第 ${source.shipment_unit_no || item.shipment_unit_no || '-'} / ${source.total_batches || '-'} 批` : '-'}</b></span>
            <span><small>整批数量</small><b>{source ? `${qualityNumber(source.pieces_per_batch)} 件` : `${qualityNumber(item.affected_quantity)} 件`}</b></span>
            <span><small>整批净重</small><b>{source ? `${qualityNumber(source.single_batch_net_weight_kg, 3)} kg` : `${qualityNumber(item.affected_weight_kg, 3)} kg`}</b></span>
          </div>

          <div className="quality-rework-mobile-reason">
            <Tag>{item.primary_reason_detail?.name || item.primary_reason?.name || item.reason_category_display || REASON_META[item.reason_category] || item.reason_category || '其他'}</Tag>
            <Typography.Text>{item.reason || '未填写具体退货原因'}</Typography.Text>
          </div>

          {!!(item.secondary_reason_details || item.secondary_reasons)?.length && <Space wrap size={[4, 4]}><Typography.Text type="secondary">次要问题：</Typography.Text>{(item.secondary_reason_details || item.secondary_reasons || []).map((reason) => <Tag key={String(reason.id)}>{reason.name}</Tag>)}</Space>}

          <Space className="quality-rework-mobile-actions">
            <Button block icon={<EditOutlined />} onClick={(event) => { event.stopPropagation(); onOpen(item) }}>查看 / 修改</Button>
            {canAddAttempt && onAddAttempt && <Button block type="primary" icon={<PlusOutlined />} onClick={(event) => { event.stopPropagation(); onAddAttempt(item) }}>登记下一轮</Button>}
          </Space>
        </Card>
      </List.Item>
    }}
  />
}
