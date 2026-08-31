import {
  CameraOutlined,
  LinkOutlined,
  QrcodeOutlined,
  SwapOutlined,
} from '@ant-design/icons'
import { Alert, App, Button, Card, Checkbox, Col, DatePicker, Drawer, Empty, Form, Input, List, Row, Select, Space, Tag, Typography } from 'antd'
import { useQuery } from '@tanstack/react-query'
import dayjs, { type Dayjs } from 'dayjs'
import { useEffect, useMemo, useState } from 'react'
import { qualityWorkflowApi, toList } from '../api/client'
import { normalizeProcessCardQrText, qualityNumber } from '../quality'
import type {
  QualityEmployee,
  QualityProcessCard,
  QualityProcessCardScanResult,
  QualityReturnableBatch,
} from '../types'
import { QualityQrScanner } from './QualityQrScanner'

type SourceSelection = {
  batch: QualityReturnableBatch
  shipmentUnitNo: number
}

type PendingCard = {
  cardNo: string
  lookup: QualityProcessCardScanResult
}

type ReturnFormValues = {
  opened_on?: Dayjs
  date_is_approximate?: boolean
  backfill_reason?: string
  primary_reason_id?: number | string
  secondary_reason_ids?: Array<number | string>
  reason?: string
  inspector_ids?: number[]
  notes?: string
}

function activeCard(result: QualityProcessCardScanResult) {
  return result.active_card || result.scanned_card
}

function processCardBinding(result: QualityProcessCardScanResult) {
  const active = activeCard(result)
  return active?.unit_binding || active?.binding || result.binding
}

function currentReturn(result: QualityProcessCardScanResult) {
  return activeCard(result)?.current_return || result.current_return
}

function processCardOrderText(card?: QualityProcessCard | null) {
  if (!card) return '首次扫描，尚未绑定订单'
  const order = card.order
  const orderNo = order?.order_no || card.source_order_no
  const itemNo = order?.item_no || card.source_item_no
  return orderNo ? `${orderNo}${itemNo ? ` / ${itemNo}` : ''}` : '尚未绑定订单'
}

function sourceOptionKey(batch: QualityReturnableBatch, shipmentUnitNo: number) {
  return `${batch.shipment_batch_id}::${shipmentUnitNo}`
}

function sourceSelectionFromKey(value: string, batches: QualityReturnableBatch[]): SourceSelection | undefined {
  const [batchId, unitText] = value.split('::')
  const batch = batches.find((item) => String(item.shipment_batch_id) === batchId)
  const shipmentUnitNo = Number(unitText)
  return batch && Number.isInteger(shipmentUnitNo) ? { batch, shipmentUnitNo } : undefined
}

function scanStatus(card: PendingCard) {
  if (processCardBinding(card.lookup)) return <Tag color="success" icon={<LinkOutlined />}>已锁定原出货</Tag>
  return <Tag color="warning">首次绑定</Tag>
}

