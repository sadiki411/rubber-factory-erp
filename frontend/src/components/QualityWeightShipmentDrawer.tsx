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
  Radio,
  Row,
  Select,
  Space,
  Statistic,
  Tag,
  Typography,
} from 'antd'
import { QrcodeOutlined } from '@ant-design/icons'
import dayjs, { type Dayjs } from 'dayjs'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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
  QualityShipmentOrderAllocation,
  QualityProcessCardScanResult,
  QualityReworkCase,
} from '../types'
import { QualityQrScanner } from './QualityQrScanner'

const DRAFT_KEY = 'erp-quality-weight-shipment-drafts-v2'
const TOLERANCE_PERCENT = 10
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
  /** Increment for every new entry session.  It prevents a kept-alive drawer
   * from reusing a previous scanner/form instance when the parent opens a new
   * shipment without an intermediate unmount. */
  resetKey?: string | number
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

type WeightEntryMode = 'same' | 'individual'

type AllocationPreviewGroup = {
  key: string
  orderId: number
  pieceQuantity: number
  specification: string
  material: string
  lineCount: number
}

type AllocationPreviewResult = AllocationPreviewGroup & {
  preview?: QualityShipmentAllocationPreview
  error?: string
}

type ScannedLineOverride = {
  unit_weight_g?: number | null
  single_batch_net_weight_kg?: number | null
  process_card_shipment_quantity?: number | null
  product_batch_count?: number
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

function processCardStandardQuantity(card?: QualityProcessCard | null) {
  // `delivered_piece_quantity` is commonly zero on a card that has not yet
  // shipped.  Zero is not the printed package standard and `??` would wrongly
  // keep it instead of falling back.  The card's quantity is authoritative;
  // the delivered value only supports older rows that lack quantity.
  const quantity = numeric(card?.quantity)
  if (quantity != null && quantity > 0) return quantity
  const delivered = numeric(card?.delivered_piece_quantity)
  return delivered != null && delivered > 0 ? delivered : null
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

function shipmentAllocationSummary(value: unknown) {
  if (!value || typeof value !== 'object') return { orderCount: 0, overflowQuantity: 0, hasAllocations: false }
  const batch = value as QualityShipmentBatch
  const nested = (batch.lines || []).flatMap((line) => line.order_allocations || [])
  const allocations: QualityShipmentOrderAllocation[] = batch.order_allocations?.length
    ? batch.order_allocations
    : nested
  if (allocations.length) {
    const orderIds = allocations
      .map((allocation) => Number(allocation.order_id))
      .filter(Number.isFinite)
    const overflowQuantity = allocations.reduce((sum, allocation) => (
      allocation.is_overflow ? sum + (numeric(allocation.piece_quantity) || 0) : sum
    ), 0)
    return {
      orderCount: new Set(orderIds).size,
      overflowQuantity,
      hasAllocations: true,
    }
  }

  // Rolling deployments may return the pre-allocation serializer shape. Keep
  // the success message useful without claiming how the split was produced.
  const orderIds = (batch.lines || []).map((line) => (
    line.order_id
    ?? line.order?.id
    ?? line.process_card?.order_id
    ?? line.process_card?.order?.id
  )).filter((id): id is number => id != null && Number.isFinite(Number(id))).map(Number)
  if (batch.order_id != null) orderIds.push(Number(batch.order_id))
  return { orderCount: new Set(orderIds).size, overflowQuantity: 0, hasAllocations: false }
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
  lines: lineSeeds,
  existingShipments = EMPTY_SHIPMENTS,
  existingBatches = EMPTY_BATCHES,
  initialOrderId,
  resetKey,
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
  const [allocationPreviews, setAllocationPreviews] = useState<AllocationPreviewResult[]>([])
  const [loadingAllocationPreview, setLoadingAllocationPreview] = useState(false)
  const allocationRequestRef = useRef(0)
  const [lines, setLines] = useState<EditableLine[]>([])
  const [scannerOpen, setScannerOpen] = useState(false)
  const [scannedCards, setScannedCards] = useState<Array<{ cardNo: string; lookup: QualityProcessCardScanResult }>>([])
  const [weightEntryMode, setWeightEntryMode] = useState<WeightEntryMode>('same')
  // In per-card mode the scanned cards become one editable shipment line per
  // physical package.  Keep only operator overrides here; card/order facts
  // continue to come from the scan response so a refresh cannot overwrite a
  // changed weight or standard quantity.
  const [scannedLineOverrides, setScannedLineOverrides] = useState<Record<string, ScannedLineOverride>>({})
  const shipmentCheckRequestRef = useRef(0)
  const entrySessionRef = useRef(0)
  const submittingRef = useRef(false)
  const [reshipCase, setReshipCase] = useState<QualityReworkCase>()

  const stableLineSeeds = lineSeeds || EMPTY_LINE_SEEDS
  const lineSeedKey = useMemo(() => (lineSeeds || EMPTY_LINE_SEEDS).map((item) => {
    if ('key' in item && item.key != null) return String(item.key)
    if ('process_card_id' in item && item.process_card_id != null) return String(item.process_card_id)
    return String('id' in item ? item.id : '')
  }).join(','), [lineSeeds])
  const basketLineMode = lines.length > 0
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

  const allKnownOrders = useMemo(() => {
    const seen = new Set<number>()
    const scannedOrders = scannedCards.map((item) => (
      item.lookup.active_card?.order || item.lookup.scanned_card?.order
    )).filter((order): order is QualityOrder => Boolean(order?.id))
    const lineOrders = lines.map((line) => line.order).filter((order): order is QualityOrder => Boolean(order?.id))
    return [...candidateOrders, ...orders, ...scannedOrders, ...lineOrders]
      .filter((order) => {
        if (seen.has(order.id)) return false
        seen.add(order.id)
        return true
      })
  }, [candidateOrders, lines, orders, scannedCards])

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

  const scannedLines = useMemo<EditableLine[]>(() => {
    if (basketLineMode || !scannedCards.length) return []
    const fallbackOrderId = numeric(orderId)
    const fallbackOrder = selectedOrder
    return scannedCards.map((item, index) => {
      const card = item.lookup.active_card || item.lookup.scanned_card
      const override = scannedLineOverrides[item.cardNo] || {}
      const cardOrderId = numeric(card?.order_id)
      const lineOrderId = cardOrderId ?? fallbackOrderId ?? undefined
      const lineOrder = allKnownOrders.find((candidate) => candidate.id === lineOrderId) || fallbackOrder
      const cardQuantity = processCardStandardQuantity(card)
      const standardQuantity = override.process_card_shipment_quantity
        ?? cardQuantity
        ?? numeric(processCardShipmentQuantity)
      const cardUnit = numeric(card?.unit_weight_g)
      const lineUnit = override.unit_weight_g ?? cardUnit ?? numeric(unitWeight)
      const lineSingleWeight = override.single_batch_net_weight_kg ?? null
      return {
        key: `scanned-${item.cardNo}-${index}`,
        process_card_id: card?.id,
        card_no: item.cardNo,
        order_id: lineOrderId,
        order: lineOrder,
        quantity: cardQuantity,
        piece_quantity: null,
        remaining_quantity: cardQuantity,
        unit_weight_g: lineUnit,
        net_weight_kg: lineSingleWeight,
        single_batch_net_weight_kg: lineSingleWeight,
        product_batch_count: override.product_batch_count || 1,
        process_card_shipment_quantity: standardQuantity,
        specification_snapshot: card?.specification_snapshot || text(specificationValue),
        material_snapshot: card?.material_snapshot || text(materialValue),
      }
    })
  }, [allKnownOrders, basketLineMode, materialValue, orderId, processCardShipmentQuantity, scannedCards, scannedLineOverrides, selectedOrder, specificationValue, unitWeight])

  // A scan is a physical package.  In the equal-weight shortcut, make the
  // batch count follow the number of scanned cards (while preserving an
  // intentionally larger count for unscanned packages).
  useEffect(() => {
    if (!open || basketLineMode || weightEntryMode !== 'same' || !scannedCards.length) return
    const current = numeric(form.getFieldValue('product_batch_count')) || 0
    if (current < scannedCards.length) {
      form.setFieldsValue({ product_batch_count: scannedCards.length, batch_count: scannedCards.length })
    }
  }, [basketLineMode, form, open, scannedCards.length, weightEntryMode])

  const scannedVariableMode = !basketLineMode && weightEntryMode === 'individual' && scannedCards.length > 0
  const activeLines = basketLineMode ? lines : scannedVariableMode ? scannedLines : EMPTY_LINE_SEEDS as EditableLine[]
  const isLineMode = basketLineMode || scannedVariableMode

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

  const lineMetrics = useMemo(() => activeLines.reduce((result, line) => {
    const metrics = editableLineMetrics(line)
    return {
      quantity: result.quantity + (metrics.quantity || 0),
      expected: result.expected + (metrics.expected || 0),
      actual: result.actual + (metrics.actual || 0),
      missing: result.missing || metrics.missing,
      over: result.over || metrics.over,
      under: result.under || metrics.under,
    }
  }, { quantity: 0, expected: 0, actual: 0, missing: false, over: false, under: false }), [activeLines])

  const allocationPreviewGroups = useMemo<AllocationPreviewGroup[]>(() => {
    if (!isLineMode) {
      const quantity = numeric(calculatedPieces)
      if (!selectedOrder || quantity == null || quantity < 1) return []
      const selectedId = selectedOrder.id
      return [{
        key: `direct-${selectedId}`,
        orderId: selectedId,
        pieceQuantity: Math.round(quantity),
        specification: text(selectedOrder.specification || specificationValue),
        material: text(selectedOrder.material || materialValue),
        lineCount: effectiveBatchCount,
      }]
    }

    const groups = new Map<string, AllocationPreviewGroup>()
    activeLines.forEach((line) => {
      const order = lineOrder(line, allKnownOrders)
      const orderId = numeric(line.order_id ?? order?.id)
      const quantity = numeric(editableLineMetrics(line).quantity)
      if (orderId == null || quantity == null || quantity < 1) return
      const specification = text(line.specification_snapshot || order?.specification)
      const material = text(line.material_snapshot || order?.material)
      const productKey = specification && material
        ? `${specification.toLocaleLowerCase()}\u0000${material.toLocaleLowerCase()}`
        : `order-${orderId}`
      const current = groups.get(productKey)
      if (current) {
        current.pieceQuantity += Math.round(quantity)
        current.lineCount += 1
      } else {
        groups.set(productKey, {
          key: productKey,
          orderId,
          pieceQuantity: Math.round(quantity),
          specification,
          material,
          lineCount: 1,
        })
      }
    })
    return [...groups.values()]
  }, [activeLines, allKnownOrders, calculatedPieces, effectiveBatchCount, isLineMode, materialValue, selectedOrder, specificationValue])

  const allocationPreviewGroupKey = useMemo(() => JSON.stringify(allocationPreviewGroups.map((group) => ({
    key: group.key,
    orderId: group.orderId,
    pieceQuantity: group.pieceQuantity,
    lineCount: group.lineCount,
  }))), [allocationPreviewGroups])

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
  const unscannedBatchCount = Math.max(0, effectiveBatchCount - scannedCards.length)

  const resetTransientState = useCallback(() => {
    // Invalidate scanner lookups and saves started by the session being
    // closed.  A slow response from the previous drawer must never write into
    // the next new-shipment session.
    entrySessionRef.current += 1
    submittingRef.current = false
    form.resetFields()
    setLines([])
    setScannedCards([])
    setScannedLineOverrides({})
    setWeightEntryMode('same')
    setReshipCase(undefined)
    setScannerOpen(false)
    setDuplicate(false)
    setCheckingNumber(false)
    shipmentCheckRequestRef.current += 1
    setDraftMatch(undefined)
    draftMatchRef.current = undefined
    setLoadedDraft(undefined)
    setLoadingDraft(false)
    setCandidateOrders([])
    setCandidateQuery('')
    setCandidateLoaded(false)
    setLoadingCandidates(false)
    setCandidateError('')
    candidateRequestRef.current += 1
    setAllocationPreviews([])
    setLoadingAllocationPreview(false)
    allocationRequestRef.current += 1
  }, [form])

  const closeDrawer = useCallback(() => {
    resetTransientState()
    onClose()
  }, [onClose, resetTransientState])

  useEffect(() => {
    if (!open) return
    entrySessionRef.current += 1
    submittingRef.current = false
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
    // `processCards` is the page-wide lookup dataset, not an implicit
    // selection. Only explicit line seeds (the shipment basket) may activate
    // line mode; otherwise every historic card would reappear in the next new
    // shipment and hide the scanner.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLines(seedLines(draftLines, EMPTY_PROCESS_CARDS))
    setScannedCards([])
    setScannedLineOverrides({})
    setWeightEntryMode('same')
    setReshipCase(undefined)
    setScannerOpen(false)
    setDuplicate(false)
    setCheckingNumber(false)
    shipmentCheckRequestRef.current += 1
    setDraftMatch(undefined)
    draftMatchRef.current = undefined
    setCandidateOrders([])
    setCandidateQuery('')
    setCandidateLoaded(false)
    setLoadingCandidates(false)
    setCandidateError('')
    candidateRequestRef.current += 1
    setAllocationPreviews([])
    setLoadingAllocationPreview(false)
    allocationRequestRef.current += 1
    // Opening the drawer, changing the target record, or replacing the
    // selected workflow-card set are the only events that should reset typed
    // values. Background order/query refreshes must not wipe a mobile form.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeBatch?.id, form, initialOrderId, lineSeedKey, open, resetKey, shipment?.id])

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
    const requestId = shipmentCheckRequestRef.current + 1
    shipmentCheckRequestRef.current = requestId
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
      if (requestId !== shipmentCheckRequestRef.current || number !== text(form.getFieldValue('shipment_no'))) return false
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
      if (requestId !== shipmentCheckRequestRef.current) return false
      setDraftMatch(undefined)
      draftMatchRef.current = undefined
      setDuplicate(false)
      return false
    } finally {
      if (requestId === shipmentCheckRequestRef.current) setCheckingNumber(false)
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
    if (!open || !allocationPreviewGroups.length) {
      allocationRequestRef.current += 1
      const resetTimer = window.setTimeout(() => {
        setAllocationPreviews([])
        setLoadingAllocationPreview(false)
      }, 0)
      return () => window.clearTimeout(resetTimer)
    }
    const requestId = allocationRequestRef.current + 1
    allocationRequestRef.current = requestId
    const loadingTimer = window.setTimeout(() => {
      if (requestId !== allocationRequestRef.current) return
      setAllocationPreviews([])
      setLoadingAllocationPreview(true)
    }, 0)
    const timer = window.setTimeout(async () => {
      const results = await Promise.all(allocationPreviewGroups.map(async (group): Promise<AllocationPreviewResult> => {
        try {
          const preview = await qualityWorkflowApi.previewShipmentAllocation({
            order_id: group.orderId,
            piece_quantity: group.pieceQuantity,
          })
          return { ...group, preview }
        } catch (error) {
          return { ...group, error: (error as Error).message || '自动分配预览读取失败。' }
        }
      }))
      if (requestId !== allocationRequestRef.current) return
      setAllocationPreviews(results)
      setLoadingAllocationPreview(false)
    }, 250)
    return () => {
      window.clearTimeout(loadingTimer)
      window.clearTimeout(timer)
    }
    // The serialized key prevents weight-line edits that do not change the
    // allocation inputs from repeatedly issuing the same preview requests.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allocationPreviewGroupKey, open])

  const updateLine = (index: number, patch: Partial<EditableLine>) => {
    setLines((previous) => previous.map((line, lineIndex) => lineIndex === index ? { ...line, ...patch } : line))
  }

  const updateActiveLine = (index: number, patch: Partial<EditableLine>) => {
    if (!scannedVariableMode) {
      updateLine(index, patch)
      return
    }
    const cardNo = scannedCards[index]?.cardNo
    if (!cardNo) return
    setScannedLineOverrides((previous) => ({
      ...previous,
      [cardNo]: {
        ...previous[cardNo],
        ...('unit_weight_g' in patch ? { unit_weight_g: numeric(patch.unit_weight_g) } : {}),
        ...('single_batch_net_weight_kg' in patch ? { single_batch_net_weight_kg: numeric(patch.single_batch_net_weight_kg) } : {}),
        ...('process_card_shipment_quantity' in patch ? { process_card_shipment_quantity: numeric(patch.process_card_shipment_quantity) } : {}),
        product_batch_count: 1,
      },
    }))
  }

  const changeWeightEntryMode = (mode: WeightEntryMode) => {
    setWeightEntryMode(mode)
    if (mode !== 'individual') return
    const commonUnit = numeric(form.getFieldValue('unit_weight_g'))
    const commonWeight = numeric(form.getFieldValue('single_batch_net_weight_kg'))
    const commonQuantity = numeric(form.getFieldValue('process_card_shipment_quantity'))
    setScannedLineOverrides((previous) => {
      const next = { ...previous }
      scannedCards.forEach((item) => {
        next[item.cardNo] = {
          unit_weight_g: next[item.cardNo]?.unit_weight_g ?? commonUnit,
          single_batch_net_weight_kg: next[item.cardNo]?.single_batch_net_weight_kg ?? commonWeight,
          process_card_shipment_quantity: next[item.cardNo]?.process_card_shipment_quantity ?? commonQuantity,
          product_batch_count: 1,
        }
      })
      return next
    })
  }

  const removeScannedCard = (cardNo: string) => {
    setScannedCards((previous) => {
      const next = previous.filter((item) => item.cardNo !== cardNo)
      if (weightEntryMode === 'same') {
        const current = numeric(form.getFieldValue('product_batch_count')) || 1
        if (current === previous.length) {
          const nextCount = Math.max(1, next.length)
          form.setFieldsValue({ product_batch_count: nextCount, batch_count: nextCount })
        }
      }
      return next
    })
    setScannedLineOverrides((previous) => {
      const next = { ...previous }
      delete next[cardNo]
      return next
    })
  }

  const clearScannedCards = () => {
    setScannedCards([])
    setScannedLineOverrides({})
    setWeightEntryMode('same')
    form.setFieldsValue({ product_batch_count: 1, batch_count: 1 })
  }

  const setEqualBatchCount = (value: number | null) => {
    const requested = numeric(value) || 1
    const next = Math.max(requested, scannedCards.length || 1)
    form.setFieldsValue({ product_batch_count: next, batch_count: next })
    if (requested < scannedCards.length) {
      message.info(`本次已扫 ${scannedCards.length} 张流程卡，一张卡对应一包，批数不能少于 ${scannedCards.length}。`)
    }
  }

  const handleShipmentCardScan = async (cardNo: string) => {
    const entrySession = entrySessionRef.current
    let lookup: QualityProcessCardScanResult
    try {
      lookup = await qualityWorkflowApi.scanProcessCard(cardNo)
    } catch (error) {
      if (/404|未找到|不存在/.test((error as Error).message || '')) lookup = { code: cardNo }
      else throw error
    }
    if (entrySession !== entrySessionRef.current || !open) return false
    const scanned = lookup.scanned_card
    const activeCard = lookup.active_card || scanned
    if (scanned?.replaced_by_id || (scanned && activeCard && String(scanned.id) !== String(activeCard.id))) {
      throw new Error(`旧流程卡 ${cardNo} 已作废，请扫描替代卡 ${activeCard?.card_no || '（见补卡记录）'}。`)
    }
    const lookupReturn = activeCard?.current_return || lookup.current_return
    const waitingReturn = lookupReturn && !['RESHIPPED', 'SCRAPPED', 'CANCELLED'].includes(lookupReturn.status)
      ? lookupReturn
      : undefined
    if (waitingReturn) {
      if (scannedCards.length) throw new Error('已扫描普通出货流程卡，请完成或清空后再处理返工重新出货。')
      const binding = activeCard?.unit_binding || activeCard?.binding || lookup.binding || waitingReturn.binding
      const source = waitingReturn.source
      const linkedOrder = allKnownOrders.find((order) => order.id === (binding?.order_id || activeCard?.order_id))
      setReshipCase(waitingReturn)
      form.setFieldsValue({
        order_id: linkedOrder?.id || binding?.order_id || activeCard?.order_id,
        product_name: binding?.product_name || source?.product_name || activeCard?.product_name_snapshot || '',
        specification: binding?.specification || source?.specification || activeCard?.specification_snapshot || '',
        specification_snapshot: binding?.specification || source?.specification || activeCard?.specification_snapshot || '',
        material: binding?.material || source?.material || activeCard?.material_snapshot || '',
        material_snapshot: binding?.material || source?.material || activeCard?.material_snapshot || '',
        unit_weight_g: numeric(activeCard?.unit_weight_g),
        single_batch_net_weight_kg: numeric(binding?.net_weight_kg ?? source?.single_batch_net_weight_kg),
        product_batch_count: 1,
        batch_count: 1,
        process_card_shipment_quantity: numeric(binding?.piece_quantity ?? source?.pieces_per_batch),
      })
      setScannerOpen(false)
      message.warning(`${activeCard?.card_no || cardNo} 是${waitingReturn.return_label || `第${waitingReturn.return_round || 1}次退货返工`}产品，已带出上次数量和重量；核对或修改后确认重新出货。`)
      return true
    }
    if (reshipCase) throw new Error('当前正在处理返工品重新出货，请先完成或关闭后再扫描普通出货。')
    const existingBinding = activeCard?.unit_binding || activeCard?.binding || lookup.binding
    if (existingBinding) {
      throw new Error(`流程卡 ${activeCard?.card_no || cardNo} 已绑定出货单 ${existingBinding.shipment_no || existingBinding.shipment_batch_id} 第 ${existingBinding.shipment_unit_no} 包，不能重复出货；如为退货返工，请先在退货端登记。`)
    }
    const normalizedCardNo = activeCard?.card_no || cardNo
    const scannedOrder = activeCard?.order || allKnownOrders.find((order) => order.id === activeCard?.order_id)
    if (String(scannedOrder?.status || '').toUpperCase() === 'CANCELLED') {
      throw new Error(`流程卡 ${normalizedCardNo} 所属订单已取消，不能登记出货。`)
    }
    const firstScannedCard = scannedCards[0]?.lookup.active_card || scannedCards[0]?.lookup.scanned_card
    const firstScannedOrder = firstScannedCard?.order || allKnownOrders.find((order) => order.id === firstScannedCard?.order_id)
    const firstOrderId = numeric(firstScannedCard?.order_id) ?? numeric(orderId)
    const scannedOrderId = numeric(activeCard?.order_id)
    if (scannedCards.length && firstOrderId != null && scannedOrderId != null && firstOrderId !== scannedOrderId) {
      const firstSpecification = text(firstScannedCard?.specification_snapshot || firstScannedOrder?.specification).toLocaleLowerCase()
      const firstMaterial = text(firstScannedCard?.material_snapshot || firstScannedOrder?.material).toLocaleLowerCase()
      const scannedSpecification = text(activeCard?.specification_snapshot || scannedOrder?.specification).toLocaleLowerCase()
      const scannedMaterial = text(activeCard?.material_snapshot || scannedOrder?.material).toLocaleLowerCase()
      const sameProduct = Boolean(
        firstSpecification
        && firstMaterial
        && firstSpecification === scannedSpecification
        && firstMaterial === scannedMaterial
      )
      if (!sameProduct) {
        throw new Error(`流程卡 ${normalizedCardNo} 属于另一规格或材质，不能和本次已扫包装一起出货。请分开登记。`)
      }
      message.info(`流程卡 ${normalizedCardNo} 属于另一张相同规格材质订单，已加入本次出货；确认时会按包装顺序自动分配。`)
    }
    if (!scannedCards.length && activeCard) {
      const scannedSpec = activeCard.specification_snapshot || scannedOrder?.specification || ''
      const scannedMaterial = activeCard.material_snapshot || scannedOrder?.material || ''
      const scannedUnit = numeric(activeCard.unit_weight_g)
      form.setFieldsValue({
        order_id: scannedOrder?.id || activeCard.order_id,
        product_name: activeCard.product_name_snapshot || scannedOrder?.product_name || '',
        specification: scannedSpec,
        specification_snapshot: scannedSpec,
        material: scannedMaterial,
        material_snapshot: scannedMaterial,
        product_specification_id: scannedOrder?.product_specification_id ?? null,
        unit_weight_g: scannedUnit ?? form.getFieldValue('unit_weight_g'),
        process_card_shipment_quantity: processCardStandardQuantity(activeCard) ?? form.getFieldValue('process_card_shipment_quantity'),
      })
    }
    const commonUnit = numeric(form.getFieldValue('unit_weight_g'))
    const commonWeight = numeric(form.getFieldValue('single_batch_net_weight_kg'))
    const commonQuantity = numeric(form.getFieldValue('process_card_shipment_quantity'))
    const scannedUnit = numeric(activeCard?.unit_weight_g)
    const scannedQuantity = processCardStandardQuantity(activeCard)
    const needsIndividualWeights = Boolean(
      weightEntryMode === 'same'
      && scannedCards.length
      && (
        (scannedUnit != null && commonUnit != null && scannedUnit !== commonUnit)
        || (scannedQuantity != null && commonQuantity != null && scannedQuantity !== commonQuantity)
      )
    )
    if (needsIndividualWeights) {
      // A tail card or a card with another saved unit weight cannot safely use
      // one shared 10% limit. Preserve the fast scan flow, but switch to one
      // editable row per physical package automatically.
      setWeightEntryMode('individual')
      setScannedLineOverrides((previous) => {
        const next = { ...previous }
        scannedCards.forEach((item) => {
          const card = item.lookup.active_card || item.lookup.scanned_card
          next[item.cardNo] = {
            unit_weight_g: next[item.cardNo]?.unit_weight_g ?? numeric(card?.unit_weight_g) ?? commonUnit,
            single_batch_net_weight_kg: next[item.cardNo]?.single_batch_net_weight_kg ?? commonWeight,
            process_card_shipment_quantity: next[item.cardNo]?.process_card_shipment_quantity
              ?? processCardStandardQuantity(card)
              ?? commonQuantity,
            product_batch_count: 1,
          }
        })
        next[normalizedCardNo] = {
          unit_weight_g: scannedUnit ?? commonUnit,
          single_batch_net_weight_kg: commonWeight,
          process_card_shipment_quantity: scannedQuantity ?? commonQuantity,
          product_batch_count: 1,
        }
        return next
      })
      message.warning('检测到流程卡标准数量或产品单重不同，已自动切换为“逐包填写不同重量”，避免套用错误的10%上限。')
    } else if (weightEntryMode === 'individual') {
      setScannedLineOverrides((previous) => ({
        ...previous,
        [normalizedCardNo]: {
          unit_weight_g: scannedUnit ?? commonUnit,
          single_batch_net_weight_kg: commonWeight,
          process_card_shipment_quantity: scannedQuantity ?? commonQuantity,
          product_batch_count: 1,
        },
      }))
    }
    setScannedCards((items) => [...items, { cardNo: normalizedCardNo, lookup }])
    return true
  }

  const submit = async () => {
    if (submittingRef.current) return
    let values: DrawerValues
    try {
      values = await form.validateFields()
    } catch {
      // Ant Design has already rendered the field-level validation message.
      return
    }
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
    if (!isLineMode && !reshipCase && !selectedOrder) {
      message.warning('请选择候选订单；无订单关联的手工出货不能确认。')
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
    if (!isLineMode && scannedCards.length > topBatchCount) {
      message.error(`已扫描 ${scannedCards.length} 张流程卡，但相同称重批数只有 ${topBatchCount} 批。请增加批数或移除多余卡号。`)
      return
    }
    if (!isLineMode && (!topUnit || !topSingleBatchWeight || !topPieces || !topProcessCardQuantity)) {
      message.warning('请填写成品单重、单批实称净重和流程卡出货数量，系统才能计算本次总出货数。')
      return
    }
    if (isLineMode && lineMetrics.missing) {
      message.warning('请为每条流程卡填写件数、产品单重和实称净重。')
      return
    }
    if (isLineMode && activeLines.some((line) => line.process_card_id == null && line.order_id == null)) {
      message.warning('存在尚未关联订单的流程卡，请先选择候选订单后再确认。')
      return
    }
    if (overLimit) {
      message.error('存在超过理论重量上限 10% 的明细，提交已被阻止。')
      return
    }
    const inspectorSelection = Array.from(new Set((values.inspector_ids || []).map(Number).filter((id) => Number.isFinite(id))))
    const snapshotSpec = text(values.specification_snapshot || values.specification)
    const snapshotMaterial = text(values.material_snapshot || values.material)
    const directOrderId = numeric(values.order_id)
    const payloadOrderIds = isLineMode
      ? Array.from(new Set(activeLines.map((line) => line.order_id).filter((value): value is number => value != null).map(Number)))
      : directOrderId == null ? [] : [directOrderId]
    const payload: QualityShipmentBatchInput = {
      shipment_no: shipmentNo || undefined,
      shipment_date: values.shipment_date?.format('YYYY-MM-DD') || null,
      order_id: scannedVariableMode ? null : directOrderId,
      order_ids: payloadOrderIds,
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
      process_card_bindings: basketLineMode ? undefined : scannedCards.map((item, index) => ({
        card_no: item.cardNo,
        shipment_unit_no: index + 1,
        order_id: numeric((item.lookup.active_card || item.lookup.scanned_card)?.order_id),
      })),
      lines: isLineMode
        ? activeLines.map((line) => {
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
            specification_snapshot: text(line.specification_snapshot) || snapshotSpec,
            material_snapshot: text(line.material_snapshot) || snapshotMaterial,
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
    // Re-check after the asynchronous validation/duplicate calls. Two very
    // fast taps must not create two confirmed shipments with different
    // generated numbers.
    if (submittingRef.current) return
    const submitSession = entrySessionRef.current
    submittingRef.current = true
    setSaving(true)
    try {
      let result: unknown
      if (reshipCase) {
        result = await qualityWorkflowApi.reshipReworkCase(reshipCase.id, {
          shipment_date: values.shipment_date?.format('YYYY-MM-DD') || null,
          net_weight_kg: topTotal,
          piece_quantity: topPieces,
          inspector_ids: inspectorSelection,
          notes: text(values.notes),
        })
      } else if (editingDraft && activeBatch?.id) {
        // A duplicate number points at an unfinished server-side draft.  Edit
        // that row in place and confirm it; creating a second row would leave
        // a unique-number conflict and could double count on retry.
        const draftPayload = { ...payload }
        delete draftPayload.process_card_bindings
        await qualityWorkflowApi.updateShipmentBatch(activeBatch.id, draftPayload as unknown as Record<string, unknown>)
        result = await qualityWorkflowApi.confirmShipmentBatch(activeBatch.id, payload.process_card_bindings || [])
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
      try {
        await onSaved?.(result)
      } catch (refreshError) {
        // Persistence already succeeded. A failed list refresh must not leave
        // a confirmed shipment in the form where a second tap can retry it.
        message.warning((refreshError as Error).message || '出货已保存，但列表刷新失败；关闭后刷新页面即可查看。')
      }
      const allocationSummary = shipmentAllocationSummary(result)
      const previewOverflow = allocationPreviews.reduce((sum, item) => sum + (item.preview?.overflow_quantity || 0), 0)
      const overflowQuantity = allocationSummary.hasAllocations
        ? allocationSummary.overflowQuantity
        : previewOverflow
      message.success(reshipCase
        ? `${reshipCase.return_label || '返工产品'}已重新出货，退货状态和订单有效出货数量已同步更新`
        : shipment || batch
        ? '出货记录已更新'
        : allocationSummary.orderCount > 1
          ? `重量出货已保存，实际计入 ${allocationSummary.orderCount} 个订单`
          : overflowQuantity > 0
            ? `重量出货已保存；无可补订单的 ${qualityNumber(overflowQuantity)} 件已记为来源订单超额`
            : '重量出货已保存，订单余量已同步更新')
      if (submitSession === entrySessionRef.current) closeDrawer()
    } catch (error) {
      message.error((error as Error).message || '重量出货提交失败')
    } finally {
      if (submitSession === entrySessionRef.current) {
        submittingRef.current = false
        setSaving(false)
      }
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
          lines: activeLines,
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
    const draftLines = activeLines
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
      if (draftLines.length < activeLines.length) {
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
  const selectedOrderIsIdentityAnchor = Boolean(selectedOrder && !availableOrders.some((order) => order.id === selectedOrder.id))
  const selectableOrders = selectedOrder && selectedOrderIsIdentityAnchor
    ? [selectedOrder, ...availableOrders]
    : availableOrders
  const orderOptions = [
    ...selectableOrders.map((order) => ({
      value: order.id,
      label: `${orderLabel(order)} · 剩余${qualityNumber(orderRemaining(order))}件${selectedOrderIsIdentityAnchor && order.id === selectedOrder?.id ? ' · 来源订单（身份锚点）' : ''}`,
    })),
  ]

  const allocationPreviewCard = allocationPreviewGroups.length ? <Card
    size="small"
    className="quality-weight-allocation-card"
    title="订单自动分配预览"
    extra={<Tag color="blue">确认时按最新余量复核</Tag>}
  >
    {isLineMode && <Alert
      type="info"
      showIcon
      message="按重量明细顺序跨订单分配"
      description="流程卡上的唯一订单作为来源身份锚点；系统按规格、材质分组汇总预览，确认时再逐条包装分配到仍未完成的匹配订单。"
    />}
    {loadingAllocationPreview && <Typography.Text type="secondary">正在根据相同规格、相同材质订单计算分配…</Typography.Text>}
    {!loadingAllocationPreview && !allocationPreviews.length && <Typography.Text type="secondary">正在准备分配预览…</Typography.Text>}
    {!loadingAllocationPreview && allocationPreviews.map((item) => {
      const preview = item.preview
      return <div className="quality-weight-allocation-group" key={item.key}>
        {(isLineMode || allocationPreviews.length > 1) && <Typography.Paragraph strong>
          {item.specification || '未填写规格'} · {item.material || '未填写材质'} · {item.lineCount} 条明细，共 {qualityNumber(item.pieceQuantity)} 件
        </Typography.Paragraph>}
        {item.error && <Alert type="warning" showIcon message="暂时无法显示分配预览" description={`${item.error} 可以继续确认，服务器会在入账时按每条包装重新计算。`} />}
        {preview && <>
          <Alert
            showIcon
            type={preview.overflow_quantity > 0 ? 'warning' : 'success'}
            message={preview.matching_allocated_quantity > 0
              ? `超出部分已预分配 ${qualityNumber(preview.matching_allocated_quantity)} 件到其他匹配订单`
              : preview.overflow_quantity > 0
                ? '没有可补足的匹配订单，允许来源订单超额出货'
                : '本次出货全部计入来源订单'}
            description={preview.overflow_quantity > 0
              ? `匹配订单补足后仍有 ${qualityNumber(preview.overflow_quantity)} 件，将作为来源订单超额数量记录。`
              : `本组共 ${qualityNumber(preview.total_allocated_quantity)} 件；确认时会按最新订单余量重新计算。`}
          />
          <div className="quality-weight-allocation-list">
            {preview.allocations.map((allocation, index) => <div
              className={`quality-weight-allocation-row${allocation.is_overflow ? ' is-overflow' : ''}`}
              key={`${item.key}-${allocation.order_id}-${allocation.is_overflow ? 'overflow' : 'normal'}-${index}`}
            >
              <div className="quality-weight-allocation-order">
                <Space size={6} wrap>
                  <strong>{allocation.order_no} / {allocation.item_no || '-'}</strong>
                  <Tag color={allocation.is_overflow ? 'orange' : allocation.is_source ? 'blue' : 'green'}>
                    {allocation.is_overflow ? '来源订单超额' : allocation.is_source ? '来源订单' : '自动补入'}
                  </Tag>
                </Space>
                <Typography.Text type="secondary">{allocation.due_date ? `交期 ${allocation.due_date}` : '未填写交期'} · 分配前剩余 {qualityNumber(allocation.remaining_before)} 件 → 分配后 {qualityNumber(allocation.remaining_after)} 件</Typography.Text>
              </div>
              <strong className="quality-weight-allocation-quantity">{qualityNumber(allocation.allocated_quantity)} 件</strong>
            </div>)}
          </div>
        </>}
      </div>
    })}
  </Card> : null

  return (
    <Drawer
      open={open}
      onClose={closeDrawer}
      size={760}
      title={shipment ? `编辑重量出货 · ${shipment.shipment_no}` : activeBatch ? `${activeBatch.status === 'DRAFT' ? '继续填写草稿' : '编辑重量出货'} · ${activeBatch.shipment_no}` : '新增重量出货'}
      className="quality-weight-shipment-drawer"
      footer={<Space className="drawer-footer-actions"><Button onClick={saveDraft}>保存草稿</Button><Button onClick={closeDrawer}>取消</Button><Button type="primary" loading={saving} disabled={duplicate || checkingNumber || Boolean(draftMatch && !activeBatch)} onClick={() => void submit()}>确认出货</Button></Space>}
    >
      <Alert
        className="quality-form-alert"
        type="info"
        showIcon
        message="新出货统一按成品重量登记"
        description="连续扫码时一张流程卡对应一包：重量相同可只填一次，扫码张数自动成为批数；重量不同可切换为逐包填写。每包换算件数超过对应流程卡标准数量 10% 会阻止提交。"
      />
      {!basketLineMode && <Card size="small" className="quality-weight-scan-card" title="流程卡扫码（选填）" extra={<Button type="primary" icon={<QrcodeOutlined />} onClick={() => setScannerOpen(true)}>连续扫码</Button>}>
        {reshipCase ? <Alert
          type="warning"
          showIcon
          message={`${reshipCase.return_label || `第 ${reshipCase.return_round || 1} 次退货返工`}产品重新出货`}
          description={`流程卡 ${reshipCase.active_process_card_no || reshipCase.process_card_no || reshipCase.process_card?.card_no || '已识别'}；已带出上一次的数量和重量，现场有变化可以直接修改。确认后自动变为“已重新出货”。`}
          action={<Button size="small" onClick={() => setReshipCase(undefined)}>取消返工出货</Button>}
        /> : scannedCards.length ? <>
          <div className="quality-weight-scanned-cards">{scannedCards.map((item, index) => <Tag key={item.cardNo} closable onClose={() => removeScannedCard(item.cardNo)}>{index + 1}. {item.cardNo}</Tag>)}</div>
          <Space wrap className="quality-weight-scan-mode">
            <Radio.Group
              value={weightEntryMode}
              optionType="button"
              buttonStyle="solid"
              onChange={(event) => changeWeightEntryMode(event.target.value as WeightEntryMode)}
              options={[
                { value: 'same', label: '这些包重量相同' },
                { value: 'individual', label: '逐包填写不同重量' },
              ]}
            />
            <Button danger type="link" onClick={clearScannedCards}>清空本次扫码</Button>
          </Space>
          {weightEntryMode === 'same'
            ? <Typography.Text type="secondary">已扫 {scannedCards.length} 张卡＝{scannedCards.length} 包；共用一次单重和单批净重，批数已自动设为至少 {scannedCards.length} 批。当前共 {effectiveBatchCount} 批，另有 {unscannedBatchCount} 批未扫码。</Typography.Text>
            : <Alert type="info" showIcon message={`已扫 ${scannedCards.length} 张卡＝${scannedCards.length} 包`} description="每张卡会生成一条独立重量明细；切换时已用当前公共单重、净重和流程卡数量预填，可只修改重量不同的包。" />}
          {weightEntryMode === 'same' && scannedCards.length > effectiveBatchCount && <Alert type="error" showIcon message="扫码张数超过相同称重批数" description="请增加批数或移除多余流程卡后再确认。" />}
        </> : <Typography.Text type="secondary">正常出货不强制逐张扫码；愿意扫码时可连续扫任意部分，其余批次仍按“卡号未录入”正常出货。退货时再扫码即可首次绑定。</Typography.Text>}
      </Card>}
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
          <Form.Item
            name="order_id"
            label={isLineMode || scannedCards.length ? '来源订单（由流程卡确定）' : '候选订单 / 批次'}
            rules={!isLineMode && !reshipCase ? [{ required: true, message: '请选择候选订单；无订单出货不能确认' }] : undefined}
            extra={isLineMode || scannedCards.length
              ? '流程卡订单作为来源身份锚点；即使该订单已出满，确认时也会继续顺延到相同规格、相同材质的未完成订单。'
              : '已出满订单不会显示；本次超出当前订单余量时，系统会自动补入相同规格、相同材质的未完成订单。'}
          >
            <Select
              showSearch
              allowClear
              disabled={basketLineMode || Boolean(scannedCards.length && selectedOrder)}
              loading={loadingCandidates}
              optionFilterProp="label"
              placeholder={basketLineMode ? '由各流程卡明细确定' : '必须选择候选订单'}
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
          {candidateError && <Alert className="quality-weight-candidate-alert" type="warning" showIcon message="候选订单暂时读取失败" description={isLineMode || selectedOrder
            ? `${candidateError} 确认出货时服务器仍会按最新订单余量重新核算。`
            : `${candidateError} 请恢复候选订单读取或扫描带订单的流程卡后再确认出货。`} />}
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
              <Col xs={12} sm={6}><Form.Item name="product_batch_count" label="相同称重批数"><InputNumber min={Math.max(1, scannedCards.length)} precision={0} style={{ width: '100%' }} placeholder="默认1批" onChange={setEqualBatchCount} /></Form.Item></Col>
            </Row>
            <div className="quality-weight-quick-batches" aria-label="批数快捷计算">
              <Typography.Text type="secondary">批数快捷计算：</Typography.Text>
              {[1, 5, 10].map((value) => <Button key={value} size="small" onClick={() => setEqualBatchCount(value)}>{value} 批</Button>)}
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
          </> : <>
            <Alert type="info" showIcon message={`已选择 ${activeLines.length} 张流程卡`} description={scannedVariableMode
              ? '一卡对应一包；可逐包调整单重、流程卡数量和实称净重。流程卡原订单仅作身份锚点，确认时会把超出件数顺延到相同规格、相同材质的未完成订单。'
              : '每条明细可调整本次件数和实称净重；确认时按明细顺序跨匹配订单分配，并分别校验每张流程卡的 +10% 上限。'} />
            <div className="quality-weight-line-list">
              {activeLines.map((line, index) => {
                const order = lineOrder(line, allKnownOrders)
                const metrics = editableLineMetrics(line)
                const variance = weightVariancePercent(metrics.actual, metrics.expected)
                return <Card key={line.key} size="small" className={`quality-weight-line ${metrics.over ? 'is-over' : ''}`}>
                  <div className="quality-weight-line-heading"><div><strong>{line.card_no || `流程卡 ${line.process_card_id}`}</strong><Typography.Text type="secondary">{order ? ` · ${orderLabel(order)}` : ''}</Typography.Text></div>{metrics.over ? <Tag color="error">超上限</Tag> : <Tag color="success">可提交</Tag>}</div>
                  <Row gutter={10}>
                    <Col xs={12} sm={6}><Form.Item label="流程卡出货数量"><InputNumber min={1} max={numeric(line.remaining_quantity) || undefined} precision={0} value={line.process_card_shipment_quantity ?? undefined} onChange={(value) => updateActiveLine(index, { process_card_shipment_quantity: numeric(value) })} style={{ width: '100%' }} /></Form.Item></Col>
                    <Col xs={12} sm={6}><Form.Item label="单重(g)"><InputNumber min={0.00001} precision={5} value={line.unit_weight_g ?? undefined} onChange={(value) => updateActiveLine(index, { unit_weight_g: numeric(value) })} style={{ width: '100%' }} /></Form.Item></Col>
                    <Col xs={12} sm={6}><Form.Item label="单批实称净重(kg)"><InputNumber min={0.001} precision={3} value={line.single_batch_net_weight_kg ?? undefined} onChange={(value) => updateActiveLine(index, { single_batch_net_weight_kg: numeric(value) })} style={{ width: '100%' }} /></Form.Item></Col>
                    <Col xs={12} sm={6}>{scannedVariableMode ? <Form.Item label="物理包装"><Input value="1张卡 = 1包" disabled /></Form.Item> : <Form.Item label="相同称重批数"><InputNumber min={1} precision={0} value={line.product_batch_count} onChange={(value) => updateActiveLine(index, { product_batch_count: numeric(value) || 1 })} style={{ width: '100%' }} /></Form.Item>}</Col>
                  </Row>
                  <Typography.Text type="secondary">单批换算 {metrics.singlePieces == null ? '-' : qualityNumber(metrics.singlePieces, Number.isInteger(metrics.singlePieces) ? 0 : 2)} 件（允许上限 {metrics.quantityUpper ?? '-'} 件） · 最终 {metrics.quantity || '-'} 件 / {metrics.actual == null ? '-' : metrics.actual.toFixed(3)} kg · 偏差 {variance == null ? '-' : `${variance >= 0 ? '+' : ''}${variance.toFixed(2)}%`}</Typography.Text>
                </Card>
              })}
            </div>
          </>}
          {allocationPreviewCard}
          {(overLimit || underLimit) && <Alert className="quality-weight-limit-alert" type={overLimit ? 'error' : 'warning'} showIcon message={overLimit ? '超过理论重量 +10%，禁止提交' : '实称净重低于理论重量，请核对后确认'} />}
        </Card>
        <Form.Item name="backfill_reason" label="历史日期补录原因" extra="早于今天的出货日期建议填写原因"><Input.TextArea rows={2} maxLength={300} showCount /></Form.Item>
        <Form.Item name="notes" label="备注"><Input.TextArea rows={3} maxLength={500} showCount placeholder="可记录车次、包装、交接或称重说明" /></Form.Item>
      </Form>
      {!isLineMode && candidateLoaded && !availableOrders.length && !selectedOrder && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无仍可出货的候选订单；无订单关联的手工出货不能确认" />}
      <div className="quality-weight-summary">
        <Statistic title={isLineMode ? '流程卡' : '订单'} value={isLineMode ? activeLines.length : selectedOrder ? 1 : 0} suffix={isLineMode ? '张' : '项'} />
        <Statistic title="本次件数" value={isLineMode ? lineMetrics.quantity : calculatedPieces || 0} suffix="件" />
        <Statistic title="理论重量" value={isLineMode ? lineMetrics.expected : totalExpected} precision={3} suffix="kg" />
        <Statistic title="实称净重" value={isLineMode ? lineMetrics.actual : totalActual} precision={3} suffix="kg" />
      </div>
      <QualityQrScanner
        open={open && scannerOpen}
        title="扫描出货流程卡（选填）"
        description="可连续扫描本次出货中的部分或全部流程卡；未扫描的批次仍允许出货。扫到待返工卡时会自动切换为返工重新出货。"
        initialValues={scannedCards.map((item) => item.cardNo)}
        onClose={() => setScannerOpen(false)}
        onScan={handleShipmentCardScan}
      />
    </Drawer>
  )
}
