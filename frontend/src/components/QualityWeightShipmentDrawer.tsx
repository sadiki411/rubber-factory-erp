import {
  Alert,
  App,
  Button,
  Card,
  Col,
  DatePicker,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Statistic,
  Tag,
  Typography,
} from 'antd'
import dayjs, { type Dayjs } from 'dayjs'
import { useEffect, useMemo, useRef, useState } from 'react'
import { qualityApi, qualityWorkflowApi, toList } from '../api/client'
import {
  expectedWeightKg,
  orderUnitWeightG,
  piecesPerBatchFromTotal,
  piecesFromBatchCount,
  piecesFromWeight,
  processCardQuantityUpperLimit,
  qualityNumber,
  repeatedBatchNetWeightKg,
  shipmentQuantityWithinFlowCardLimit,
  shipmentPieceQuantity,
  weightVariancePercent,
} from '../quality'
import type {
  QualityEmployee,
  QualityOrder,
  QualityProcessCard,
  QualityShipment,
  QualityShipmentAllocationPreview,
  QualityShipmentBatch,
  QualityShipmentBatchLine,
  QualityShipmentBatchInput,
} from '../types'

const DRAFT_KEY = 'erp-quality-weight-shipment-drafts-v2'
const TOLERANCE_PERCENT = 10
const MANUAL_ORDER = '__MANUAL_ORDER__'
const EMPTY_PROCESS_CARDS: QualityProcessCard[] = []
const EMPTY_LINE_SEEDS: QualityWeightShipmentLineSeed[] = []
const EMPTY_SHIPMENTS: QualityShipment[] = []
const EMPTY_BATCHES: QualityShipmentBatch[] = []

export interface QualityWeightShipmentLineSeed {
  key?: string | number
  process_card_id?: string | number
  card_no?: string
  order_id?: number
  order?: QualityOrder
  quantity?: number | string | null
  piece_quantity?: number | string | null
  remaining_quantity?: number | string | null
  unit_weight_g?: number | string | null
  net_weight_kg?: number | string | null
  actual_weight_kg?: number | string | null
  single_batch_net_weight_kg?: number | string | null
  product_batch_count?: number | string | null
  process_card_shipment_quantity?: number | string | null
  specification_snapshot?: string
  material_snapshot?: string
  notes?: string
}

export interface QualityWeightShipmentDrawerProps {
  open: boolean
  /** A legacy shipment can still be edited from the daily ledger. */
  shipment?: QualityShipment
  /** A weighted draft/confirmed batch can be edited from workflow management. */
  batch?: QualityShipmentBatch
  orders: QualityOrder[]
  employees: QualityEmployee[]
  processCards?: QualityProcessCard[]
  lines?: QualityWeightShipmentLineSeed[]
  existingShipments?: QualityShipment[]
  existingBatches?: QualityShipmentBatch[]
  initialOrderId?: number
  onClose: () => void
  /** Override persistence when the parent owns query refresh/fallback logic. */
  onSubmit?: (payload: QualityShipmentBatchInput) => Promise<unknown>
  onSaved?: (result?: unknown) => void | Promise<void>
}

type DrawerValues = {
  shipment_no?: string
  shipment_date?: Dayjs
  order_id?: number | string
  product_specification_id?: number | null
  product_name?: string
  specification?: string
  specification_snapshot?: string
  material?: string
  material_snapshot?: string
  unit_weight_g?: number | null
  unit_weight_g_snapshot?: number | null
  single_batch_net_weight_kg?: number | null
  total_net_weight_kg?: number | null
  net_weight_kg?: number | null
  piece_quantity?: number | null
  product_batch_count?: number | null
  batch_count?: number | null
  pieces_per_batch?: number | null
  process_card_shipment_quantity?: number | null
  inspector_id?: number | null
  inspector_ids?: number[]
  notes?: string
  customer?: string
  delivery_info?: string
  backfill_reason?: string
}

interface EditableLine extends QualityWeightShipmentLineSeed {
  key: string
  quantity: number | null
  unit_weight_g: number | null
  net_weight_kg: number | null
  single_batch_net_weight_kg: number | null
  product_batch_count: number
  process_card_shipment_quantity: number | null
}