export function QualityFlowCardReturnDrawer({
  open,
  employees,
  onClose,
  onSaved,
  onBackfillShipment,
}: {
  open: boolean
  employees: QualityEmployee[]
  onClose: () => void
  onSaved: () => Promise<void>
  onBackfillShipment: () => void
}) {
  const [form] = Form.useForm<ReturnFormValues>()
  const { message } = App.useApp()
  const [scannerOpen, setScannerOpen] = useState(false)
  const [cards, setCards] = useState<PendingCard[]>([])
  const [sources, setSources] = useState<Record<string, SourceSelection>>({})
  const [saving, setSaving] = useState(false)
  const openedOn = Form.useWatch('opened_on', form)
  const dateApproximate = Form.useWatch('date_is_approximate', form)
  const primaryReasonId = Form.useWatch('primary_reason_id', form)

  useEffect(() => {
    if (!open) return
    form.resetFields()
    form.setFieldsValue({ opened_on: dayjs(), date_is_approximate: false, secondary_reason_ids: [], inspector_ids: [] })
    // Opening a fresh business drawer intentionally discards its previous
    // transient scan basket; persisted data remains server-owned.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setCards([])
    setSources({})
    setScannerOpen(true)
  }, [form, open])

  const reasonsQuery = useQuery({
    queryKey: ['quality', 'return-reasons'],
    queryFn: async () => toList(await qualityWorkflowApi.listReturnReasons({ active: true, page_size: 200 })),
    enabled: open,
    retry: false,
  })
  const candidatesQuery = useQuery({
    queryKey: ['quality', 'returnable-batches', 'flow-card-binding'],
    queryFn: async () => toList(await qualityWorkflowApi.listReturnableBatches({ page_size: 200 })),
    enabled: open && cards.some((item) => !processCardBinding(item.lookup)),
    retry: false,
  })

  const reasons = reasonsQuery.data || []
  const candidates = useMemo(() => candidatesQuery.data || [], [candidatesQuery.data])
  const historical = Boolean(openedOn?.isValid() && openedOn.startOf('day').isBefore(dayjs().startOf('day')))
  const sourceOptions = useMemo(() => candidates.flatMap((batch) => (batch.available_batch_numbers || []).map((unitNo) => ({
    value: sourceOptionKey(batch, unitNo),
    label: `${batch.shipment_no} · ${batch.order_no || '未关联订单'}${batch.item_no ? ` / ${batch.item_no}` : ''} · 第${unitNo}批 · ${batch.product_name || ''} ${batch.specification || ''} ${batch.material || ''}`.trim(),
  }))), [candidates])
  const inspectorOptions = employees
    .filter((item) => item.is_active && ['INSPECTOR', 'BOTH'].includes(item.role))
    .map((item) => ({ value: item.id, label: `${item.employee_no} · ${item.name}` }))
  const reasonOptions = reasons.map((item) => ({ value: item.id, label: item.name || item.label || item.code || String(item.id) }))
  const secondaryOptions = reasonOptions.filter((item) => String(item.value) !== String(primaryReasonId ?? ''))

  const closeDrawer = () => {
    setScannerOpen(false)
    setCards([])
    setSources({})
    form.resetFields()
    onClose()
  }

  const handleScan = async (cardNo: string) => {
    let lookup: QualityProcessCardScanResult
    try {
      lookup = await qualityWorkflowApi.scanProcessCard(cardNo)
    } catch (error) {
      const text = (error as Error).message || ''
      // A never-before-recorded card is still valid: scan-return will create
      // and bind it once the operator selects the original shipment.
      if (/404|未找到|不存在/.test(text)) lookup = { code: cardNo }
      else throw error
    }
    const scanned = lookup.scanned_card
    const active = activeCard(lookup)
    if (scanned?.replaced_by_id || (scanned && active && String(scanned.id) !== String(active.id))) {
      throw new Error(`旧流程卡 ${cardNo} 已作废，请扫描替代卡 ${active?.card_no || '（见补卡记录）'}。`)
    }
    const openReturn = currentReturn(lookup)
    if (openReturn && !['RESHIPPED', 'SCRAPPED', 'CANCELLED'].includes(openReturn.status)) {
      throw new Error(`${cardNo} 当前已有“${openReturn.return_label || '待返工'}”记录，请返工完成后在出货端重新扫码。`)
    }
    setCards((items) => [...items, { cardNo: active?.card_no || normalizeProcessCardQrText(cardNo), lookup }])
    return true
  }

  const removeCard = (cardNo: string) => {
    setCards((items) => items.filter((item) => item.cardNo !== cardNo))
    setSources((values) => {
      const next = { ...values }
      delete next[cardNo]
      return next
    })
  }

  const submit = async () => {
    if (!cards.length) {
      message.warning('请先扫描至少一张退回产品的流程卡。')
      return
    }
    const values = await form.validateFields()
    const missingSource = cards.find((item) => !processCardBinding(item.lookup) && !sources[item.cardNo])
    if (missingSource) {
      message.warning(`流程卡 ${missingSource.cardNo} 第一次登记，请选择它对应的原出货批次。`)
      return
    }
    const duplicateSources = cards
      .map((item) => sources[item.cardNo])
      .filter(Boolean)
      .map((item) => sourceOptionKey(item.batch, item.shipmentUnitNo))
    if (new Set(duplicateSources).size !== duplicateSources.length) {
      message.error('两张流程卡不能绑定到同一个原出货物理批次，请核对。')
      return
    }
    const opened = values.opened_on || dayjs()
    const common = {
      opened_on: opened.format('YYYY-MM-DD'),
      date_is_approximate: Boolean(values.date_is_approximate),
      backfill_reason: values.backfill_reason || '',
      primary_reason_id: values.primary_reason_id,
      secondary_reason_ids: values.secondary_reason_ids || [],
      reason: values.reason || '',
      inspector_ids: values.inspector_ids || [],
      notes: values.notes || '',
    }
    const scanCards = cards.map((item) => {
      const source = sources[item.cardNo]
      return {
        card_no: item.cardNo,
        shipment_batch_id: source?.batch.shipment_batch_id,
        shipment_unit_no: source?.shipmentUnitNo,
        order_id: activeCard(item.lookup)?.order_id || source?.batch.order_ids?.[0],
      }
    })
    setSaving(true)
    try {
      if (scanCards.length === 1) await qualityWorkflowApi.scanReturn({ ...scanCards[0], ...common })
      else await qualityWorkflowApi.bulkScanReturn({ cards: scanCards, ...common })
      await onSaved()
      message.success(`已登记 ${scanCards.length} 批退货，每张流程卡均建立独立返工追踪。`)
      closeDrawer()
    } catch (error) {
      message.error((error as Error).message || '扫描退货登记失败')
    } finally {
      setSaving(false)
    }
  }

  return <>
    <Drawer
      open={open}
      onClose={closeDrawer}
      width={720}
      className="quality-return-rework-drawer quality-flow-card-return-drawer"
      title="扫描登记退货返工"
      footer={<Space className="drawer-footer-actions"><Button onClick={closeDrawer}>取消</Button><Button icon={<CameraOutlined />} onClick={() => setScannerOpen(true)}>继续扫码</Button><Button type="primary" loading={saving} disabled={!cards.length} onClick={() => void submit()}>确认登记 {cards.length || ''} 批退货</Button></Space>}
    >
      <Alert type="info" showIcon message="一张流程卡追踪一批产品" description="多批退货可连续扫码后统一填写日期和原因；系统仍为每张流程卡建立独立的第1次、第2次、第3次退货记录。" />
      <Button className="quality-flow-card-scan-button" type="primary" size="large" block icon={<QrcodeOutlined />} onClick={() => setScannerOpen(true)}>扫描流程卡二维码</Button>

      {!cards.length ? <Empty description="尚未扫描退回产品的流程卡"><Button type="primary" icon={<CameraOutlined />} onClick={() => setScannerOpen(true)}>开始扫码</Button></Empty> : <List
        className="quality-flow-card-return-list"
        dataSource={cards}
        renderItem={(item, index) => {
          const card = activeCard(item.lookup)
          const binding = processCardBinding(item.lookup)
          const source = sources[item.cardNo]
          return <List.Item>
            <Card size="small" title={<Space wrap><strong>{item.cardNo}</strong>{scanStatus(item)}<Tag color="blue">第 {index + 1} 批</Tag></Space>} extra={<Button type="link" danger onClick={() => removeCard(item.cardNo)}>移除</Button>}>
              <div className="quality-flow-card-return-facts">
                <span><small>订单 / 项次</small><b>{binding ? `${binding.order_no || '-'}${binding.item_no ? ` / ${binding.item_no}` : ''}` : processCardOrderText(card)}</b></span>
                <span><small>产品 / 规格 / 材质</small><b>{binding ? [binding.product_name, binding.specification, binding.material].filter(Boolean).join(' · ') || '-' : [card?.product_name_snapshot, card?.specification_snapshot, card?.material_snapshot].filter(Boolean).join(' · ') || '首次绑定后自动带入'}</b></span>
              </div>
              {binding ? <Typography.Text type="secondary">原出货 {binding.shipment_no || `#${binding.shipment_batch_id}`} · 第 {binding.shipment_unit_no} 批 · {qualityNumber(binding.piece_quantity)}件 / {qualityNumber(binding.net_weight_kg, 3)}kg</Typography.Text> : <Select
                showSearch
                optionFilterProp="label"
                placeholder={candidatesQuery.isLoading ? '正在读取原出货…' : '首次退货：选择对应的原出货和物理批号'}
                loading={candidatesQuery.isLoading}
                value={source ? sourceOptionKey(source.batch, source.shipmentUnitNo) : undefined}
                options={sourceOptions}
                onChange={(value) => {
                  const selection = sourceSelectionFromKey(value, candidates)
                  if (selection) setSources((values) => ({ ...values, [item.cardNo]: selection }))
                }}
              />}
            </Card>
          </List.Item>
        }}
      />}
      {cards.some((item) => !processCardBinding(item.lookup)) && candidatesQuery.error && <Alert type="warning" showIcon message="原出货读取失败" description={(candidatesQuery.error as Error).message} action={<Button onClick={onBackfillShipment}>补录原出货</Button>} />}

      {!!cards.length && <Form form={form} layout="vertical" requiredMark="optional" className="quality-flow-card-return-form">
        <Typography.Title level={5}>本次共同信息</Typography.Title>
        <Row gutter={12}>
          <Col xs={24} sm={12}><Form.Item name="opened_on" label="退货日期"><DatePicker style={{ width: '100%' }} /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="date_is_approximate" valuePropName="checked" label="历史补录"><Checkbox>日期为大概日期</Checkbox></Form.Item></Col>
        </Row>
        {(historical || dateApproximate) && <Form.Item name="backfill_reason" label="补录说明" rules={[{ required: true, whitespace: true, message: '历史或大概日期请填写补录说明' }]}><Input placeholder="例如：根据原流程卡补录，具体日期不确定" /></Form.Item>}
        <Row gutter={12}>
          <Col xs={24} sm={12}><Form.Item name="primary_reason_id" label="主要退货原因" rules={[{ required: true, message: '请选择一个主要原因' }]}><Select showSearch optionFilterProp="label" loading={reasonsQuery.isLoading} options={reasonOptions} placeholder="用于责任和趋势统计" /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="secondary_reason_ids" label="次要问题标签（可多选）"><Select mode="multiple" showSearch optionFilterProp="label" options={secondaryOptions} placeholder="可不填或选择多项" maxTagCount="responsive" /></Form.Item></Col>
        </Row>
        {reasonsQuery.error && <Alert type="warning" showIcon message="退货原因库暂时读取失败" description="请刷新后重试，避免原因统计缺失。" />}
        <Form.Item name="inspector_ids" label="责任品检员（选填，可多人、可后补）"><Select mode="multiple" allowClear showSearch optionFilterProp="label" options={inspectorOptions} placeholder="暂不填写或选择一名/多名" maxTagCount="responsive" /></Form.Item>
        <Form.Item name="reason" label="具体问题说明（选填）"><Input.TextArea rows={2} maxLength={500} showCount /></Form.Item>
        <Form.Item name="notes" label="备注（选填）"><Input.TextArea rows={2} maxLength={500} showCount /></Form.Item>
      </Form>}
    </Drawer>
    <QualityQrScanner
      open={open && scannerOpen}
      title="连续扫描退货流程卡"
      description="每批产品扫描一次。相同卡号会提醒且不会重复加入；扫完全部退货批次后点“完成”。"
      initialValues={cards.map((item) => item.cardNo)}
      onClose={() => setScannerOpen(false)}
      onScan={handleScan}
    />
  </>
}

