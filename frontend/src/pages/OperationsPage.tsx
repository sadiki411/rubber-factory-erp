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
  const dueScope = params.get('due_scope') || ''
  const page = Math.max(1, Number(params.get('page')) || 1)
  const result = useQuery({
    queryKey: ['operations', q, kind, dueScope, page],
    queryFn: ({ signal }) => orderApi.workbench({ q, kind, ...(dueScope ? { due_scope: dueScope } : {}), page, page_size: 20 }, { signal }),
  })
  const change = (values: Record<string, string>) => {
    const next = new URLSearchParams(params)
    Object.entries(values).forEach(([key, value]) => value ? next.set(key, value) : next.delete(key))
    setParams(next)
  }
  return <div className="page-container operations-page">
    <PageTitle title="全厂待办" description="先查看已登记事实中的待处理项；此页只读，不另建一套账，不自动指派负责人。" extra={<Space wrap><Button onClick={() => navigate('/mold-search')}>查模具 / 扫库位</Button><Button loading={result.isFetching} onClick={() => void result.refetch()}>刷新事实</Button></Space>} />
    <details className="business-basis"><summary>口径说明</summary>欠交不等于欠生产；统计是事项条数，不可相加为产量。后工序未登记时不能推断进度；不推定责任人，不自动派工。</details>
    <Card className="operations-filter-card">
      <Space wrap className="operations-filters">
        <Input.Search key={q} defaultValue={q} allowClear aria-label="搜索待办" placeholder="订单、规格、材质或案号" onSearch={(value) => change({ q: value.trim(), page: '1' })} enterButton="查询" />
        <Select aria-label="待办类别" value={kind} onChange={(value) => change({ kind: value, page: '1' })} options={[{ value: '', label: '全部类别' }, ...kinds]} />
        <Select aria-label="交期范围" value={dueScope} onChange={(value) => change({ due_scope: value, page: '1' })} options={[{ value: '', label: '全部待办' }, { value: 'today', label: '今日交期（订单）' }, { value: 'overdue', label: '已过交期（订单）' }, { value: 'upcoming', label: '未来7日交期（订单）' }]} />
      </Space>
      {result.data && !result.isError && <><Typography.Paragraph type="secondary">{result.data.summary_scope}</Typography.Paragraph><Space wrap>{kinds.map((entry) => <Tag key={entry.value}>{entry.label} {result.data.summary[entry.value]}</Tag>)}</Space></>}
    </Card>
    {result.isError ? <Alert type="error" showIcon title="待办读取失败，不能将失败视为0项" description={(result.error as Error).message} action={<Button onClick={() => void result.refetch()}>重试</Button>} /> : result.isPending ? <Skeleton active /> : <>
      <div className="operations-task-list">{result.data.results.map((row) => {
        const target = new URL(row.url, window.location.origin)
        target.searchParams.set('return_to', `/operations?${params}`)
        const label = { ORDER_SHORTAGE: '看订单', RETURN_PENDING: '看退回单', INVENTORY_QUALITY: '看库位', RECEIPT_UNLINKED: '核对发料' }[row.kind]
        return <article key={row.id} className="operations-task-row">
          <div><Tag>{row.title}</Tag><strong>{row.document_no}</strong><div>{row.specification || '规格未登记'} · {row.material || '材质未登记'}</div></div>
          <div><strong>{row.quantity === null ? '未登记' : Number(row.quantity).toLocaleString('zh-CN')} {row.unit}</strong><div>{row.due_date ? `交期 ${row.due_date}` : row.reason}</div></div>
          {row.owner && <span>{row.owner}</span>}
          <Button onClick={() => navigate(target.pathname + target.search)}>{label}</Button>
        </article>
      })}</div>
      {!result.data.results.length && <Empty description="本次筛选没有已登记的待处理项，不代表现场没有其他待办" />}
      <Pagination current={page} total={result.data.count} pageSize={20} showSizeChanger={false} responsive onChange={(value) => change({ page: String(value) })} showTotal={(total) => `共 ${total} 项`} />
    </>}
  </div>
}
