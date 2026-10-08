import { Alert, Button, Card, Empty, Skeleton, Tag, Typography } from 'antd'
import { useInfiniteQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'
import { inventoryApi, slotApi } from '../api/client'
import type { PublicLocationHistoryEntry } from '../types'

function movementLocation(entry: PublicLocationHistoryEntry) {
  const source = entry.from_location ? `库位 ${entry.from_location}` : entry.from_machine ? `机台 ${entry.from_machine}` : ''
  const target = entry.to_location ? `库位 ${entry.to_location}` : entry.to_machine ? `机台 ${entry.to_machine}` : ''
  return source && target && source !== target ? `${source} → ${target}` : source || target || '—'
}

export function PublicLocationHistory({ kind, locationKey }: { kind: 'mold' | 'inventory'; locationKey: string }) {
  const historyQuery = useInfiniteQuery({
    queryKey: ['public-location-history', kind, locationKey],
    queryFn: ({ pageParam }) => kind === 'mold'
      ? slotApi.publicHistory(locationKey, pageParam)
      : inventoryApi.publicLocationHistory(locationKey, pageParam),
    initialPageParam: 1,
    getNextPageParam: (lastPage, pages) => lastPage.next ? pages.length + 1 : undefined,
    retry: 1,
  })
  // A newly recorded operation between page requests can shift a page boundary.
  // Do not render the same immutable record twice in that case.
  const records = [...new Map(historyQuery.data?.pages.flatMap((page) => page.results).map((entry) => [entry.id, entry]) || []).values()]
  const count = historyQuery.data?.pages[0]?.count

  return <Card className="public-location-history" title={`库位历史${count !== undefined ? `（${count}条）` : ''}`}>
    <Typography.Paragraph type="secondary">按时间倒序，包含已移出的{kind === 'mold' ? '模具' : '产品'}。名称与规格按现有档案显示。</Typography.Paragraph>
    {historyQuery.isPending && <Skeleton active paragraph={{ rows: 3 }} />}
    {historyQuery.isError && <Alert type="warning" showIcon title="历史记录读取失败" description="当前库位信息不受影响，请重试。" action={<Button onClick={() => { if (historyQuery.isFetchNextPageError) void historyQuery.fetchNextPage(); else void historyQuery.refetch() }} loading={historyQuery.isFetching}>重试</Button>} />}
    {!historyQuery.isPending && !historyQuery.isError && !records.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该库位暂无已登记的历史记录" />}
    <div className="public-location-history-list">
      {records.map((entry) => <article className="public-location-history-entry" key={entry.id}>
        <div className="public-location-history-heading"><Tag>{entry.operation_label}</Tag><time dateTime={entry.created_at}>{dayjs(entry.created_at).format('YYYY-MM-DD HH:mm')}</time></div>
        <dl>
          <dt>{kind === 'mold' ? '模具编号' : '产品编号'}</dt><dd>{entry.item_code || '—'}</dd>
          <dt>产品名称</dt><dd>{entry.item_name || '—'}</dd>
          <dt>{kind === 'mold' ? '模具型号' : '规格 / 材质'}</dt><dd>{[entry.specification, entry.material].filter(Boolean).join(' / ') || '—'}</dd>
          {entry.batch_no && <><dt>库存批次</dt><dd>{entry.batch_no}</dd></>}
          {entry.container_code && <><dt>容器编号</dt><dd>{entry.container_code}</dd></>}
          {entry.quantity_change != null && <><dt>数量变动</dt><dd>{entry.quantity_change === 0 ? '数量未变更' : `${entry.quantity_change > 0 ? '+' : ''}${entry.quantity_change.toLocaleString()} 件`}</dd></>}
          <dt>位置记录</dt><dd>{movementLocation(entry)}</dd>
        </dl>
      </article>)}
    </div>
    {historyQuery.hasNextPage && <Button block className="public-location-history-more" onClick={() => void historyQuery.fetchNextPage()} loading={historyQuery.isFetchingNextPage}>加载更多历史记录</Button>}
    {!!records.length && !historyQuery.hasNextPage && <Typography.Text type="secondary">已显示全部已登记记录</Typography.Text>}
  </Card>
}
