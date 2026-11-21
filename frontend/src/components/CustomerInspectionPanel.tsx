import { Alert, App, Button, Card, Collapse, Descriptions, Empty, Grid, Select, Space, Spin, Tag, Typography } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { conditionText, customerInspectionApi, type CustomerInspectionGroup } from '../customerInspection'
import { inspectionItemLabel } from '../inspectionItemLabel'

const STATE: Record<string, string> = { PENDING: '原表待核对', CONFLICT: '来源有冲突', CONFIRMED: '来源内容已核对', INHERITED: '沿用已确认版，本单未附检验表', MISSING: '本单未附检验表', CURRENT: '现行核对版', HISTORICAL: '历史已核对版' }

export function CustomerInspectionGroups({ groups }: { groups: CustomerInspectionGroup[] }) {
  const { sm } = Grid.useBreakpoint()
  return <Collapse items={groups.map((g, i) => ({
    key: String(i), label: `客户 ${g.customer_code || '待核'} · 类别 ${g.category || '待核'} · ${g.version || '版号待核'} · ${g.rows.length} 项`,
    children: <><Typography.Paragraph type="secondary">项目号：{g.project_no || '待核'}。类别保留原码，括注供读表参考，测点和量法以翔邦图纸为准。</Typography.Paragraph>
      <Descriptions size="small" layout={sm ? 'horizontal' : 'vertical'} column={1} items={g.rows.map((row, index) => ({
        key: index, label: <span style={{ overflowWrap: 'anywhere' }}>{inspectionItemLabel(row.item)}</span>, children: <><span>下限 {row.lower === '' ? '未提供' : row.lower} · 上限 {row.upper === '' ? '未提供' : row.upper} · {row.unit || '单位未提供'}</span><br />
          <Typography.Text type="secondary" style={{ overflowWrap: 'anywhere' }}>{row.batch ? <a href={`/api/flow-cards/source-files/${row.batch}/`}>{row.file || '原表'}</a> : row.file} {row.sheet}{row.row != null ? ` 第 ${row.row} 行` : ''}</Typography.Text></>,
      }))} /></>,
  }))} />
}

