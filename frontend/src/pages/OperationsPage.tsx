import { Alert, Button, Card, Empty, Input, Pagination, Select, Skeleton, Space, Tag, Typography } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { orderApi } from '../api/client'
import { PageTitle } from '../components/PageTitle'
import type { WorkbenchKind } from '../operations'

const kinds: { value: WorkbenchKind; label: string }[] = [
  { value: 'ORDER_SHORTAGE', label: '订单欠交' }, { value: 'RETURN_PENDING', label: '退回/返工待处理' },
  { value: 'INVENTORY_QUALITY', label: '库存质量处理' }, { value: 'RECEIPT_UNLINKED', label: '发料待关联' },
]

export function OperationsPage() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const q = params.get('q') || ''
  const kind = kinds.some((entry) => entry.value === params.get('kind')) ? params.get('kind')! : ''
  const page = Math.max(1, Number(params.get('page')) || 1)
  const result = useQuery({
    queryKey: ['operations', q, kind, page],
    queryFn: ({ signal }) => orderApi.workbench({ q, kind, page, page_size: 20 }, { signal }),
  })
  const change = (values: Record<string, string>) => {
    const next = new URLSearchParams(params)
    Object.entries(values).forEach(([key, value]) => value ? next.set(key, value) : next.delete(key))
    setParams(next)
  }
  return <div className="page-container operations-page">
    <PageTitle title="全厂待办" description="先查看已登记事实中的待处理项；此页只读，不另建一套账，不自动指派负责人。" extra={<Button loading={result.isFetching} onClick={() => void result.refetch()}>刷新事实</Button>} />
    <Alert type="info" showIcon title="范围说明" description="欠交不等于欠生产；各类统计是事项条数，不可相加为产量。后工序未登记时不能推断进度。" />
    <Card className="operations-filter-card">
      <Space wrap className="operations-filters">
        <Input.Search key={q} defaultValue={q} allowClear aria-label="搜索待办" placeholder="订单、规格、材质或案号" onSearch={(value) => change({ q: value.trim(), page: '1' })} enterButton="查询" />
        <Select aria-label="待办类别" value={kind} onChange={(value) => change({ kind: value, page: '1' })} options={[{ value: '', label: '全部类别' }, ...kinds]} />
      </Space>
      {result.data && !result.isError && <><Typography.Paragraph type="secondary">{result.data.summary_scope}</Typography.Paragraph><Space wrap>{kinds.map((entry) => <Tag key={entry.value}>{entry.label} {result.data.summary[entry.value]}</Tag>)}</Space></>}
    </Card>
    {result.isError ? <Alert type="error" showIcon title="待办读取失败，不能将失败视为0项" description={(result.error as Error).message} action={<Button onClick={() => void result.refetch()}>重试</Button>} /> : result.isPending ? <Skeleton active /> : <>
      <div className="operations-task-grid">{result.data.results.map((row) => <Card key={row.id} size="small" title={<Space wrap><Tag color="blue">{row.title}</Tag><span>{row.document_no}</span></Space>}>
        <Typography.Title level={5}>{row.specification || '规格未登记'} · {row.material || '材质未登记'}</Typography.Title>
        <Typography.Paragraph>待处理相关数量：<strong>{row.quantity === null ? '未登记' : `${row.quantity} ${row.unit}`}</strong> · 交期：{row.due_date || '未登记'}</Typography.Paragraph>
        <Typography.Paragraph>{row.reason}</Typography.Paragraph>
        <Typography.Paragraph type="secondary">负责人：{row.owner || '未指定（不推定责任人）'}<br />来源：{row.source}</Typography.Paragraph>
        <Space wrap><Button onClick={() => navigate(row.url.startsWith('/order-overview/') ? `${row.url}?return_to=${encodeURIComponent(`/operations?${params}`)}` : row.url)}>查看来源</Button>{row.order_id && !row.url.startsWith('/order-overview/') && <Button onClick={() => navigate(`/order-overview/${row.order_id}?return_to=${encodeURIComponent(`/operations?${params}`)}`)}>订单完整查看</Button>}</Space>
      </Card>)}</div>
      {!result.data.results.length && <Empty description="本次筛选没有已登记的待处理项，不代表现场没有其他待办" />}
      <Pagination current={page} total={result.data.count} pageSize={20} showSizeChanger={false} responsive onChange={(value) => change({ page: String(value) })} showTotal={(total) => `共 ${total} 项`} />
    </>}
  </div>
}