function numeric(value: unknown) {
  if (value == null || (typeof value === 'string' && value.trim() === '')) return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function text(value: unknown) {
  return String(value ?? '').trim()
}

function orderRemaining(order: QualityOrder) {
  const quantity = numeric(order.order_quantity) || 0
  const explicitRemaining = numeric((order as QualityOrder & { remaining_quantity?: number | string }).remaining_quantity)
    ?? numeric(order.weighted_remaining_quantity)
  if (explicitRemaining != null) return Math.max(0, explicitRemaining)
  const weightedShipped = numeric(order.weighted_shipped_quantity)
  if (weightedShipped != null) return Math.max(0, quantity - weightedShipped)
  const shipped = numeric(order.shipped_quantity) || 0
  return Math.max(0, quantity - shipped)
}

function orderLabel(order: QualityOrder) {
  return [
    order.order_no,
    order.item_no ? `项次${order.item_no}` : '',
    order.batch_no,
    order.product_name || order.product_code,
    order.specification,
    order.material,
  ].filter(Boolean).join(' · ')
}

function inspectorIds(value: QualityShipment | QualityShipmentBatch | undefined) {
  if (!value) return []
  const ids = Array.isArray(value.inspector_ids) ? value.inspector_ids : []
  const nested = Array.isArray(value.inspectors) ? value.inspectors.map((item) => item.id) : []
  const first = value.inspector_id == null ? [] : [value.inspector_id]
  return [...new Set([...ids, ...nested, ...first].filter((item): item is number => Number.isFinite(Number(item))).map(Number))]
}

function allocatedOrderCount(value: unknown) {
  if (!value || typeof value !== 'object') return 0
  const batch = value as QualityShipmentBatch
  const orderIds = (batch.lines || []).map((line) => (
    line.order_id
    ?? line.order?.id
    ?? line.process_card?.order_id
    ?? line.process_card?.order?.id
  )).filter((id): id is number => id != null && Number.isFinite(Number(id))).map(Number)
  if (batch.order_id != null) orderIds.push(Number(batch.order_id))
  return new Set(orderIds).size
}

function seedLines(seeds: QualityWeightShipmentLineSeed[] | undefined, cards: QualityProcessCard[]) {
  if (seeds?.length) {
    return seeds.map((line, index) => {
      const batchCount = numeric(line.product_batch_count) || 1
      const totalWeight = numeric(line.net_weight_kg ?? line.actual_weight_kg)
      const singleWeight = numeric(line.single_batch_net_weight_kg)
        ?? (batchCount === 1 ? totalWeight : null)
      const standardQuantity = numeric(
        line.process_card_shipment_quantity
        ?? line.remaining_quantity
        ?? line.quantity,
      )
      return {
        ...line,
        key: String(line.key ?? line.process_card_id ?? `line-${index}`),
        quantity: numeric(line.piece_quantity ?? line.quantity),
        unit_weight_g: numeric(line.unit_weight_g),
        net_weight_kg: totalWeight,
        single_batch_net_weight_kg: singleWeight,
        product_batch_count: batchCount,
        process_card_shipment_quantity: standardQuantity,
      }
    }) satisfies EditableLine[]
  }
  // A process-card selection can be supplied by id without forcing callers to
  // construct line objects themselves.  Generic order entry uses no lines and
  // is represented by the top-level weight fields in the form.
  return cards.map((card) => ({
    key: String(card.id),
    process_card_id: card.id,
    card_no: card.card_no,
    order_id: card.order_id,
    order: card.order,
    quantity: numeric(card.quantity),
    remaining_quantity: numeric(card.quantity),
    unit_weight_g: numeric(card.unit_weight_g),
    net_weight_kg: numeric(card.theoretical_weight_kg),
    single_batch_net_weight_kg: numeric(card.theoretical_weight_kg),
    product_batch_count: 1,
    process_card_shipment_quantity: numeric(card.quantity),
  })) satisfies EditableLine[]
}

type ShipmentNumberMatch = {
  id?: string | number
  shipment_no?: string
  status?: 'DRAFT' | 'CONFIRMED' | 'VOID' | string
  /** The API returns a legacy shipment or a weighted batch under this key. */
  record?: QualityShipment | QualityShipmentBatch
}

function localDuplicateRecord(
  shipmentNo: string,
  shipment?: QualityShipment,
  batch?: QualityShipmentBatch,
  shipments: QualityShipment[] = [],
  batches: QualityShipmentBatch[] = [],
): ShipmentNumberMatch | undefined {
  const normalized = text(shipmentNo).toUpperCase()
  if (!normalized) return undefined
  const currentId = shipment?.id == null ? String(batch?.id ?? '') : String(shipment.id)
  const records: Array<QualityShipment | QualityShipmentBatch> = [...shipments, ...batches]
  const found = records.find((item) => String(item.id) !== currentId && text(item.shipment_no).toUpperCase() === normalized)
  if (!found) return undefined
  return {
    id: found.id,
    shipment_no: found.shipment_no,
    // Legacy rows have no workflow status and are already posted records.
    status: 'status' in found && found.status ? found.status : 'CONFIRMED',
    record: found,
  }
}

function batchLineSeeds(lines?: QualityShipmentBatchLine[]) {
  if (!lines?.length) return []
  return lines.map((line, index) => {
    const processCard = line.process_card
    const order = line.order || processCard?.order
    return {
      key: String(line.id ?? line.process_card_id ?? `draft-line-${index}`),
      process_card_id: line.process_card_id ?? processCard?.id,
      card_no: line.card_no || processCard?.card_no,
      order_id: line.order_id ?? order?.id ?? processCard?.order_id,
      order,
      quantity: numeric(line.quantity ?? line.piece_quantity),
      piece_quantity: numeric(line.piece_quantity ?? line.quantity),
      remaining_quantity: numeric(line.remaining_quantity ?? line.quantity ?? line.piece_quantity),
      unit_weight_g: numeric(line.unit_weight_g ?? line.unit_weight_g_snapshot),
      net_weight_kg: numeric(line.net_weight_kg),
      single_batch_net_weight_kg: numeric(line.single_batch_net_weight_kg),
      product_batch_count: numeric(line.product_batch_count),
      process_card_shipment_quantity: numeric(line.process_card_shipment_quantity),
      specification_snapshot: line.specification_snapshot,
      material_snapshot: line.material_snapshot,
    } satisfies QualityWeightShipmentLineSeed
  })
}

function lineOrder(line: EditableLine, orders: QualityOrder[]) {
  return line.order || orders.find((item) => item.id === line.order_id)
}

function editableLineMetrics(line: EditableLine) {
  const batchCount = Number.isInteger(line.product_batch_count) && line.product_batch_count > 0
    ? line.product_batch_count
    : 1
  const standardQuantity = numeric(line.process_card_shipment_quantity)
  const singleWeight = numeric(line.single_batch_net_weight_kg)
  const savedTotalWeight = numeric(line.net_weight_kg ?? line.actual_weight_kg)
  const savedQuantity = numeric(line.piece_quantity ?? line.quantity)
  const calculatedSinglePieces = piecesFromWeight(singleWeight, line.unit_weight_g)
  const quantity = calculatedSinglePieces == null
    ? (savedQuantity ?? piecesFromWeight(savedTotalWeight, line.unit_weight_g))
    : calculatedSinglePieces * batchCount
  const singlePieces = calculatedSinglePieces ?? piecesPerBatchFromTotal(quantity, batchCount)
  const actual = singleWeight == null
    ? savedTotalWeight
    : repeatedBatchNetWeightKg(singleWeight, batchCount)
  const expected = expectedWeightKg(
    standardQuantity == null ? null : standardQuantity * batchCount,
    line.unit_weight_g,
  )
  const quantityUpper = processCardQuantityUpperLimit(standardQuantity, TOLERANCE_PERCENT)
  const over = Boolean(
    singlePieces != null
    && quantityUpper != null
    && singlePieces > quantityUpper,
  )
  return {
    batchCount,
    standardQuantity,
    singleWeight,
    singlePieces,
    quantity,
    actual,
    expected,
    quantityUpper,
    over,
    under: Boolean(singlePieces && standardQuantity && singlePieces < standardQuantity),
    missing: !standardQuantity || !numeric(line.unit_weight_g) || !actual || !quantity || !singlePieces,
  }
}

/**
 * Unified weighted shipment entry drawer.
 *
 * It deliberately accepts both process-card lines and a free-form order line:
 * old flow-card operators keep their existing basket workflow, while a new
 * shipment can be entered directly from the daily ledger without first
 * creating a process card.  The payload carries both canonical snapshot names
 * and the short aliases used by older deployments.
 */
export function QualityWeightShipmentDrawer({
  open,
  shipment,
  batch,
  orders,
  employees,
  processCards = EMPTY_PROCESS_CARDS,
  lines: lineSeeds,
  existingShipments = EMPTY_SHIPMENTS,
  existingBatches = EMPTY_BATCHES,
  initialOrderId,
  onClose,
  onSubmit,
  onSaved,
}: QualityWeightShipmentDrawerProps) {
  const [form] = Form.useForm<DrawerValues>()
  const { message } = App.useApp()
  const [saving, setSaving] = useState(false)
  const [duplicate, setDuplicate] = useState(false)
  const [checkingNumber, setCheckingNumber] = useState(false)
  const [draftMatch, setDraftMatch] = useState<QualityShipmentBatch>()
  const draftMatchRef = useRef<QualityShipmentBatch | undefined>(undefined)
  const [loadedDraft, setLoadedDraft] = useState<QualityShipmentBatch>()
  const [loadingDraft, setLoadingDraft] = useState(false)
  const [candidateOrders, setCandidateOrders] = useState<QualityOrder[]>([])
  const [candidateQuery, setCandidateQuery] = useState('')
  const [candidateLoaded, setCandidateLoaded] = useState(false)
  const [loadingCandidates, setLoadingCandidates] = useState(false)
  const [candidateError, setCandidateError] = useState('')
  const candidateRequestRef = useRef(0)
  const [allocationPreview, setAllocationPreview] = useState<QualityShipmentAllocationPreview>()
  const [loadingAllocationPreview, setLoadingAllocationPreview] = useState(false)
  const [allocationPreviewError, setAllocationPreviewError] = useState('')
  const allocationRequestRef = useRef(0)
  const [lines, setLines] = useState<EditableLine[]>([])

  const stableLineSeeds = lineSeeds || EMPTY_LINE_SEEDS
  const lineSeedKey = useMemo(() => (lineSeeds || processCards).map((item) => {
    if ('key' in item && item.key != null) return String(item.key)
    if ('process_card_id' in item && item.process_card_id != null) return String(item.process_card_id)
    return String('id' in item ? item.id : '')
  }).join(','), [lineSeeds, processCards])
  const isLineMode = lines.length > 0
  const shipmentNumberValue = Form.useWatch('shipment_no', form)
  const orderId = Form.useWatch('order_id', form)
  const unitWeight = Form.useWatch('unit_weight_g', form)
  const singleBatchWeight = Form.useWatch('single_batch_net_weight_kg', form)
  const specificationValue = Form.useWatch('specification_snapshot', form)
  const materialValue = Form.useWatch('material_snapshot', form)
  const productBatchCount = Form.useWatch('product_batch_count', form)
  const legacyBatchCount = Form.useWatch('batch_count', form)
  const batchCount = productBatchCount ?? legacyBatchCount
  const processCardShipmentQuantity = Form.useWatch('process_card_shipment_quantity', form)
  const selectedInspectors = Form.useWatch('inspector_ids', form) || []
  const activeBatch = loadedDraft || batch

  useEffect(() => {
    if (open) return
    // Do not carry a previously loaded draft into the next new-shipment
    // drawer.  The parent-provided `batch` remains the authoritative edit
    // target when one is supplied.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoadedDraft(undefined)
    setDraftMatch(undefined)
    draftMatchRef.current = undefined
  }, [open])

  const allKnownOrders = useMemo(() => {
    const seen = new Set<number>()
    return [...candidateOrders, ...orders]
      .filter((order) => {
        if (seen.has(order.id)) return false
        seen.add(order.id)
        return true
      })
  }, [candidateOrders, orders])

  const availableOrders = useMemo(() => {
    if (candidateLoaded) return candidateOrders.filter((order) => orderRemaining(order) > 0)
    // Do not reintroduce potentially stale, already-full orders when the
    // authoritative candidate request fails. Manual entry remains available;
    // an existing draft's original order is injected separately below.
    return []
  }, [candidateLoaded, candidateOrders])

  const selectedOrder = useMemo(() => {
    const id = numeric(orderId)
    return id == null ? undefined : allKnownOrders.find((order) => order.id === id)
  }, [allKnownOrders, orderId])

  const calculatedPieces = useMemo(() => shipmentPieceQuantity({
    totalNetWeightKg: singleBatchWeight,
    unitWeightG: unitWeight,
    batchCount,
  }), [batchCount, singleBatchWeight, unitWeight])
  const singleBatchPieces = useMemo(() => piecesFromWeight(singleBatchWeight, unitWeight), [singleBatchWeight, unitWeight])
  const effectiveBatchCount = useMemo(() => {
    const parsed = numeric(batchCount)
    return parsed && Number.isInteger(parsed) && parsed > 0 ? parsed : 1
  }, [batchCount])
  const standardTotalPieces = useMemo(
    () => piecesFromBatchCount(effectiveBatchCount, processCardShipmentQuantity),
    [effectiveBatchCount, processCardShipmentQuantity],
  )
  const quantityUpperLimit = useMemo(() => {
    return processCardQuantityUpperLimit(processCardShipmentQuantity, TOLERANCE_PERCENT)
  }, [processCardShipmentQuantity])
  const repeatedTotalWeight = useMemo(() => {
    return repeatedBatchNetWeightKg(singleBatchWeight, effectiveBatchCount) || 0
  }, [effectiveBatchCount, singleBatchWeight])
  const quantityOverLimit = Boolean(
    singleBatchPieces
    && processCardShipmentQuantity
    && !shipmentQuantityWithinFlowCardLimit(singleBatchPieces, processCardShipmentQuantity, TOLERANCE_PERCENT),
  )

  const lineMetrics = useMemo(() => lines.reduce((result, line) => {
    const metrics = editableLineMetrics(line)
    return {
      quantity: result.quantity + (metrics.quantity || 0),
      expected: result.expected + (metrics.expected || 0),
      actual: result.actual + (metrics.actual || 0),
      missing: result.missing || metrics.missing,
      over: result.over || metrics.over,
      under: result.under || metrics.under,
    }
  }, { quantity: 0, expected: 0, actual: 0, missing: false, over: false, under: false }), [lines])

  const totalExpected = isLineMode
    ? lineMetrics.expected
    : expectedWeightKg(standardTotalPieces || calculatedPieces, unitWeight) || 0
  const totalActual = isLineMode ? lineMetrics.actual : repeatedTotalWeight
  const overLimit = isLineMode
    ? lineMetrics.over
    : quantityOverLimit
  const underLimit = isLineMode
    ? lineMetrics.under
    : Boolean(singleBatchPieces && numeric(processCardShipmentQuantity) && singleBatchPieces < Number(processCardShipmentQuantity))

  useEffect(() => {
    if (!open) return
    form.resetFields()
    const source = shipment || activeBatch
    const sourceOrder = source && 'order' in source ? source.order : undefined
    const fallbackOrder = initialOrderId != null ? orders.find((item) => item.id === initialOrderId) : sourceOrder
    const sourceSpec = source?.specification_snapshot || source?.specification || fallbackOrder?.specification || ''
    const sourceMaterial = source?.material_snapshot || source?.material || fallbackOrder?.material || ''
    const sourceWeight = numeric(source?.unit_weight_g ?? source?.unit_weight_g_snapshot)
      ?? (fallbackOrder ? orderUnitWeightG(fallbackOrder) : null)
    const sourceTotal = numeric(source?.total_net_weight_kg ?? source?.net_weight_kg)
    const sourceBatchCount = numeric(source?.product_batch_count ?? source?.batch_count)
    const sourceSingleBatchWeight = numeric(source?.single_batch_net_weight_kg)
      ?? (sourceBatchCount == null || sourceBatchCount === 1 ? sourceTotal : null)
    const sourceProcessCardQuantity = numeric(source?.process_card_shipment_quantity ?? source?.pieces_per_batch)
    form.setFieldsValue({
      shipment_no: source?.shipment_no || '',
      // A server draft may intentionally have no date.  Keep that blank so
      // the operator can fill the real date later; only a brand-new entry gets
      // today's convenience default.
      shipment_date: source && source.shipment_date === null
        ? undefined
        : source?.shipment_date
          ? dayjs(source.shipment_date)
          : dayjs(),
      order_id: fallbackOrder?.id,
      product_specification_id: source?.product_specification_id ?? fallbackOrder?.product_specification_id ?? null,
      product_name: source?.product_name || source?.product_name_snapshot || fallbackOrder?.product_name || '',
      specification: sourceSpec,
      specification_snapshot: sourceSpec,
      material: sourceMaterial,
      material_snapshot: sourceMaterial,
      unit_weight_g: sourceWeight,
      unit_weight_g_snapshot: sourceWeight,
      single_batch_net_weight_kg: sourceSingleBatchWeight,
      total_net_weight_kg: sourceTotal,
      net_weight_kg: sourceTotal,
      piece_quantity: numeric(source?.piece_quantity),
      product_batch_count: sourceBatchCount ?? (source ? undefined : 1),
      batch_count: sourceBatchCount ?? (source ? undefined : 1),
      process_card_shipment_quantity: sourceProcessCardQuantity,
      inspector_ids: inspectorIds(source),
      inspector_id: source?.inspector_id ?? inspectorIds(source)[0],
      customer: source?.customer || '',
      delivery_info: source?.delivery_info || '',
      backfill_reason: source?.backfill_reason || '',
      notes: source?.notes || '',
    })
    const draftLines = !shipment && activeBatch?.lines?.length
      ? batchLineSeeds(activeBatch.lines)
      : stableLineSeeds
    // Keep this state reset tied to the drawer opening/line selection only.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLines(seedLines(draftLines, processCards))
    setDuplicate(false)
    setDraftMatch(undefined)
    draftMatchRef.current = undefined
    setCandidateOrders([])
    setCandidateQuery('')
    setCandidateLoaded(false)
    setLoadingCandidates(false)
    setCandidateError('')
    candidateRequestRef.current += 1
    setAllocationPreview(undefined)
    setLoadingAllocationPreview(false)
    setAllocationPreviewError('')
    allocationRequestRef.current += 1
    // Opening the drawer, changing the target record, or replacing the
    // selected workflow-card set are the only events that should reset typed
    // values. Background order/query refreshes must not wipe a mobile form.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeBatch?.id, form, initialOrderId, lineSeedKey, open, shipment?.id])

  useEffect(() => {
    if (!open || !selectedOrder) return
    const currentSpec = text(form.getFieldValue('specification'))
    const currentMaterial = text(form.getFieldValue('material'))
    const currentProduct = text(form.getFieldValue('product_name'))
    // Fill blanks only.  Operators can deliberately override the candidate
    // order's snapshots by typing into the fields afterwards.
    form.setFieldsValue({
      product_name: currentProduct || selectedOrder.product_name,
      specification: currentSpec || selectedOrder.specification,
      specification_snapshot: currentSpec || selectedOrder.specification,
      material: currentMaterial || selectedOrder.material,
      material_snapshot: currentMaterial || selectedOrder.material,
      product_specification_id: form.getFieldValue('product_specification_id') ?? selectedOrder.product_specification_id ?? null,
      unit_weight_g: form.getFieldValue('unit_weight_g') || orderUnitWeightG(selectedOrder) || undefined,
    })
  }, [form, open, selectedOrder])

  const checkShipmentNumber = async (value?: string): Promise<boolean> => {
    const number = text(value ?? form.getFieldValue('shipment_no'))
    if (!number) {
      setDuplicate(false)
      setDraftMatch(undefined)
      draftMatchRef.current = undefined
      return false
    }

    const local = localDuplicateRecord(number, shipment, activeBatch, existingShipments, existingBatches)
    if (local) {
      if (local.status === 'DRAFT' && local.record) {
        const record = local.record as QualityShipmentBatch
        draftMatchRef.current = record
        setDraftMatch(record)
        setDuplicate(false)
      } else {
        setDraftMatch(undefined)
        draftMatchRef.current = undefined
        setDuplicate(true)
      }
      return local.status !== 'DRAFT'
    }

    setCheckingNumber(true)
    try {
      // A legacy shipment and a weighted batch use separate tables.  Check
      // both so the same human-entered number cannot accidentally be reused
      // across the two ledgers during a rolling deployment.
      const excludeId = activeBatch?.id ?? shipment?.id
      const results = await Promise.allSettled([
        qualityWorkflowApi.checkShipmentNo(number, activeBatch?.id),
        qualityApi.checkShipmentNo(number, shipment ? excludeId : undefined),
      ])
      const weighted = results[0].status === 'fulfilled' ? results[0].value : undefined
      const legacy = results[1].status === 'fulfilled' ? results[1].value : undefined
      const found = weighted?.shipment || legacy?.shipment
      const exists = Boolean(weighted?.exists || weighted?.duplicate || legacy?.exists || legacy?.duplicate)
      if (exists && found && 'status' in found && String(found.status || '').toUpperCase() === 'DRAFT') {
        const record = found as QualityShipmentBatch
        draftMatchRef.current = record
        setDraftMatch(record)
        setDuplicate(false)
        return false
      }
      setDraftMatch(undefined)
      draftMatchRef.current = undefined
      setDuplicate(exists)
      return exists
    } catch {
      // Rolling deployments may not expose either check action.  The local
      // duplicate guard still protects records already loaded in this page.
      setDraftMatch(undefined)
      draftMatchRef.current = undefined
      setDuplicate(false)
      return false
    } finally {
      setCheckingNumber(false)
    }
  }

  const continueDraft = async (match: QualityShipmentBatch | undefined = draftMatchRef.current || draftMatch) => {
    if (!match?.id) return
    setLoadingDraft(true)
    try {
      const detail = await qualityWorkflowApi.getShipmentBatch(match.id)
      setLoadedDraft(detail)
      draftMatchRef.current = detail
      setDraftMatch(detail)
      form.setFieldValue('shipment_no', detail.shipment_no)
      message.info(`已载入出货草稿 ${detail.shipment_no}，可继续填写后确认。`)
    } catch (error) {
      // The duplicate-check response already contains enough fields for most
      // deployments.  Keep it as a fallback instead of forcing the operator
      // to abandon an unfinished record when the detail request is unavailable.
      draftMatchRef.current = match
      setLoadedDraft(match)
      message.warning((error as Error).message || '草稿详情读取失败，已使用查重结果继续填写。')
    } finally {
      setLoadingDraft(false)
    }
  }

  const loadCandidates = async (
    search?: string,
    scope?: { specification?: string; material?: string },
  ) => {
    const query = text(search)
    const specification = scope
      ? text(scope.specification)
      : text(form.getFieldValue('specification_snapshot') || form.getFieldValue('specification'))
    const material = scope
      ? text(scope.material)
      : text(form.getFieldValue('material_snapshot') || form.getFieldValue('material'))
    const requestId = candidateRequestRef.current + 1
    candidateRequestRef.current = requestId
    setLoadingCandidates(true)
    setCandidateError('')
    try {
      const payload = await qualityWorkflowApi.listShipmentCandidates({
        q: query || undefined,
        specification: specification || undefined,
        material: material || undefined,
        page_size: 1000,
        candidate: true,
      })
      const candidates = toList(payload as any) as any[]
      const mapped = candidates
        .map((item) => ({ ...(item.order || {}), ...item, remaining_quantity: item.remaining_quantity ?? item.order?.weighted_remaining_quantity }))
        .filter((item): item is QualityOrder => Number.isFinite(Number(item?.id)))
      if (requestId !== candidateRequestRef.current) return
      setCandidateOrders(mapped)
      setCandidateLoaded(true)
    } catch (error) {
      if (requestId !== candidateRequestRef.current) return
      setCandidateLoaded(false)
      setCandidateError((error as Error).message || '候选订单读取失败，请稍后重试。')
    } finally {
      if (requestId === candidateRequestRef.current) setLoadingCandidates(false)
    }
  }

  useEffect(() => {
    if (!open) return
    const timer = window.setTimeout(() => {
      void loadCandidates('', { specification: '', material: '' })
    }, 0)
    return () => window.clearTimeout(timer)
    // Candidate loading is intentionally tied to opening the drawer. Search
    // and exact specification/material refreshes are handled separately.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  useEffect(() => {
    if (!open) return
    const specification = text(specificationValue)
    const material = text(materialValue)
    if (!candidateQuery && !specification && !material) return
    const timer = window.setTimeout(() => {
      if (candidateQuery) void loadCandidates(candidateQuery, { specification: '', material: '' })
      else void loadCandidates('', { specification, material })
    }, 300)
    return () => window.clearTimeout(timer)
    // loadCandidates intentionally reads the current form values; watching
    // the two visible snapshot fields and the order search is sufficient.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidateQuery, materialValue, open, specificationValue])

  useEffect(() => {
    if (!open) return
    const number = text(shipmentNumberValue)
    if (!number || number === text(activeBatch?.shipment_no)) return
    const timer = window.setTimeout(() => { void checkShipmentNumber(number) }, 350)
    return () => window.clearTimeout(timer)
    // Duplicate lookup is intentionally debounced while the operator types;
    // submit still performs a final authoritative check.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeBatch?.shipment_no, open, shipmentNumberValue])

  const chooseOrder = (value?: number | string) => {
    setCandidateQuery('')
    if (value == null) {
      form.setFieldsValue({
        order_id: undefined,
        product_name: '',
        specification: '',
        specification_snapshot: '',
        material: '',
        material_snapshot: '',
        product_specification_id: null,
        unit_weight_g: null,
      })
      void loadCandidates('', { specification: '', material: '' })
      return
    }
    if (value === MANUAL_ORDER) {
      form.setFieldsValue({
        order_id: undefined,
        product_name: '',
        specification: '',
        specification_snapshot: '',
        material: '',
        material_snapshot: '',
        product_specification_id: null,
        unit_weight_g: null,
      })
      void loadCandidates('', { specification: '', material: '' })
      return
    }
    const order = allKnownOrders.find((item) => String(item.id) === String(value))
    if (!order) return
    form.setFieldsValue({
      order_id: order.id,
      product_name: order.product_name,
      specification: order.specification,
      specification_snapshot: order.specification,
      material: order.material,
      material_snapshot: order.material,
      product_specification_id: order.product_specification_id ?? null,
      // Selecting another product is an explicit context switch: use that
      // product's remembered value (or clear the field) rather than carrying
      // over a manually typed weight from the previous product.
      unit_weight_g: orderUnitWeightG(order),
    })
    void loadCandidates('', { specification: order.specification, material: order.material })
  }

  useEffect(() => {
    const selectedId = selectedOrder?.id
    const quantity = numeric(calculatedPieces)
    if (!open || isLineMode || selectedId == null || quantity == null || quantity < 1) {
      allocationRequestRef.current += 1
      const resetTimer = window.setTimeout(() => {
        setAllocationPreview(undefined)
        setLoadingAllocationPreview(false)
        setAllocationPreviewError('')
      }, 0)
      return () => window.clearTimeout(resetTimer)
    }
    const requestId = allocationRequestRef.current + 1
    allocationRequestRef.current = requestId
    const loadingTimer = window.setTimeout(() => {
      if (requestId !== allocationRequestRef.current) return
      setAllocationPreview(undefined)
      setLoadingAllocationPreview(true)
      setAllocationPreviewError('')
    }, 0)
    const timer = window.setTimeout(async () => {
      try {
        const preview = await qualityWorkflowApi.previewShipmentAllocation({
          order_id: selectedId,
          piece_quantity: Math.round(quantity),
        })
        if (requestId !== allocationRequestRef.current) return
        setAllocationPreview(preview)
      } catch (error) {
        if (requestId !== allocationRequestRef.current) return
        setAllocationPreview(undefined)
        setAllocationPreviewError((error as Error).message || '自动分配预览读取失败。')
      } finally {
        if (requestId === allocationRequestRef.current) setLoadingAllocationPreview(false)
      }
    }, 250)
    return () => {
      window.clearTimeout(loadingTimer)
      window.clearTimeout(timer)
    }
  }, [calculatedPieces, isLineMode, open, selectedOrder?.id])

  const updateLine = (index: number, patch: Partial<EditableLine>) => {
    setLines((previous) => previous.map((line, lineIndex) => lineIndex === index ? { ...line, ...patch } : line))
  }

  const submit = async () => {
    const values = await form.validateFields()
    const shipmentNo = text(values.shipment_no)
    const duplicateFound = shipmentNo ? await checkShipmentNumber(shipmentNo) : false
    const editingDraft = Boolean(activeBatch?.id && activeBatch.status !== 'CONFIRMED' && activeBatch.status !== 'VOID')
    const localMatch = shipmentNo
      ? localDuplicateRecord(shipmentNo, shipment, activeBatch, existingShipments, existingBatches)
      : undefined
    if ((localMatch && localMatch.status !== 'DRAFT' && !editingDraft) || duplicateFound) {
      message.error('出货单号已存在且已确认或已作废，请更换后再提交。')
      return
    }
    if ((draftMatchRef.current || draftMatch) && !editingDraft) {
      message.warning('该出货单号已有未完成草稿，请先点击“继续填写草稿”。')
      return
    }
    if (!selectedOrder && !text(values.specification) && !text(values.material)) {
      message.warning('请选择候选订单，或至少填写手工规格和材质。')
      return
    }
    const topUnit = numeric(values.unit_weight_g)
    const topSingleBatchWeight = numeric(values.single_batch_net_weight_kg)
    const topBatchCount = numeric(values.product_batch_count ?? values.batch_count) || 1
    const topProcessCardQuantity = numeric(values.process_card_shipment_quantity ?? values.pieces_per_batch)
    const topTotal = repeatedBatchNetWeightKg(topSingleBatchWeight, topBatchCount)
    const topPieces = shipmentPieceQuantity({
      totalNetWeightKg: topSingleBatchWeight,
      unitWeightG: topUnit,
      batchCount: topBatchCount,
    }) ?? numeric(values.piece_quantity)
    if (!isLineMode && (!topUnit || !topSingleBatchWeight || !topPieces || !topProcessCardQuantity)) {
      message.warning('请填写成品单重、单批实称净重和流程卡出货数量，系统才能计算本次总出货数。')
      return
    }
    if (isLineMode && lineMetrics.missing) {
      message.warning('请为每条流程卡填写件数、产品单重和实称净重。')
      return
    }
    if (overLimit) {
      message.error('存在超过理论重量上限 10% 的明细，提交已被阻止。')
      return
    }
    const inspectorSelection = Array.from(new Set((values.inspector_ids || []).map(Number).filter((id) => Number.isFinite(id))))
    const snapshotSpec = text(values.specification_snapshot || values.specification)
    const snapshotMaterial = text(values.material_snapshot || values.material)
    const payload: QualityShipmentBatchInput = {
      shipment_no: shipmentNo || undefined,
      shipment_date: values.shipment_date?.format('YYYY-MM-DD') || null,
      order_id: numeric(values.order_id),
      order_ids: numeric(values.order_id) == null ? [] : [Number(values.order_id)],
      product_specification_id: values.product_specification_id ?? selectedOrder?.product_specification_id ?? null,
      product_name: text(values.product_name),
      specification: snapshotSpec,
      material: snapshotMaterial,
      specification_snapshot: snapshotSpec,
      material_snapshot: snapshotMaterial,
      unit_weight_g: topUnit,
      unit_weight_g_snapshot: topUnit,
      single_batch_net_weight_kg: isLineMode ? undefined : topSingleBatchWeight,
      total_net_weight_kg: isLineMode ? undefined : topTotal,
      net_weight_kg: isLineMode ? undefined : topTotal,
      product_batch_count: isLineMode ? undefined : topBatchCount,
      batch_count: isLineMode ? undefined : topBatchCount,
      process_card_shipment_quantity: isLineMode ? undefined : topProcessCardQuantity,
      piece_quantity: isLineMode ? undefined : topPieces,
      inspector_ids: inspectorSelection,
      inspector_id: inspectorSelection[0] ?? null,
      client_key: activeBatch?.client_key || `quality-weight-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      notes: text(values.notes),
      confirm_warnings: false,
      lines: isLineMode
        ? lines.map((line) => {
          const metrics = editableLineMetrics(line)
          return {
            process_card_id: line.process_card_id,
            order_id: line.order_id,
            quantity: metrics.quantity || undefined,
            piece_quantity: metrics.quantity || undefined,
            unit_weight_g: numeric(line.unit_weight_g),
            unit_weight_g_snapshot: numeric(line.unit_weight_g),
            expected_weight_kg: metrics.expected,
            actual_weight_kg: metrics.actual || undefined,
            net_weight_kg: metrics.actual || undefined,
            single_batch_net_weight_kg: metrics.singleWeight || undefined,
            product_batch_count: metrics.batchCount,
            process_card_shipment_quantity: metrics.standardQuantity || undefined,
            specification_snapshot: snapshotSpec,
            material_snapshot: snapshotMaterial,
            notes: text(line.notes),
          }
        })
        : [{
          process_card_id: undefined,
          order_id: numeric(values.order_id) ?? undefined,
          quantity: topPieces || undefined,
          piece_quantity: topPieces || undefined,
          unit_weight_g: topUnit,
          unit_weight_g_snapshot: topUnit,
          expected_weight_kg: expectedWeightKg(
            topProcessCardQuantity ? topProcessCardQuantity * topBatchCount : topPieces,
            topUnit,
          ),
          actual_weight_kg: topTotal || undefined,
          net_weight_kg: topTotal || undefined,
          single_batch_net_weight_kg: topSingleBatchWeight || undefined,
          product_batch_count: topBatchCount,
          process_card_shipment_quantity: topProcessCardQuantity || undefined,
          specification_snapshot: snapshotSpec,
          material_snapshot: snapshotMaterial,
        }],
    }
    setSaving(true)
    try {
      let result: unknown
      if (editingDraft && activeBatch?.id) {
        // A duplicate number points at an unfinished server-side draft.  Edit
        // that row in place and confirm it; creating a second row would leave
        // a unique-number conflict and could double count on retry.
        await qualityWorkflowApi.updateShipmentBatch(activeBatch.id, payload as unknown as Record<string, unknown>)
        result = await qualityWorkflowApi.confirmShipmentBatch(activeBatch.id)
      } else if (onSubmit) {
        result = await onSubmit(payload)
      } else if (shipment) {
        // Editing a legacy daily-ledger row remains available from the same
        // drawer, while all new records use the weighted batch endpoint.
        result = await qualityApi.updateShipment(shipment.id, {
          ...payload,
          inspector_id: payload.inspector_id,
          inspector_ids: payload.inspector_ids,
          shipped_quantity: topPieces,
          inspection_quantity: topPieces,
          qualified_quantity: topPieces,
          defective_quantity: 0,
        } as unknown as Record<string, unknown>)
      } else {
        result = await qualityWorkflowApi.createAndConfirmShipmentBatch(payload)
      }
      await onSaved?.(result)
      const orderCount = allocatedOrderCount(result)
      message.success(shipment || batch
        ? '出货记录已更新'
        : orderCount > 1
          ? `重量出货已保存，系统已自动分配到 ${orderCount} 个订单`
          : allocationPreview?.overflow_quantity
            ? '重量出货已保存；无可补订单的超出数量已记入当前订单'
            : '重量出货已保存，订单余量已同步更新')
      onClose()
    } catch (error) {
      message.error((error as Error).message || '重量出货提交失败')
    } finally {
      setSaving(false)
    }
  }

  const saveDraft = async () => {
    const values = form.getFieldsValue(true) as DrawerValues
    const saveLocalCopy = () => {
      try {
        const current = JSON.parse(localStorage.getItem(DRAFT_KEY) || '[]') as unknown[]
        current.push({
          created_at: new Date().toISOString(),
          ...values,
          shipment_date: values.shipment_date?.format?.('YYYY-MM-DD') || null,
          lines,
        })
        localStorage.setItem(DRAFT_KEY, JSON.stringify(current.slice(-30)))
        return true
      } catch {
        return false
      }
    }
    const shipmentNo = text(values.shipment_no)
    const duplicateFound = shipmentNo ? await checkShipmentNumber(shipmentNo) : false
    const matchedDraft = draftMatchRef.current || draftMatch
    if (matchedDraft && !activeBatch) {
      await continueDraft(matchedDraft)
      return
    }
    if (duplicateFound) {
      message.error('该出货单号已确认或已作废，请更换单号。')
      return
    }
    const inspectorSelection = Array.from(new Set((values.inspector_ids || []).map(Number).filter((id: number) => Number.isFinite(id))))
    const snapshotSpec = text(values.specification_snapshot || values.specification)
    const snapshotMaterial = text(values.material_snapshot || values.material)
    const draftBatchCount = numeric(values.product_batch_count ?? values.batch_count) || 1
    const draftSingleBatchWeight = numeric(values.single_batch_net_weight_kg)
    const draftTotalWeight = repeatedBatchNetWeightKg(draftSingleBatchWeight, draftBatchCount)
    const draftProcessCardQuantity = numeric(values.process_card_shipment_quantity ?? values.pieces_per_batch)
    const draftLines = lines
      // The current database requires a positive weight for a persisted line.
      // Keep incomplete line inputs in the local fallback, while still saving
      // the batch header on the server so it can be found from another device.
      .filter((line) => (line.process_card_id != null || line.order_id != null) && (editableLineMetrics(line).actual || 0) > 0)
      .map((line) => {
        const metrics = editableLineMetrics(line)
        return {
          process_card_id: line.process_card_id,
          order_id: line.order_id,
          quantity: metrics.quantity || undefined,
          piece_quantity: metrics.quantity || undefined,
          unit_weight_g: numeric(line.unit_weight_g),
          unit_weight_g_snapshot: numeric(line.unit_weight_g),
          expected_weight_kg: metrics.expected,
          actual_weight_kg: metrics.actual || undefined,
          net_weight_kg: metrics.actual || undefined,
          single_batch_net_weight_kg: metrics.singleWeight || undefined,
          product_batch_count: metrics.batchCount,
          process_card_shipment_quantity: metrics.standardQuantity || undefined,
          specification_snapshot: snapshotSpec,
          material_snapshot: snapshotMaterial,
          notes: text(line.notes),
        }
      })
    const payload: QualityShipmentBatchInput = {
      shipment_no: shipmentNo || undefined,
      shipment_date: values.shipment_date?.format?.('YYYY-MM-DD') || null,
      order_id: numeric(values.order_id),
      product_specification_id: values.product_specification_id ?? selectedOrder?.product_specification_id ?? null,
      product_name: text(values.product_name),
      specification: snapshotSpec,
      material: snapshotMaterial,
      specification_snapshot: snapshotSpec,
      material_snapshot: snapshotMaterial,
      unit_weight_g: numeric(values.unit_weight_g),
      unit_weight_g_snapshot: numeric(values.unit_weight_g),
      single_batch_net_weight_kg: draftLines.length ? undefined : draftSingleBatchWeight,
      total_net_weight_kg: draftLines.length ? undefined : draftTotalWeight,
      net_weight_kg: draftLines.length ? undefined : draftTotalWeight,
      product_batch_count: draftLines.length ? undefined : draftBatchCount,
      batch_count: draftLines.length ? undefined : draftBatchCount,
      process_card_shipment_quantity: draftLines.length ? undefined : draftProcessCardQuantity,
      inspector_ids: inspectorSelection,
      inspector_id: inspectorSelection[0] ?? null,
      client_key: activeBatch?.client_key || `quality-weight-draft-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      notes: text(values.notes),
      lines: draftLines,
    }
    setSaving(true)
    try {
      const result = activeBatch?.id && activeBatch.status !== 'CONFIRMED' && activeBatch.status !== 'VOID'
        ? await qualityWorkflowApi.updateShipmentBatch(activeBatch.id, payload as unknown as Record<string, unknown>)
        : await qualityWorkflowApi.createShipmentBatch(payload)
      setLoadedDraft(result as QualityShipmentBatch)
      draftMatchRef.current = result as QualityShipmentBatch
      setDraftMatch(result as QualityShipmentBatch)
      await onSaved?.(result)
      if (draftLines.length < lines.length) {
        const locallySaved = saveLocalCopy()
        message.warning(locallySaved
          ? '服务器草稿已保存；尚未称重的流程卡明细同时保存在本机。'
          : '服务器草稿已保存，但尚未称重的流程卡明细无法写入本机缓存，请先补重量。')
      } else {
        message.success('服务器草稿已保存，稍后可继续填写并确认。')
      }
    } catch (error) {
      // Keep a local fallback for a rolling deployment where the workflow
      // endpoint has not been migrated yet.  The server path is preferred so
      // another phone/browser can discover the unfinished draft by number.
      const locallySaved = saveLocalCopy()
      message.warning(locallySaved
        ? ((error as Error).message || '服务器草稿保存失败，已暂存到本机。')
        : ((error as Error).message || '服务器和本机草稿均保存失败，请勿关闭页面并检查网络。'))
    } finally {
      setSaving(false)
    }
  }

  const inspectorOptions = employees
    .filter((item) => item.is_active && ['INSPECTOR', 'BOTH'].includes(item.role))
    .map((item) => ({ value: item.id, label: `${item.employee_no} · ${item.name}${item.team ? ` · ${item.team}` : ''}` }))
  const selectableOrders = selectedOrder && activeBatch && !availableOrders.some((order) => order.id === selectedOrder.id)
    ? [selectedOrder, ...availableOrders]
    : availableOrders
  const orderOptions = [
    { value: MANUAL_ORDER, label: '手工输入（无订单关联）' },
    ...selectableOrders.map((order) => ({
      value: order.id,
      label: `${orderLabel(order)} · 剩余${qualityNumber(orderRemaining(order))}件${activeBatch && order.id === selectedOrder?.id && orderRemaining(order) <= 0 ? ' · 原草稿订单' : ''}`,
    })),
  ]

  return (
    <Drawer
      open={open}
      onClose={onClose}
      size={760}
      title={shipment ? `编辑重量出货 · ${shipment.shipment_no}` : activeBatch ? `${activeBatch.status === 'DRAFT' ? '继续填写草稿' : '编辑重量出货'} · ${activeBatch.shipment_no}` : '新增重量出货'}
      className="quality-weight-shipment-drawer"
      footer={<Space className="drawer-footer-actions"><Button onClick={saveDraft}>保存草稿</Button><Button onClick={onClose}>取消</Button><Button type="primary" loading={saving} disabled={duplicate || checkingNumber || Boolean(draftMatch && !activeBatch)} onClick={() => void submit()}>确认出货</Button></Space>}
    >
      <Alert
        className="quality-form-alert"
        type="info"
        showIcon
        message="新出货统一按成品重量登记"
        description="单批只称重一次，再填写相同称重的出货批数；系统会立即计算最终总件数和累计总净重。单批换算件数超过流程卡标准数量 10% 会阻止提交。"
      />
      {duplicate && <Alert type="error" showIcon message="出货单号重复" description="请更换出货单号；系统不会覆盖已有出货记录。" className="quality-weight-duplicate-alert" />}
      {draftMatch && !activeBatch && <Alert
        type="warning"
        showIcon
        className="quality-weight-draft-alert"
        message={`发现未完成草稿：${draftMatch.shipment_no || form.getFieldValue('shipment_no')}`}
        description={<span>该单号已经保存过但尚未确认入账。{draftMatch.order ? `关联订单：${orderLabel(draftMatch.order)}。` : ''}继续填写会打开原草稿并保留原有明细，不会新建重复出货单。</span>}
        action={<Button size="small" loading={loadingDraft} onClick={() => void continueDraft()}>继续填写草稿</Button>}
      />}
      <Form form={form} layout="vertical" requiredMark="optional" className="quality-weight-shipment-form">
        <Card size="small" className="quality-weight-basic-card" title="出货基本信息">
          <Row gutter={14}>
            <Col xs={24} sm={12}>
              <Form.Item name="shipment_no" label="出货单号" extra="可留空，保存时由系统自动生成；手工填写时会自动查重。">
                <Input allowClear placeholder="留空自动生成" onBlur={(event) => void checkShipmentNumber(event.target.value)} suffix={checkingNumber ? <Typography.Text type="secondary">查重中</Typography.Text> : undefined} />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12}>
              <Form.Item name="shipment_date" label="实际出货日期" rules={[{ required: true, message: '请选择实际出货日期' }]}><DatePicker style={{ width: '100%' }} /></Form.Item>
            </Col>
          </Row>
          <Form.Item name="order_id" label="候选订单 / 批次" extra="已出满订单不会显示；本次超出当前订单余量时，系统会自动补入相同规格、相同材质的未完成订单。">
            <Select
              showSearch
              allowClear
              loading={loadingCandidates}
              optionFilterProp="label"
              placeholder="选择候选订单（可选）"
              options={orderOptions}
              onChange={chooseOrder}
              searchValue={candidateQuery}
              onSearch={setCandidateQuery}
              onOpenChange={(visible) => {
                if (!visible) return
                if (!candidateLoaded && !loadingCandidates) {
                  if (selectedOrder) void loadCandidates('', { specification: selectedOrder.specification, material: selectedOrder.material })
                  else void loadCandidates('', { specification: '', material: '' })
                }
              }}
              notFoundContent={loadingCandidates ? '正在读取可出货订单…' : '没有仍可出货的候选订单'}
            />
          </Form.Item>
          {candidateError && <Alert className="quality-weight-candidate-alert" type="warning" showIcon message="候选订单暂时读取失败" description={`${candidateError} 确认出货时服务器仍会按最新订单余量重新核算。`} />}
          {selectedOrder && <Descriptions size="small" column={{ xs: 1, sm: 2 }} bordered className="quality-weight-order-preview"><Descriptions.Item label="订单">{selectedOrder.order_no} / {selectedOrder.item_no || '-'}</Descriptions.Item><Descriptions.Item label="剩余数量">{qualityNumber(orderRemaining(selectedOrder))} 件</Descriptions.Item></Descriptions>}
          <Row gutter={14}>
            <Col xs={24} sm={8}><Form.Item name="product_name" label="产品名称"><Input placeholder="可手工输入" /></Form.Item></Col>
            <Col xs={24} sm={8}><Form.Item name="specification_snapshot" label="规格" rules={[{ required: true, whitespace: true, message: '请输入规格' }]}><Input onChange={(event) => form.setFieldValue('specification', event.target.value)} placeholder="支持手工输入" /></Form.Item></Col>
            <Col xs={24} sm={8}><Form.Item name="material_snapshot" label="材质 / 胶料" rules={[{ required: true, whitespace: true, message: '请输入材质' }]}><Input onChange={(event) => form.setFieldValue('material', event.target.value)} placeholder="支持手工输入" /></Form.Item></Col>
          </Row>
          <Form.Item name="specification" hidden><Input /></Form.Item>
          <Form.Item name="material" hidden><Input /></Form.Item>
          <Form.Item name="product_specification_id" hidden><InputNumber /></Form.Item>
          <Form.Item name="customer" label="客户 / 收货信息"><Input placeholder="可选" /></Form.Item>
          <Form.Item name="delivery_info" label="交接 / 包装说明"><Input.TextArea rows={2} maxLength={300} showCount /></Form.Item>
        </Card>

        <Card size="small" className="quality-weight-inspector-card" title="品检责任">
          <Form.Item name="inspector_ids" label="品检员（选填，可后续补录）" extra={selectedInspectors.length > 1 ? `已选择 ${selectedInspectors.length} 人，将共同计入本批责任` : '新增出货时可以留空，确认后仍可在重量出货批次中补录'}>
            <Select mode="multiple" allowClear showSearch optionFilterProp="label" placeholder="暂不填写或选择一名/多名品检员" options={inspectorOptions} maxTagCount="responsive" />
          </Form.Item>
        </Card>

        <Card size="small" className="quality-weight-measure-card" title="重量与件数">
          {!isLineMode ? <>
            <Row gutter={14}>
              <Col xs={12} sm={6}><Form.Item name="unit_weight_g" label="成品单重(g/件)" rules={[{ required: true, type: 'number', min: 0.00001, message: '请输入大于0的单重' }]}><InputNumber min={0.00001} precision={5} style={{ width: '100%' }} /></Form.Item></Col>
              <Col xs={12} sm={6}><Form.Item name="single_batch_net_weight_kg" label="单批实称净重(kg)" rules={[{ required: true, type: 'number', min: 0.001, message: '请输入单批实称净重' }]}><InputNumber min={0.001} precision={3} style={{ width: '100%' }} /></Form.Item></Col>
              <Col xs={12} sm={6}><Form.Item name="process_card_shipment_quantity" label="流程卡出货数量" rules={[{ required: true, type: 'number', min: 1, message: '请输入流程卡单批出货数量' }]} extra="一批的标准件数"><InputNumber min={1} precision={0} style={{ width: '100%' }} /></Form.Item></Col>
              <Col xs={12} sm={6}><Form.Item name="product_batch_count" label="相同称重批数"><InputNumber min={1} precision={0} style={{ width: '100%' }} placeholder="默认1批" onChange={(value) => form.setFieldValue('batch_count', value)} /></Form.Item></Col>
            </Row>
            <div className="quality-weight-quick-batches" aria-label="批数快捷计算">
              <Typography.Text type="secondary">批数快捷计算：</Typography.Text>
              {[1, 5, 10].map((value) => <Button key={value} size="small" onClick={() => form.setFieldsValue({ product_batch_count: value, batch_count: value })}>{value} 批</Button>)}
              {selectedOrder && <Button size="small" onClick={() => { const pieces = numeric(form.getFieldValue('process_card_shipment_quantity')) || orderRemaining(selectedOrder); form.setFieldsValue({ product_batch_count: 1, batch_count: 1, process_card_shipment_quantity: pieces }) }}>按订单剩余量</Button>}
            </div>
            <Row gutter={14} className="quality-weight-derived-row">
              <Col xs={12} sm={6}><Statistic title="单批称重换算" value={singleBatchPieces || 0} suffix="件" /></Col>
              <Col xs={12} sm={6}><Statistic title="相同称重批数" value={effectiveBatchCount} suffix="批" /></Col>
              <Col xs={12} sm={6}><Statistic title="最终总出货数" value={calculatedPieces || 0} suffix="件" /></Col>
              <Col xs={12} sm={6}><Statistic title="累计总净重" value={repeatedTotalWeight} precision={3} suffix="kg" /></Col>
            </Row>
            {quantityOverLimit && <Alert className="quality-weight-limit-alert" type="error" showIcon message="单批换算数量超过流程卡标准 +10%" description={`单批称重换算为 ${singleBatchPieces} 件，流程卡标准为 ${processCardShipmentQuantity} 件，允许上限为 ${qualityNumber(quantityUpperLimit, 1)} 件。请核对单重、净重或流程卡数量。`} />}
            {calculatedPieces && singleBatchWeight && unitWeight && <Typography.Paragraph type="secondary" className="quality-weight-calculation-note">单批：{singleBatchWeight} kg × 1000 ÷ {unitWeight} g/件 ≈ {singleBatchPieces} 件；合计：{singleBatchPieces} 件 × {effectiveBatchCount} 批 = {calculatedPieces} 件，累计净重 {qualityNumber(repeatedTotalWeight, 3)} kg。</Typography.Paragraph>}
            {selectedOrder && calculatedPieces && <Card
              size="small"
              className="quality-weight-allocation-card"
              title="订单自动分配预览"
              extra={<Tag color="blue">确认时按最新余量复核</Tag>}
            >
              {loadingAllocationPreview && <Typography.Text type="secondary">正在根据相同规格、相同材质订单计算分配…</Typography.Text>}
              {!loadingAllocationPreview && allocationPreviewError && <Alert type="warning" showIcon message="暂时无法显示分配预览" description={`${allocationPreviewError} 可以继续确认，服务器会在入账时重新计算。`} />}
              {!loadingAllocationPreview && allocationPreview && <>
                <Alert
                  showIcon
                  type={allocationPreview.overflow_quantity > 0 ? 'warning' : 'success'}
                  message={allocationPreview.matching_allocated_quantity > 0
                    ? `超出部分已预分配 ${qualityNumber(allocationPreview.matching_allocated_quantity)} 件到其他匹配订单`
                    : allocationPreview.overflow_quantity > 0
                      ? '没有可补足的匹配订单，允许当前订单超额出货'
                      : '本次出货全部计入当前订单'}
                  description={allocationPreview.overflow_quantity > 0
                    ? `匹配订单补足后仍有 ${qualityNumber(allocationPreview.overflow_quantity)} 件，将作为当前订单超额数量记录。`
                    : `本次共 ${qualityNumber(allocationPreview.total_allocated_quantity)} 件；确认时会按最新订单余量重新计算。`}
                />
                <div className="quality-weight-allocation-list">
                  {allocationPreview.allocations.map((allocation, index) => <div
                    className={`quality-weight-allocation-row${allocation.is_overflow ? ' is-overflow' : ''}`}
                    key={`${allocation.order_id}-${allocation.is_overflow ? 'overflow' : 'normal'}-${index}`}
                  >
                    <div className="quality-weight-allocation-order">
                      <Space size={6} wrap>
                        <strong>{allocation.order_no} / {allocation.item_no || '-'}</strong>
                        <Tag color={allocation.is_overflow ? 'orange' : allocation.is_source ? 'blue' : 'green'}>
                          {allocation.is_overflow ? '当前订单超额' : allocation.is_source ? '当前订单' : '自动补入'}
                        </Tag>
                      </Space>
                      <Typography.Text type="secondary">{allocation.due_date ? `交期 ${allocation.due_date}` : '未填写交期'} · 分配前剩余 {qualityNumber(allocation.remaining_before)} 件 → 分配后 {qualityNumber(allocation.remaining_after)} 件</Typography.Text>
                    </div>
                    <strong className="quality-weight-allocation-quantity">{qualityNumber(allocation.allocated_quantity)} 件</strong>
                  </div>)}
                </div>
              </>}
            </Card>}
          </> : <>
            <Alert type="info" showIcon message={`已选择 ${lines.length} 张流程卡`} description="每条明细可调整本次件数和实称净重；系统按每条流程卡理论重量 +10% 校验。" />
            <div className="quality-weight-line-list">
              {lines.map((line, index) => {
                const order = lineOrder(line, orders)
                const metrics = editableLineMetrics(line)
                const variance = weightVariancePercent(metrics.actual, metrics.expected)
                return <Card key={line.key} size="small" className={`quality-weight-line ${metrics.over ? 'is-over' : ''}`}>
                  <div className="quality-weight-line-heading"><div><strong>{line.card_no || `流程卡 ${line.process_card_id}`}</strong><Typography.Text type="secondary">{order ? ` · ${orderLabel(order)}` : ''}</Typography.Text></div>{metrics.over ? <Tag color="error">超上限</Tag> : <Tag color="success">可提交</Tag>}</div>
                  <Row gutter={10}>
                    <Col xs={12} sm={6}><Form.Item label="流程卡出货数量"><InputNumber min={1} max={numeric(line.remaining_quantity) || undefined} precision={0} value={line.process_card_shipment_quantity ?? undefined} onChange={(value) => updateLine(index, { process_card_shipment_quantity: numeric(value) })} style={{ width: '100%' }} /></Form.Item></Col>
                    <Col xs={12} sm={6}><Form.Item label="单重(g)"><InputNumber min={0.00001} precision={5} value={line.unit_weight_g ?? undefined} onChange={(value) => updateLine(index, { unit_weight_g: numeric(value) })} style={{ width: '100%' }} /></Form.Item></Col>
                    <Col xs={12} sm={6}><Form.Item label="单批实称净重(kg)"><InputNumber min={0.001} precision={3} value={line.single_batch_net_weight_kg ?? undefined} onChange={(value) => updateLine(index, { single_batch_net_weight_kg: numeric(value) })} style={{ width: '100%' }} /></Form.Item></Col>
                    <Col xs={12} sm={6}><Form.Item label="相同称重批数"><InputNumber min={1} precision={0} value={line.product_batch_count} onChange={(value) => updateLine(index, { product_batch_count: numeric(value) || 1 })} style={{ width: '100%' }} /></Form.Item></Col>
                  </Row>
                  <Typography.Text type="secondary">单批换算 {metrics.singlePieces == null ? '-' : qualityNumber(metrics.singlePieces, Number.isInteger(metrics.singlePieces) ? 0 : 2)} 件（允许上限 {metrics.quantityUpper ?? '-'} 件） · 最终 {metrics.quantity || '-'} 件 / {metrics.actual == null ? '-' : metrics.actual.toFixed(3)} kg · 偏差 {variance == null ? '-' : `${variance >= 0 ? '+' : ''}${variance.toFixed(2)}%`}</Typography.Text>
                </Card>
              })}
            </div>
          </>}
          {(overLimit || underLimit) && <Alert className="quality-weight-limit-alert" type={overLimit ? 'error' : 'warning'} showIcon message={overLimit ? '超过理论重量 +10%，禁止提交' : '实称净重低于理论重量，请核对后确认'} />}
        </Card>
        <Form.Item name="backfill_reason" label="历史日期补录原因" extra="早于今天的出货日期建议填写原因"><Input.TextArea rows={2} maxLength={300} showCount /></Form.Item>
        <Form.Item name="notes" label="备注"><Input.TextArea rows={3} maxLength={500} showCount placeholder="可记录车次、包装、交接或称重说明" /></Form.Item>
      </Form>
      {!isLineMode && candidateLoaded && !availableOrders.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无仍可出货的候选订单，可直接手工填写规格和材质" />}
      <div className="quality-weight-summary">
        <Statistic title={isLineMode ? '流程卡' : '订单'} value={isLineMode ? lines.length : selectedOrder ? 1 : 0} suffix={isLineMode ? '张' : '项'} />
        <Statistic title="本次件数" value={isLineMode ? lineMetrics.quantity : calculatedPieces || 0} suffix="件" />
        <Statistic title="理论重量" value={isLineMode ? lineMetrics.expected : totalExpected} precision={3} suffix="kg" />
        <Statistic title="实称净重" value={isLineMode ? lineMetrics.actual : totalActual} precision={3} suffix="kg" />
      </div>
    </Drawer>
  )
}

export { MANUAL_ORDER }
