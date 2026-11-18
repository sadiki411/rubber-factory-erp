import { Alert, Button, Empty, Input, Pagination, Skeleton, Space } from 'antd'
import { useEffect, useState } from 'react'
import { flowApi, flowRequestKey, finishFlowRequest } from '../flowcards'
import { useReadOnly } from '../access'

const SOURCE_STATES: Record<string, string> = { NEEDS_REVIEW: '待核对：保留原关联快照', CONFIRMED: '已关联来源集合（不重复计量）', REVOKED: '原错误关联已撤销（保留历史）', SUPERSEDED: '原关联已由重新核对版本替代' }

function ReviewSource({ id, state, done }: { id: number; state: string; done: () => void }) {
  const readOnly = useReadOnly()
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  if (readOnly || ['REVOKED', 'SUPERSEDED'].includes(state)) return null
  const save = async (action: string) => {
    setBusy(true); setError('')
    const body = { action, reason }
    const slot = `source-review:${id}`
    try { await flowApi.reviewSource(id, { ...body, request_key: flowRequestKey(slot, body) }); finishFlowRequest(slot); done() }
    catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  return <details><summary>人工核对 / 撤销错误关系（不改数量）</summary><Input placeholder="按实物及当前出货核对的依据 / 撤销原因" value={reason} onChange={(e) => setReason(e.target.value)} /><Space wrap><Button disabled={!reason.trim()} loading={busy} onClick={() => void save('CONFIRM_SET')}>确认仍涉及此来源卡（逐包份额未知）</Button><Button danger disabled={!reason.trim()} loading={busy} onClick={() => void save('REVOKE')}>撤销错误关联并留痕</Button></Space>{error && <Alert type="error" title="复核未完成" description={error} />}</details>
}

export function InternalShipmentTrace({ batchId }: { batchId: number | string }) {
  return <ShipmentTracePage key={batchId} batchId={batchId} />
}

function ShipmentTracePage({ batchId }: { batchId: number | string }) {
  const [open, setOpen] = useState(false)
  const [result, setResult] = useState<{ page: number; data?: Awaited<ReturnType<typeof flowApi.sources>>; error?: string }>()
  const [page, setPage] = useState(1)
  const [revision, setRevision] = useState(0)
  const data = result?.page === page ? result.data : undefined
  const error = result?.page === page ? result.error : undefined
  useEffect(() => {
    if (!open) return
    let active = true
    flowApi.sources(batchId, page).then((data) => { if (active) setResult({ page, data }) }).catch((e: Error) => { if (active) setResult({ page, error: e.message }) })
    return () => { active = false }
  }, [open, batchId, page, revision])
  return <details onToggle={(e) => setOpen(e.currentTarget.open)}><summary>工序流转卡来源（按需读取）</summary>
    {error ? <Alert type="error" title="内部来源读取失败，不视为无来源" description={error} /> : !data ? <Skeleton active paragraph={{ rows: 1 }} /> : !data.results.length ? <Empty description="未建立内部包追溯；不猜填历史" /> : data.results.map((s) => <div key={s.id}><Button href={`/flow-cards/cards/${s.card_id}`}>{s.card_no}</Button>{SOURCE_STATES[s.state] || '状态需核对'}<ReviewSource id={s.id} state={s.state} done={() => setRevision((v) => v + 1)} /></div>)}
    {data && data.count > 20 && <Pagination current={page} total={data.count} pageSize={20} showSizeChanger={false} onChange={setPage} />}
  </details>
}
