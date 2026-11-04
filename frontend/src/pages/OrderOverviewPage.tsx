import { Alert, Button, Card, Col, Descriptions, Empty, Pagination, Row, Skeleton, Space, Statistic, Tabs, Tag, Typography } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { orderApi } from '../api/client'
import { PageTitle } from '../components/PageTitle'
import { TRACE_SECTIONS } from '../operations'

const quantity = (value: number | string | null, unit: string) => value === null ? '未登记/不适用' : `${value} ${unit}`

export function OrderOverviewPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const section = TRACE_SECTIONS.find((entry) => entry.key === params.get('section'))?.key || 'production'
  const page = Math.max(1, Number(params.get('page')) || 1)
  const validId = Boolean(id && /^\d+$/.test(id) && Number(id) > 0)
  const result = useQuery({
    queryKey: ['order-overview', id, section, page],
    queryFn: ({ signal }) => orderApi.overview(Number(id), { section, page, page_size: 20 }, { signal }),
    enabled: validId,
  })
  const data = result.data
  const returnTo = params.get('return_to') || ''
  const safeReturn = /^\/operations(?:\?|$)/.test(returnTo) ? returnTo : '/operations'
  const change = (values: Record<string, string>) => {
    const next = new URLSearchParams(params)
    Object.entries(values).forEach(([key, value]) => next.set(key, value))
    setParams(next)
  }
  return <div className="page-container order-overview-page">
    <PageTitle title="订单完整查看" description="沿明确关联查看原记录；只读，不改数量、状态或历史归属。" extra={<Space wrap><Button onClick={() => navigate(safeReturn)}>返回待办筛选</Button><Button onClick={() => void result.refetch()} loading={result.isFetching}>刷新</Button></Space>} />
    {!validId ? <Alert type="error" showIcon title="订单编号无效" description="请从待办或订单管理打开完整查看。" /> : result.isError ? <Alert type="error" showIcon title="订单链路读取失败" description={(result.error as Error).message} action={<Button onClick={() => void result.refetch()}>重试</Button>} /> : !data ? <Skeleton active /> : <>
      <Card><Descriptions column={{ xs: 1, sm: 2, lg: 4 }} items={[
        { key: 'order', label: '订单 / 项次', children: `${data.order.order_no} / ${data.order.item_no || '未登记'}` },
        { key: 'product', label: '规格 / 材质', children: `${data.order.specification} / ${data.order.material || '未登记'}` },
        { key: 'due', label: '交期', children: data.order.due_date || '未登记' },
        { key: 'status', label: '订单状态', children: ({ OPEN: '进行中', COMPLETED: '已完成', CANCELLED: '已取消' } as Record<string, string>)[data.order.status] || data.order.status },
      ]} /><Row gutter={[12, 12]}>
        <Col xs={12} lg={6}><Statistic title="需求件数" value={data.order.order_quantity} /></Col>
        <Col xs={12} lg={6}><Statistic title="净有效交付" value={data.summary.net_delivered} /></Col>
        <Col xs={12} lg={6}><Statistic title="欠交件数" value={data.summary.delivery_remaining} /></Col>
        <Col xs={12} lg={6}><Statistic title="分配生产进度（非实测良品）" value={data.summary.production_progress_quantity} /></Col>
      </Row></Card>
      <Alert type="info" showIcon title="不要把物理量、分摊量、库存量重复相加" description={data.limitations.join(' ')} />
      <Tabs activeKey={section} onChange={(value) => change({ section: value, page: '1' })} items={TRACE_SECTIONS.map((entry) => ({ key: entry.key, label: `${entry.label}（${data.counts[entry.key]}）` }))} />
      <div className="operations-task-grid">{data.results.map((row) => <Card key={`${row.source}:${row.id}`} size="small" title={<Space wrap><span>{row.document_no}</span>{row.status && <Tag>{row.status}</Tag>}</Space>}>
        <Typography.Paragraph>业务日期：{row.date?.slice(0, 10) || '未登记'}<br />物理/原记录量：{quantity(row.physical_quantity, row.unit)}<br />本项次归属量：{quantity(row.own_quantity, row.unit)}</Typography.Paragraph>
        {row.cards.length > 0 && <Typography.Paragraph>流程卡：{row.cards.join('、')}</Typography.Paragraph>}
        <Typography.Paragraph>{row.details}</Typography.Paragraph><Typography.Paragraph type="secondary">来源：{row.source} #{row.id}</Typography.Paragraph>
        {row.url && <Button onClick={() => navigate(row.url!)}>打开原业务页面</Button>}
      </Card>)}</div>
      {!data.results.length && <Empty description="本节未找到明确关联记录；不代表现场没有发生" />}
      <Pagination current={page} total={data.count} pageSize={20} showSizeChanger={false} responsive onChange={(value) => change({ page: String(value) })} showTotal={(total) => `共 ${total} 条原记录`} />
    </>}
  </div>
}