export function QualityProcessCardReplacementDrawer({
  open,
  onClose,
  onSaved,
}: {
  open: boolean
  onClose: () => void
  onSaved: () => Promise<void>
}) {
  const { message } = App.useApp()
  const [scannerTarget, setScannerTarget] = useState<'old' | 'new'>()
  const [oldCard, setOldCard] = useState<QualityProcessCard>()
  const [newCardNo, setNewCardNo] = useState('')
  const [notes, setNotes] = useState('')
  const [saving, setSaving] = useState(false)

  const closeReplacement = () => {
    setScannerTarget(undefined)
    setOldCard(undefined)
    setNewCardNo('')
    setNotes('')
    onClose()
  }

  useEffect(() => {
    if (!open) return
    // This drawer is a one-shot replacement workflow, so every opening starts
    // with a clean pair of card numbers.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setOldCard(undefined)
    setNewCardNo('')
    setNotes('')
    setScannerTarget('new')
  }, [open])

  const handleReplacementScan = async (cardNo: string) => {
    if (scannerTarget === 'new') {
      try {
        const existing = await qualityWorkflowApi.scanProcessCard(cardNo)
        if (activeCard(existing)) throw new Error(`新卡号 ${cardNo} 已存在，不能重复作为补卡。`)
      } catch (error) {
        if (!/404|未找到|不存在/.test((error as Error).message || '')) throw error
      }
      setNewCardNo(cardNo)
      setScannerTarget(undefined)
      return true
    }
    const lookup = await qualityWorkflowApi.scanProcessCard(cardNo)
    const card = activeCard(lookup)
    if (!card) throw new Error(`没有找到旧流程卡 ${cardNo}`)
    setOldCard(card)
    setScannerTarget(undefined)
    return true
  }

  const submitReplacement = async () => {
    if (!oldCard || !newCardNo) {
      message.warning('请先扫描新流程卡，再扫描或选择被替代的旧流程卡。')
      return
    }
    setSaving(true)
    try {
      await qualityWorkflowApi.replaceProcessCard(oldCard.id, { new_card_no: newCardNo, notes })
      await onSaved()
      message.success(`补卡完成：${newCardNo} 已继承 ${oldCard.card_no} 的全部追踪历史。`)
      closeReplacement()
    } catch (error) {
      message.error((error as Error).message || '流程卡换号失败')
    } finally {
      setSaving(false)
    }
  }

  return <>
    <Drawer open={open} onClose={closeReplacement} width={560} className="quality-return-rework-drawer" title="流程卡丢失 / 补卡换号" footer={<Space className="drawer-footer-actions"><Button onClick={closeReplacement}>取消</Button><Button type="primary" loading={saving} disabled={!oldCard || !newCardNo} onClick={() => void submitReplacement()}>确认补卡换号</Button></Space>}>
      <Alert type="info" showIcon message="新卡继承旧卡历史" description="旧卡会保留查询记录并标记作废；以后扫描新卡，返工次数会从原来的次数继续累计。" />
      <div className="quality-replacement-steps">
        <Card size="small" title="1. 扫描新补开的流程卡" extra={newCardNo && <Tag color="success">已扫描</Tag>}><Space direction="vertical" style={{ width: '100%' }}><Input value={newCardNo} readOnly placeholder="尚未扫描新卡" /><Button block type="primary" icon={<QrcodeOutlined />} onClick={() => setScannerTarget('new')}>扫描新卡</Button></Space></Card>
        <SwapOutlined className="quality-replacement-arrow" />
        <Card size="small" title="2. 扫描被替代的旧流程卡" extra={oldCard && <Tag color="success">已找到</Tag>}><Space direction="vertical" style={{ width: '100%' }}><Input value={oldCard?.card_no || ''} readOnly placeholder="尚未扫描旧卡" /><Button block icon={<QrcodeOutlined />} onClick={() => setScannerTarget('old')}>扫描旧卡</Button></Space></Card>
      </div>
      <Input.TextArea value={notes} onChange={(event) => setNotes(event.target.value)} rows={3} maxLength={300} showCount placeholder="补卡原因或说明（选填）" />
    </Drawer>
    <QualityQrScanner open={open && Boolean(scannerTarget)} title={scannerTarget === 'new' ? '扫描新补开的流程卡' : '扫描被替代的旧流程卡'} continuous={false} onClose={() => setScannerTarget(undefined)} onScan={handleReplacementScan} />
  </>
}
