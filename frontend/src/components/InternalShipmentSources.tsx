import { Alert, Button, Input, Space, Tag, Typography } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { flowApi, isInternalCardCode, type InternalSource } from '../flowcards'
import { QualityQrScanner } from './QualityQrScanner'

export function InternalShipmentSources({ value, onChange, disabled = false }: { value: Array<InternalSource & { card_no: string }>; onChange: (sources: Array<InternalSource & { card_no: string }>) => void; disabled?: boolean }) {
  const [scanner, setScanner] = useState(false)
  const [code, setCode] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const active = useRef(true)
  const locked = useRef(false)
  useEffect(() => { active.current = true; return () => { active.current = false } }, [])
  const scan = async (raw: string) => {
    if (locked.current) return false
    locked.current = true
    setBusy(true); setError('')
    try {
      const card = await flowApi.scan(raw)
      if (!active.current) return false
      if (!value.some((s) => s.card_id === card.id)) onChange([...value, { card_id: card.id, card_no: card.card_no, scope: 'SET_UNKNOWN', request_key: crypto.randomUUID(), raw_code: raw, evidence: '出货操作员明确选入本次生产来源集合；未确认逐包份额' }])
      setCode(''); return true
    } catch (e) { if (active.current) setError((e as Error).message); return false }
    finally { locked.current = false; if (active.current) setBusy(false) }
  }
  return <details className="business-basis"><summary>生产来源卡（可选，不改变原出货数量） · {value.length} 张</summary>
    <Typography.Paragraph type="secondary">这些卡是本次涉及的来源集合，逐包对应和份额未确认。客户流程卡、来源订单与实际交付分摊分别保留；扫描顺序不代表包序号。</Typography.Paragraph>
    {value.map((s) => <Tag key={s.card_id} closable={!disabled} onClose={() => onChange(value.filter((v) => v.card_id !== s.card_id))}><a href={`/flow-cards/cards/${s.card_id}`} target="_blank" rel="noreferrer">{s.card_no}</a></Tag>)}
    {!disabled && <Space.Compact style={{ width: '100%' }}><Input aria-label="内部工序卡号" value={code} onChange={(e) => setCode(e.target.value)} onPressEnter={() => void scan(code)} placeholder="ERPFC:… 或 FC-…" /><Button disabled={!code} loading={busy} onClick={() => void scan(code)}>添加</Button><Button onClick={() => setScanner(true)}>扫码</Button></Space.Compact>}
    {error && <Alert type="error" title="来源卡读取失败，未添加" description={error} />}
    <QualityQrScanner open={scanner} onClose={() => setScanner(false)} onScan={scan} continuous normalizeValue={(s) => s?.trim() || ''} isValidValue={(s) => isInternalCardCode(s || '')} title="扫描内部工序流转卡" valueNoun="内部卡" invalidMessage="请扫内部工序卡；客户流程卡仍在原区域扫描。" />
  </details>
}
