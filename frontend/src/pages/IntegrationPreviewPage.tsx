import { Alert, Button, Card, Empty, Input, Pagination, Select, Skeleton, Space, Tag, Typography } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { orderApi, productionApi } from '../api/client'
import { useReadOnly } from '../access'
import { PageTitle } from '../components/PageTitle'
import type { WorkbenchKind } from '../operations'
import type { ProductionRunStatus } from '../types'
import './IntegrationPreviewPage.css'

const STATUS: Record<ProductionRunStatus, string> = { PLANNED: '待上机', RUNNING: '生产中', PAUSED_ON_MACHINE: '暂停·在机', PAUSED_UNLOADED: '暂停·下机', COMPLETED: '已完成', CANCELLED: '已取消' }
const KINDS: { value: WorkbenchKind; label: string }[] = [{ value: 'ORDER_SHORTAGE', label: '交期与欠交' }, { value: 'RETURN_PENDING', label: '退回待处理' }, { value: 'INVENTORY_QUALITY', label: '库存质量' }, { value: 'RECEIPT_UNLINKED', label: '发料待核实' }]

function savedView() { try { return localStorage.getItem('erp-production-preview-view') || 'register' } catch { return 'register' } }

export function IntegrationPreviewPage() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const readOnly = useReadOnly()
  const scene = params.get('scene') === 'production' ? 'production' : 'workbench'
  const view = params.get('view') || savedView()
  const q = params.get('q') || ''
  const kind = params.get('kind') || ''
  const scope = params.get('scope') || 'near'
  const status = params.get('status') || 'COMPLETED,CANCELLED'
  const from = params.get('from') || ''
  const to = params.get('to') || ''
  const station = params.get('station') || ''
  const page = Math.max(1, Number(params.get('page')) || 1)
  const change = (values: Record<string, string>) => {
    const next = new URLSearchParams(params)
    Object.entries(values).forEach(([key, value]) => value ? next.set(key, value) : next.delete(key))
    setParams(next)
    if (values.view) { try { localStorage.setItem('erp-production-preview-view', values.view) } catch { /* Optional preference only. */ } }
  }
  const tasks = useQuery({ queryKey: ['preview', 'workbench', q, kind, scope, page], queryFn: ({ signal }) => orderApi.workbench({ q, kind, due_scope: kind && kind !== 'ORDER_SHORTAGE' ? '' : scope === 'all' ? '' : scope, page, page_size: 10 }, { signal }), enabled: scene === 'workbench' })
  const runs = useQuery({ queryKey: ['preview', 'production', view, q, status, page, from, to, station], queryFn: ({ signal }) => productionApi.compactHistory({ q, status: view === 'register' ? 'PLANNED,RUNNING,PAUSED_ON_MACHINE,PAUSED_UNLOADED' : status === 'ALL' ? '' : status, is_ledger_only: true, date_from: from || undefined, date_to: to || undefined, station: station ? Number(station) : undefined, page, page_size: 10, ordering: '-created_at' }, { signal }), enabled: scene === 'production' && view !== 'board' })
  const board = useQuery({ queryKey: ['preview', 'board'], queryFn: () => productionApi.board(), enabled: scene === 'production' && view === 'board' })
  const openSource = (path: string) => { const target = new URL(path, window.location.origin); target.searchParams.set('return_to', `/previews/integration?${params}`); navigate(target.pathname + target.search) }
  const error = scene === 'workbench' ? tasks.error : view === 'board' ? board.error : runs.error
  return <div className="page-container integration-preview">
    <Alert type="info" showIcon title="独立预览 · 尚未替换正式首页和生产页" description="读取真实记录；不增加数量账、不自动派工、不改变现有退回再出货流程。正式页面入口保持原样。" />
    <PageTitle title={scene === 'workbench' ? '今天先处理什么' : '生产 · 登记与查询分开'} description="先看已登记事实，需要时再打开完整原记录。" extra={<Button onClick={() => navigate(scene === 'workbench' ? '/operations' : '/production')}>回到正式页面</Button>} />
    <nav aria-label="预览页面"><Button type={scene === 'workbench' ? 'primary' : 'default'} onClick={() => change({ scene: 'workbench', q: '', kind: '', page: '1' })}>工作台预览</Button><Button type={scene === 'production' ? 'primary' : 'default'} onClick={() => change({ scene: 'production', q: '', page: '1' })}>生产页预览</Button></nav>
    {scene === 'workbench' ? <>
      <Space wrap>{KINDS.map((entry) => <Button key={entry.value} type={kind === entry.value ? 'primary' : 'default'} onClick={() => change({ kind: entry.value, page: '1' })}>{entry.label}</Button>)}</Space>
      <Card><Space wrap className="preview-filters"><Input.Search key={q} defaultValue={q} allowClear placeholder="订单、规格、材质或案号" aria-label="预览待办查询" enterButton="查找" onSearch={(value) => change({ q: value.trim(), page: '1' })} /><Select aria-label="预览交期范围" value={scope} onChange={(value) => change({ scope: value, kind: 'ORDER_SHORTAGE', page: '1' })} options={[{ value: 'near', label: '近期交期（前后7日）' }, { value: 'today', label: '今天交期' }, { value: 'overdue', label: '全部逾期（含历史）' }, { value: 'upcoming', label: '未来7日' }, { value: 'all', label: '全部记录（含历史）' }]} /></Space><Typography.Paragraph type="secondary">近期列表没有代替全部历史。欠交只说明交付余额，未关联生产不等于现场没有生产。</Typography.Paragraph></Card>
      {error ? <Alert type="error" title="待办读取失败，不显示为0项" action={<Button onClick={() => void tasks.refetch()}>重试</Button>} /> : !tasks.data ? <Skeleton active /> : <>
        <Typography.Text>当前筛选 {tasks.data.count} 项 · 查询刷新 {tasks.data.generated_at}</Typography.Text>
        <div className="preview-list">{tasks.data.results.map((row) => <article key={row.id}><div><Tag>{row.title}</Tag><Typography.Text copyable>{row.document_no}</Typography.Text><strong>{row.specification || '规格未关联'} / {row.material || '材质未关联'}</strong><span>{row.reason}</span></div><div><strong>{row.quantity ?? '未登记'} {row.unit}</strong><span>{row.due_date ? `交期 ${row.due_date}` : '交期不适用'}{row.owner ? ` · 受理 ${row.owner}` : ''}</span><Button onClick={() => openSource(row.url)}>查看原记录</Button></div></article>)}</div>
        {!tasks.data.results.length && <Empty description="此筛选无已登记待办；可切换全部历史" />}
        <Pagination current={page} total={tasks.data.count} pageSize={10} showSizeChanger={false} responsive onChange={(value) => change({ page: String(value) })} />
      </>}
      <details><summary>历史欠交和其他待核实记录没有删除</summary><Button onClick={() => change({ scope: 'all', kind: '', page: '1' })}>查看全部历史与类别</Button><Typography.Paragraph>完整清单仍可按订单/规格查询；不自动销账、不把二烤参数当已完成工序。</Typography.Paragraph></details>
    </> : <>
      <nav aria-label="生产视图">{[{ value: 'register', label: '生产登记' }, { value: 'board', label: '机台看板' }, { value: 'history', label: '历史查询' }].map((entry) => <Button key={entry.value} type={view === entry.value ? 'primary' : 'default'} onClick={() => change({ view: entry.value, page: '1' })}>{entry.label}</Button>)}</nav>
      {view === 'board' ? <><Typography.Paragraph>这里只表示实时看板的登记占用。手工账不占用看板；看板空闲不等于现场没生产。</Typography.Paragraph>{error ? <Alert type="error" title="机台读取失败" /> : board.isPending ? <Skeleton active /> : <div className="preview-stations">{board.data?.groups.flatMap((group) => group.stations).map((station) => <Card key={station.id} size="small" title={`${station.code}号机台`}><strong>{station.run ? STATUS[station.run.status] : '未登记生产任务'}</strong><p>{station.run?.order_no || '不推断现场状态'}</p>{station.run && <Button onClick={() => openSource(`/production/records/${station.run!.id}`)}>看原记录</Button>}</Card>)}</div>}</> : <>
        {view === 'register' && !readOnly && <Button type="primary" size="large" onClick={() => navigate('/production#production-ledger')}>打开现有手工账登记</Button>}
        <Card><Space wrap className="preview-filters"><Input.Search key={q} defaultValue={q} aria-label="生产历史查询" placeholder="订单、规格、材质或人员" enterButton="查询" allowClear onSearch={(value) => change({ q: value.trim(), page: '1' })} />{view === 'history' && <Select aria-label="历史生产状态" value={status} onChange={(value) => change({ status: value, page: '1' })} options={[{ value: 'COMPLETED,CANCELLED', label: '已结束和已取消' }, { value: 'COMPLETED', label: '已完成' }, { value: 'CANCELLED', label: '已取消' }, { value: 'ALL', label: '全部状态' }]} />}<Input type="date" aria-label="生产开始日期" value={from} onChange={(event) => change({ from: event.target.value, page: '1' })} /><Input type="date" aria-label="生产结束日期" value={to} onChange={(event) => change({ to: event.target.value, page: '1' })} /><Input type="number" min={1} aria-label="生产机台编号" placeholder="机台编号" value={station} onChange={(event) => change({ station: event.target.value, page: '1' })} /></Space><Typography.Paragraph type="secondary">列表只读取摘要，每页10项；打开一项才读取该项完整交接。良率、上下机时间和生产参数在原记录查看，不强制补录。</Typography.Paragraph></Card>
{error ? <Alert type="error" title="生产记录读取失败" action={<Button onClick={() => void runs.refetch()}>重试</Button>} /> : !runs.data ? <Skeleton active /> : <><div className="preview-list">{runs.data.results.map((run) => <article key={run.id}><div><Typography.Text copyable>{run.order_no} / {run.order__item_no || '未登记项次'}</Typography.Text><strong>{run.specification} / {run.material || '未登记'}</strong><span>{run.station__code || '机台未登记'} · 第{run.segment_no}段 · {STATUS[run.status]}</span></div><div><strong>{run.production_record_count ? `${run.recorded_mold_count} 模` : '未登记交接'}</strong><Button onClick={() => openSource(`/production/records/${run.id}`)}>查看交接与参数</Button></div></article>)}</div>{!runs.data.results.length && <Empty description="没有匹配的已登记任务" />}<Pagination current={page} total={runs.data.count} pageSize={10} showSizeChanger={false} responsive onChange={(value) => change({ page: String(value) })} /></>}
      </>}
    </>}
  </div>
}
