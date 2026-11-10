import { Alert, Button, Card, Descriptions, Skeleton, Space, Table, Tag, Typography } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ApiError, productionApi } from '../api/client'
import { PageTitle } from '../components/PageTitle'
import { ProductionRunParameters } from '../components/ProductionRunParameters'
import { safeBusinessReturn } from '../navigation'
import { formatProductionDate } from '../production'

const STATUS = { PLANNED: '待上机', RUNNING: '生产中', PAUSED_ON_MACHINE: '暂停·模具在机', PAUSED_UNLOADED: '暂停·已下机', COMPLETED: '已完成', CANCELLED: '已取消' }

export function ProductionRecordPage() {
  const { id } = useParams()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const validId = Boolean(id && /^[1-9]\d*$/.test(id))
  const query = useQuery({ queryKey: ['production', 'record', id], queryFn: () => productionApi.detailRun(id!), enabled: validId })
  const run = query.data
  const status = query.error instanceof ApiError ? query.error.status : undefined
  return <div className="page-container production-record-page">
    <PageTitle title={`生产原记录${validId ? ` #${id}` : ''}`} description="按真实任务编号读取，不受生产首页日期、状态和分页限制；本页只读。" extra={<Space wrap><Button onClick={() => navigate(safeBusinessReturn(params.get('return_to'), '/production'))}>返回来源页面</Button><Button disabled={!validId} loading={query.isFetching} onClick={() => void query.refetch()}>刷新</Button></Space>} />
    {!validId ? <Alert type="error" title="生产任务编号无效" /> : query.isError ? <Alert type="error" showIcon title={status === 404 ? '原记录不存在或已移除' : status === 403 ? '没有查看此记录的权限' : '原记录读取失败'} description={query.error.message} action={<Button onClick={() => void query.refetch()}>重试</Button>} /> : !run ? <Skeleton active /> : <>
      <Card><Descriptions column={{ xs: 1, sm: 2, lg: 3 }} items={[
        { key: 'order', label: '订单 / 项次', children: `${run.order_no} / ${run.order_item_no || '未登记'}` },
        { key: 'product', label: '规格 / 材质', children: `${run.specification} / ${run.material || '未登记'}` },
        { key: 'status', label: '状态', children: <Tag>{STATUS[run.status]}</Tag> },
        { key: 'station', label: '机台 / 生产段', children: `${run.station?.code || '未登记'} / 第${run.segment_no || 1}段` },
        { key: 'loaded', label: '上机时间', children: formatProductionDate(run.loaded_at) },
        { key: 'unloaded', label: '下机时间', children: formatProductionDate(run.unloaded_at) },
        { key: 'molds', label: '有效登记模数', children: run.daily_logs?.some((log) => !log.is_cancelled) ? `${run.produced_mold_count} 模` : '未登记' },
        { key: 'physical', label: '物理折算生产量', children: run.daily_logs?.some((log) => !log.is_cancelled) ? `${run.theoretical_quantity ?? '未知'} 件` : '未登记' },
      ]} /><ProductionRunParameters run={run} /></Card>
      <Card title="交接与生产记录"><Typography.Paragraph type="secondary">保留系统交接顺序和作废标记，不按业务日期重新排列累计读数。物理折算量与订单分配量不是同一口径。</Typography.Paragraph><Table rowKey="id" dataSource={run.daily_logs || []} pagination={{ pageSize: 20, showSizeChanger: false }} scroll={{ x: 700 }} columns={[
        { title: '记录', dataIndex: 'id', render: (value) => `#${value}` },
        { title: '业务日', dataIndex: 'date', render: (value) => value || '未登记' },
        { title: '人员', dataIndex: 'operator', render: (value) => value || '未登记' },
        { title: '累计读数', dataIndex: 'cumulative_mold_count', render: (value) => value ?? '不适用' },
        { title: '本段模数', dataIndex: 'produced_mold_count' },
        { title: '当时孔数', dataIndex: 'cavities_snapshot', render: (value) => value ?? '未保存' },
        { title: '状态', dataIndex: 'is_cancelled', render: (value) => value ? '已作废' : '有效' },
      ]} /></Card>
    </>}
  </div>
}