export function OrderCustomerInspectionPanel({ orderId, compact = false }: { orderId: number; compact?: boolean }) {
  const client = useQueryClient()
  const { message } = App.useApp()
  const [selected, setSelected] = useState<number>()
  const [confirming, setConfirming] = useState(false)
  const key = ['customer-inspection', orderId]
  const query = useQuery({queryKey: key, queryFn: () => customerInspectionApi.get(orderId)})
  const mutation = useMutation({
    mutationFn: (body: Parameters<typeof customerInspectionApi.update>[1]) => customerInspectionApi.update(orderId, body),
    onSuccess: () => {
      setConfirming(false)
      void client.invalidateQueries({queryKey: key})
      void client.invalidateQueries({queryKey: ['customer-inspection-product']})
      message.success('已记录本单客户标准，未登记现场检验')
    },
    onError: (error: Error) => message.error(error.message),
  })
  if (query.isPending) return <Spin size="small" />
  if (query.isError) return <Alert type="error" title="客户标准读取失败" action={<Button onClick={() => void query.refetch()}>重试</Button>} />
  const data = query.data.inspection
  if (!data) return null
  return <Card size="small" title="翔邦客户检验标准" extra={data.groups.length > 0 && <Button href={customerInspectionApi.print(orderId)}>打印标准 PDF</Button>}>
    <Space wrap><Tag>客户 {data.customer_code || '待核'}</Tag><Tag>项目 {data.project_no || '待核'}</Tag><Tag color={data.status === 'CONFIRMED' || data.status === 'INHERITED' ? 'green' : 'orange'}>{STATE[data.status]}</Tag></Space>
    <Typography.Paragraph type="secondary">下单方：{data.source}；协力商：{data.supplier || '未提供'}。原表限值不代表已检验或放行。</Typography.Paragraph>
    {!!data.problems.length && <Alert type="warning" title="客户检验要求待核对" description={data.problems.join('；')} />}
    {!!data.changes?.length && <details><summary>与上次核对版不同（{data.changes.length} 项）</summary>
      <Descriptions size="small" column={1} items={data.changes.map((row, index) => ({
        key: index, label: `${row.category} / ${inspectionItemLabel(row.item)}`, children: `${row.previous?.join(' / ') || '无'} → ${row.incoming?.join(' / ') || '本次未提供'}`,
      }))} />
    </details>}
    {!compact && <details><summary>订单原表工艺与模具</summary><Descriptions size="small" column={1} items={[
      {key: 'primary', label: '一次加硫', children: <>{conditionText(data.reference.primary)}{data.reference.primary.raw && <><br />原文：{data.reference.primary.raw}</>}</>},
      {key: 'secondary', label: '二烤', children: <>{conditionText(data.reference.secondary)}{data.reference.secondary.raw && <><br />原文：{data.reference.secondary.raw}</>}</>},
      {key: 'mold', label: '原表模具 / 尺寸', children: `${data.reference.mold_no || '未提供'} / ${data.reference.mold_size || '未提供'}`},
      {key: 'cut', label: '原表料长 / 切料重', children: `${data.reference.material_length || '未提供'} / ${data.reference.cut_weight || '未提供'}`},
    ]} /></details>}
    {data.groups.length ? <CustomerInspectionGroups groups={data.groups} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本单没有原始检验表，不按产品猜客户" />}
    {data.can_review && data.status === 'PENDING' && data.digest && (confirming ?
      <Alert type="info" title="核对无误后，相同客户、项目、类别、版本及限值的订单可复用；不会改变现场测项。"
        action={<Space wrap><Button onClick={() => setConfirming(false)}>取消</Button><Button type="primary" loading={mutation.isPending} onClick={() => mutation.mutate({action:'confirm',digest:data.digest})}>确认原表要求</Button></Space>} /> :
      <Button onClick={() => setConfirming(true)}>核对并确认本单标准</Button>)}
    {data.can_review && data.status === 'MISSING' && !!data.available?.length && <Space wrap>
      <Select aria-label="选择已核实客户与项目" placeholder="本单缺表：请选择已核实客户及项目" value={selected} onChange={setSelected}
        options={data.available.map(r => ({value:r.id,label:`${r.customer_code} / ${r.project_no} · ${r.versions.map(v=>`${v.category}:${v.version}`).join('、')}`}))} />
      <Button disabled={!selected} loading={mutation.isPending} onClick={() => mutation.mutate({action:'inherit',review_id:selected})}>确认客户与项目，沿用该版</Button>
    </Space>}
  </Card>
}

export function ProductCustomerInspectionPanel({ productId }: { productId: number }) {
  const [page, setPage] = useState(1)
  const query = useQuery({queryKey: ['customer-inspection-product', productId, page], queryFn: () => customerInspectionApi.product(productId, page)})
  if (query.isPending) return <Spin size="small" />
  if (query.isError) return <Alert type="error" title="产品客户标准读取失败" action={<Button onClick={() => void query.refetch()}>重试</Button>} />
  return <Card size="small" title="翔邦客户标准（独立于本厂工艺）">
    {query.data.results.map(r => <details key={r.key}>
      <summary>客户 {r.customer_code || '待核'} · {r.project_no || '项目待核'} · {r.groups.map(g=>`${g.category}/${g.version}`).join('、')} · {STATE[r.status] || r.status}</summary>
      <CustomerInspectionGroups groups={r.groups} />
      <Button href={`/order-overview/${r.order_id}`}>查看本单并核对</Button>
    </details>)}
    {!query.data.results.length && <Typography.Text type="secondary">未找到明确关联的翔邦原表。</Typography.Text>}
    {(page > 1 || query.data.has_next) && <Space><Button disabled={page===1} onClick={()=>setPage(page-1)}>上一页</Button><Button disabled={!query.data.has_next} onClick={()=>setPage(page+1)}>下一页</Button></Space>}
  </Card>
}
