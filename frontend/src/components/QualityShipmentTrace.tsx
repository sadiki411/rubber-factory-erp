import { Alert, Button, Card, Descriptions, Drawer, Empty, Space, Spin, Tag, Timeline, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { qualityWorkflowApi } from '../api/client'
import { qualityNumber } from '../quality'
import type { QualityCardShipmentTrace, QualityShipmentCardSource } from '../types'

export function ShipmentCardSources({ sources }: { sources: QualityShipmentCardSource[] }) {
  const [selected, setSelected] = useState<QualityShipmentCardSource>()
  const renderSource = (source: QualityShipmentCardSource, index: number) => <Card size="small" key={`${source.card_id || source.card_no}-${source.shipment_unit_no}-${index}`} className="quality-source-card">
    <Space wrap>
      <Typography.Text strong copyable>{source.card_no}</Typography.Text>
      {source.card_id != null && <Button type="link" size="small" onClick={() => setSelected(source)}>出货追溯</Button>}
      {source.shipment_unit_no != null && <Tag>系统第{source.shipment_unit_no}包</Tag>}
      {source.historical_only && <Tag>历史来源</Tag>}
    </Space>
    <div>{source.evidence.map((item) => <Tag key={item.code}>{item.label}</Tag>)}</div>
    {source.quantity_scope === 'PHYSICAL_UNIT' && source.piece_quantity != null && <div>已保存包量：{qualityNumber(source.piece_quantity)} 件 / {source.net_weight_kg == null ? '重量未记录' : `${qualityNumber(source.net_weight_kg, 3)} kg`}</div>}
    <details><summary>查看来源说明</summary><Typography.Text type="secondary">当前系统来源订单：{source.current_system_order ? `${source.current_system_order.order_no} / ${source.current_system_order.item_no || '-'}` : '未记录'}；不等于纸卡原始订单的独立认证。</Typography.Text></details>
  </Card>
  return <div className="quality-shipment-sources">
    {!sources.length ? <Typography.Text type="secondary">未记录明确关联的流程卡；不能据此判断当时未扫码。</Typography.Text> : <>
      <Typography.Paragraph>已找到 {new Set(sources.map((source) => source.card_id || source.card_no)).size} 张关联卡；包序号来自系统绑定，未独立核验实物纸卡。</Typography.Paragraph>
      {sources.slice(0, 2).map(renderSource)}
      {sources.length > 2 && <details><summary>展开其余 {sources.length - 2} 条关联</summary>{sources.slice(2).map(renderSource)}</details>}
    </>}
    {selected?.card_id != null && <ShipmentCardTraceDrawer key={String(selected.card_id)} cardId={selected.card_id} cardNo={selected.card_no} onClose={() => setSelected(undefined)} />}
  </div>
}

export function ShipmentCardTraceDrawer({ cardId, cardNo, onClose }: { cardId: string | number; cardNo: string; onClose: () => void }) {
  const [state, setState] = useState<{ data?: QualityCardShipmentTrace; error?: string }>({})
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    let active = true
    qualityWorkflowApi.getCardShipmentTrace(cardId).then((data) => { if (active) setState({ data }) })
      .catch((error: Error) => { if (active) setState({ error: error.message || '读取失败' }) })
    return () => { active = false }
  }, [cardId, retry])
  return <Drawer open onClose={onClose} width="min(720px, 100vw)" className="quality-shipment-trace-drawer" title={`${cardNo} · 出货追溯（全部历史）`} footer={<Button onClick={onClose}>关闭</Button>}>
    <Alert type="info" showIcon title="本页不受出货列表日期筛选" description="按明确卡身份串联首发、退回、再出货与更正；整单分摊不按关联卡数重复计量。" />
    {!state.data && !state.error && <Spin aria-label="正在读取出货追溯" />}
    {state.error && <Alert type="error" showIcon title="出货追溯读取失败" description={state.error} action={<Button onClick={() => { setState({}); setRetry((value) => value + 1) }}>重试</Button>} />}
    {state.data && <>
      <Descriptions column={1} size="small" style={{ marginTop: 16 }} items={[
        { key: 'count', label: '明确关联', children: `${state.data.shipment_count} 张出货单 / ${state.data.return_count} 条退回记录（含取消历史）` },
        { key: 'chain', label: '同一追踪身份的卡', children: state.data.cards.map((card) => card.card_no).join(' → ') },
        { key: 'paper', label: '原始纸卡订单', children: '本页未核验原始纸卡凭据；不按号码段或交付分摊推断。' },
      ]} />
      {!state.data.events.length ? <Empty description="未找到明确关联历史，不代表现场未处理" /> : <Timeline items={state.data.events.map((event) => ({
        key: event.key,
        color: event.type === 'return' ? 'orange' : event.type === 'correction' ? 'gray' : 'blue',
        title: <span>{event.business_date || '业务日期未登记'} · {({ shipment: '出货记录', reshipment: '再次出货', return: '退回', correction: '更正 / 作废' })[event.type]}</span>,
        content: <Card size="small" className="quality-source-card">
          <Typography.Text strong copyable>{event.document_no}</Typography.Text> <Tag>{event.status}</Tag>
          {event.batch_quantity != null && <div>整张出货单：{qualityNumber(event.batch_quantity)} 件 / {qualityNumber(event.batch_weight_kg, 3)} kg</div>}
          {(event.type === 'shipment' || event.type === 'reshipment') && <Typography.Text type="secondary">{event.quantity_evidence || '数量依据未记录'}{event.batch_quantity == null ? '（不显示为0）' : ''}</Typography.Text>}
          {event.card_sources?.filter((ref) => ref.quantity_scope === 'PHYSICAL_UNIT').map((ref, index) => <div key={index}>此卡系统第{ref.shipment_unit_no}包：{ref.piece_quantity == null ? '数量未记录' : `${qualityNumber(ref.piece_quantity)} 件 / ${qualityNumber(ref.net_weight_kg, 3)} kg`}{ref.historical_only ? '（历史来源）' : ''}</div>)}
          {!!event.allocations?.length && <details><summary>查看整单已保存的订单交付分摊</summary>{event.allocations.map((share, index) => <div key={index}>{share.order_no} / {share.item_no || '-'}：{qualityNumber(share.piece_quantity)} 件 / {qualityNumber(share.net_weight_kg, 3)} kg</div>)}</details>}
          {event.type === 'return' && <div>第{event.return_round ?? '未登记'}轮 · 退回 {event.piece_quantity == null ? '件数未记录' : `${qualityNumber(event.piece_quantity)} 件`}</div>}
          {event.type === 'correction' && <><div>{event.scope}</div><div>原因：{event.reason || '未记录'}；操作者 #{event.operator_id}</div><div>更正前卡：{event.before_card_nos?.join('、') || '未登记'}</div><div>更正后卡：{event.after_card_nos?.join('、') || '未登记'}</div></>}
          <a href={event.url}>查看原单</a>
        </Card>,
      }))} />}
    </>}
  </Drawer>
}
