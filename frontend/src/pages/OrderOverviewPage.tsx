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
  const section = TRACE_SECTIONS.find((entry) => entry.key === params.get('section'))?.key
  const page = Math.max(1, Number(params.get('page')) || 1)
  const validId = Boolean(id && /^\d+$/.test(id) && Number(id) > 0)
  const result = useQuery({
    queryKey: ['order-overview', id, section, page],
    queryFn: ({ signal }) => orderApi.overview(Number(id), { section, page, page_size: 20 }, { signal }),
    enabled: validId && Boolean(section),
  })
  const summaryQuery = useQuery({
    queryKey: ['order-overview-summary', id],
    queryFn: ({ signal }) => orderApi.overview(Number(id), { section: 'production', page: 1, page_size: 1 }, { signal }),
    enabled: validId,
  })
  const data = summaryQuery.data || result.data
  const returnTo = params.get('return_to') || ''
  const safeReturn = /^\/(?:operations|orders|analytics)(?:\?|$)/.test(returnTo) ? returnTo : '/operations'
  const change = (values: Record<string, string>) => {
    const next = new URLSearchParams(params)
    Object.entries(values).forEach(([key, value]) => next.set(key, value))
    setParams(next)
  }
  return <div className="page-container order-overview-page">
    <PageTitle title="订单完整查看" description="沿明确关联查看原记录；只读，不改数量、状态或历史归属。" extra={<Space wrap><Button onClick={() => navigate(safeReturn)}>返回待办筛选</Button><Button disabled={!validId} onClick={() => { void summaryQuery.refetch(); if (section) void result.refetch() }} loading={result.isFetching || summaryQuery.isFetching}>刷新</Button></Space>} />
    {!validId ? <Alert type="error" showIcon title="订单编号无效" description="请从待办或订单管理打开完整查看。" /> : summaryQuery.isError ? <Alert type="error" showIcon title="订单链路读取失败" description={(summaryQuery.error as Error).message} action={<Button onClick={() => void summaryQuery.refetch()}>重试</Button>} /> : !data ? <Skeleton active /> : <>
      <Card><Descriptions column={{ xs: 1, sm: 2, lg: 4 }} items={[
        { key: 'order', label: '订单 / 项次', children: `${data.order.order_no} / ${data.order.item_no || '未登记'}` },
        { key: 'product', label: '规格 / 材质', children: `${data.order.specification} / ${data.order.material || '未登记'}` },
        { key: 'due', label: '交期', children: data.order.due_date || '未登记' },
        { key: 'status', label: '订单状态', children: ({ OPEN: '进行中', COMPLETED: '已完成', CANCELLED: '已取消' } as Record<string, string>)[data.order.status] || data.order.status },
      ]} /><Row gutter={[12, 12]}>
        <Col xs={12} lg={6}><Statistic title="需求件数" value={data.order.order_quantity} /></Col>
        <Col xs={12} lg={6}><Statistic title="净有效交付" value={data.summary.net_delivered} /></Col>
        <Col xs={12} lg={6}><Statistic title="欠交件数" value={data.summary.delivery_remaining} /></Col>
        <Col xs={12} lg={6}><Statistic title="分配生产进度（非实测良品）" value={data.counts.production ? data.summary.production_progress_quantity : '未关联生产记录'} /></Col>
      </Row></Card>
      <details className="business-basis"><summary>数量与关联口径</summary>不要把物理量、分摊量、库存量重复相加。{data.limitations.join(' ')}</details>
      {!section && <Card title="订单概览 · 按需追溯">
        <Typography.Paragraph>{data.summary.net_delivered > data.order.order_quantity ? `净交付超出需求 ${Number(data.summary.net_delivered - data.order.order_quantity).toLocaleString('zh-CN')} 件，保留真实净交付，不截断。` : `当前欠交 ${Number(data.summary.delivery_remaining).toLocaleString('zh-CN')} 件。`}净交付已按有效出货减退回、加有效再出货汇总；下方原单用于核对，不再次累加。</Typography.Paragraph>
        {[
          { title: '生产与发料', keys: ['production', 'receipts', 'cards'] },
          { title: '出货与退回', keys: ['shipments', 'piece_shipments', 'returns', 'piece_returns'] },
          { title: '库存引用', keys: ['inventory'] },
        ].map((group) => <div key={group.title} className="order-source-group"><Typography.Title level={5}>{group.title}</Typography.Title><Space wrap>{TRACE_SECTIONS.filter((entry) => group.keys.includes(entry.key)).map((entry) => <Button key={entry.key} onClick={() => change({ section: entry.key, page: '1' })}>{entry.label}（{data.counts[entry.key]}）</Button>)}</Space></div>)}
        <Typography.Paragraph type="secondary">没有关联记录表示系统未找到明确依据，不表示现场未生产或未完成工序。各节可查看所有分页原单；本页不补造完成时间线。</Typography.Paragraph>
      </Card>}
      {section && <>
      <Button onClick={() => { const next = new URLSearchParams(params); next.delete('section'); next.delete('page'); setParams(next) }}>返回订单概览</Button>
      <Tabs activeKey={section} onChange={(value) => change({ section: value, page: '1' })} items={TRACE_SECTIONS.map((entry) => ({ key: entry.key, label: `${entry.label}（${data.counts[entry.key]}）` }))} />
      {result.isError ? <Alert type="error" title="本节来源读取失败" action={<Button onClick={() => void result.refetch()}>重试本节</Button>} /> : result.isPending ? <Skeleton active /> : <>
      <div className="operations-task-grid">{result.data.results.map((row) => <Card key={`${row.source}:${row.id}`} size="small" title={<Space wrap><span>{row.document_no}</span>{row.status && <Tag>{row.status}</Tag>}</Space>}>
        <Typography.Paragraph>业务日期：{row.date?.slice(0, 10) || '未登记'}<br />物理/原记录量：{quantity(row.physical_quantity, row.unit)}<br />本项次归属量：{quantity(row.own_quantity, row.unit)}</Typography.Paragraph>
        {row.cards.length > 0 && <Typography.Paragraph>流程卡：{row.cards.join('、')}</Typography.Paragraph>}
        <Typography.Paragraph>{row.details}</Typography.Paragraph><Typography.Paragraph type="secondary">来源：{row.source} #{row.id}</Typography.Paragraph>
        {row.url && <Button onClick={() => { const target = new URL(row.url!, window.location.origin); target.searchParams.set('return_to', `/order-overview/${id}?${params}`); navigate(target.pathname + target.search) }}>打开原业务页面</Button>}
      </Card>)}</div>
      {!result.data.results.length && <Empty description="本节未找到明确关联记录；不代表现场没有发生" />}
      <Pagination current={page} total={result.data.count} pageSize={20} showSizeChanger={false} responsive onChange={(value) => change({ page: String(value) })} showTotal={(total) => `共 ${total} 条原记录`} />
      </>}
      </>}
    </>}
  </div>
}
