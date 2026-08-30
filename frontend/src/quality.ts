import dayjs from 'dayjs'
import type { QualityOrder, QualityProcessCard, QualityReworkCase } from './types'

function finiteNonNegative(...values: number[]) {
  return values.every((value) => Number.isFinite(value) && value >= 0)
}

export function shipmentQuantitiesMatch(
  inspectionQuantity: number,
  qualifiedQuantity: number,
  defectiveQuantity: number,
) {
  return finiteNonNegative(inspectionQuantity, qualifiedQuantity, defectiveQuantity)
    && inspectionQuantity === qualifiedQuantity + defectiveQuantity
}

export function shipmentQuantityAllowed(shippedQuantity: number, qualifiedQuantity: number) {
  return finiteNonNegative(shippedQuantity, qualifiedQuantity) && shippedQuantity <= qualifiedQuantity
}

export function reworkQuantitiesValid(
  returnedQuantity: number,
  reworkedQuantity: number,
  recoveredQuantity: number,
  scrapQuantity: number,
) {
  return finiteNonNegative(returnedQuantity, reworkedQuantity, recoveredQuantity, scrapQuantity)
    && recoveredQuantity + scrapQuantity <= reworkedQuantity
    && reworkedQuantity <= returnedQuantity
}

export function isHighReworkCount(count: number | string | null | undefined) {
  return Number(count || 0) > 3
}

export function formatQualityDate(value: string | null | undefined, format = 'YYYY-MM-DD', fallback = '-') {
  if (!value) return fallback
  const parsed = dayjs(value)
  return parsed.isValid() ? parsed.format(format) : fallback
}

export function qualityNumber(value: number | string | null | undefined, digits = 0) {
  const parsed = Number(value)
  return Number.isFinite(parsed)
    ? parsed.toLocaleString('zh-CN', { maximumFractionDigits: digits })
    : '-'
}

export function reworkCaseSourceTitle(item: QualityReworkCase) {
  const source = item.source
  if (!source) return item.shipment_batch_id ? `出货批次 #${item.shipment_batch_id}` : '历史来源记录'
  return `${source.shipment_no} · ${source.order_no || '未关联订单'}${source.item_no ? ` / ${source.item_no}` : ''}`
}

export function resolvedProcessCardReworkCount(item: QualityProcessCard, linkedCaseCount: number) {
  return item.rework_count == null ? linkedCaseCount : Number(item.rework_count)
}

/**
 * Normalize the text carried by a customer's process-card QR code.
 *
 * Their current cards encode the card number directly (for example
 * `04-M003-2608210028`).  Keeping this helper deliberately small also makes
 * scans pasted from a clipboard or a scanner gun behave exactly like camera
 * scans.  A surrounding URL is accepted as a safety net for future card
 * formats, but no customer ERP connection is required.
 */
export function normalizeProcessCardQrText(rawValue: string | null | undefined) {
  const raw = String(rawValue || '').trim()
  if (!raw) return ''
  try {
    const url = new URL(raw)
    const queryValue = url.searchParams.get('card_no') || url.searchParams.get('code')
    if (queryValue?.trim()) return queryValue.trim().toUpperCase()
    const lastSegment = decodeURIComponent(url.pathname.split('/').filter(Boolean).at(-1) || '')
    if (lastSegment) return lastSegment.trim().toUpperCase()
  } catch {
    // Plain process-card numbers are the expected and most common format.
  }
  return raw.toUpperCase()
}

export function isLikelyProcessCardNo(value: string | null | undefined) {
  const normalized = normalizeProcessCardQrText(value)
  return /^[A-Z0-9]+(?:-[A-Z0-9]+){2,}$/.test(normalized) && normalized.length >= 12
}

/**
 * Return the product unit weight in grams when it is explicitly present in a
 * shipment candidate or its product specification.  The flow card's
 * material/胶料重量 is deliberately not used here: it is a material issue
 * quantity, not a finished-piece weight.
 */
export function orderUnitWeightG(order: Pick<QualityOrder, 'product_specification' | 'unit_weight_g'>): number | null {
  const candidateValue = Number(order.unit_weight_g)
  if (Number.isFinite(candidateValue) && candidateValue > 0) return candidateValue
  const latest = Number(order.product_specification?.latest_unit_weight_g)
  if (Number.isFinite(latest) && latest > 0) return latest
  const raw = order.product_specification?.raw_data
  const candidates: unknown[] = [
    (raw as Record<string, unknown> | undefined)?.unit_weight_g,
    (raw as Record<string, unknown> | undefined)?.product_unit_weight_g,
    (raw as Record<string, unknown> | undefined)?.finished_unit_weight_g,
    (raw as Record<string, unknown> | undefined)?.['成品单重(g)'],
    (raw as Record<string, unknown> | undefined)?.['产品单重'],
    (raw as Record<string, unknown> | undefined)?.['单重'],
  ]
  for (const candidate of candidates) {
    const parsed = Number(String(candidate ?? '').replace(/[,，]/g, '').trim())
    if (Number.isFinite(parsed) && parsed > 0) return parsed
  }
  return null
}

export function expectedWeightKg(quantity: number | string | null | undefined, unitWeightG: number | string | null | undefined) {
  const qty = Number(quantity)
  const unit = Number(unitWeightG)
  if (!Number.isFinite(qty) || qty <= 0 || !Number.isFinite(unit) || unit <= 0) return null
  return (qty * unit) / 1000
}

/**
 * Derive a piece count from a total net weight and a finished-piece unit
 * weight.  Both values use the units shown in the quality UI (kg and g/pc),
 * so the conversion is intentionally explicit here rather than duplicated in
 * form components.  A null result means that there is not enough information
 * to calculate a count.  Real-world scale readings can have a few decimal
 * places; the nearest whole piece is the least surprising value for an
 * operator while the server remains the source of truth for final validation.
 */
export function piecesFromWeight(
  totalNetWeightKg: number | string | null | undefined,
  unitWeightG: number | string | null | undefined,
) {
  const total = Number(totalNetWeightKg)
  const unit = Number(unitWeightG)
  if (!Number.isFinite(total) || total <= 0 || !Number.isFinite(unit) || unit <= 0) return null
  const pieces = Math.round((total * 1000) / unit)
  return pieces > 0 ? pieces : null
}

/** Return the piece count represented by a number of equal production batches. */
export function piecesFromBatchCount(
  batchCount: number | string | null | undefined,
  piecesPerBatch: number | string | null | undefined,
) {
  const batches = Number(batchCount)
  const pieces = Number(piecesPerBatch)
  if (!Number.isFinite(batches) || batches <= 0 || !Number.isFinite(pieces) || pieces <= 0) return null
  const result = Math.round(batches) * Math.round(pieces)
  return result > 0 ? result : null
}

/** Mirror the server's positive ROUND_HALF_UP per-batch check for legacy totals. */
export function piecesPerBatchFromTotal(
  totalPieces: number | string | null | undefined,
  batchCount: number | string | null | undefined,
) {
  const total = Number(totalPieces)
  const batches = Number(batchCount)
  if (!Number.isFinite(total) || total <= 0 || !Number.isInteger(batches) || batches <= 0) return null
  return Math.round(total / batches)
}

/**
 * Calculate the final shipped quantity for repeated, identical weighings.
 * `totalNetWeightKg` is the scale reading for one batch; an omitted batch
 * count means one batch.  This mirrors the workshop workflow: weigh one
 * identical batch once, then enter how many batches were shipped.
 */
export function shipmentPieceQuantity(values: {
  totalNetWeightKg?: number | string | null
  unitWeightG?: number | string | null
  batchCount?: number | string | null
  piecesPerBatch?: number | string | null
}) {
  const singleBatchPieces = piecesFromWeight(values.totalNetWeightKg, values.unitWeightG)
  if (singleBatchPieces != null) {
    const parsedBatchCount = Number(values.batchCount)
    const batchCount = Number.isInteger(parsedBatchCount) && parsedBatchCount > 0
      ? parsedBatchCount
      : 1
    return singleBatchPieces * batchCount
  }
  return piecesFromBatchCount(values.batchCount, values.piecesPerBatch)
}

/** Normalize a kilogram value to the three-decimal scale used by the API. */
export function normalizeWeightKg(value: number | string | null | undefined) {
  if (value == null || (typeof value === 'string' && value.trim() === '')) return null
  const weight = Number(value)
  if (!Number.isFinite(weight)) return null
  return Number(weight.toFixed(3))
}

/**
 * Expand one repeated scale reading into the accumulated net weight accepted
 * by the shipment API. Scale inputs and persisted weights use three decimal
 * places; normalising here prevents binary floating-point tails such as
 * `10.2 * 34 === 346.79999999999995` from leaking into a DecimalField payload.
 */
export function repeatedBatchNetWeightKg(
  singleBatchNetWeightKg: number | string | null | undefined,
  batchCount: number | string | null | undefined,
) {
  if (singleBatchNetWeightKg == null || (typeof singleBatchNetWeightKg === 'string' && singleBatchNetWeightKg.trim() === '')) return null
  const weight = Number(singleBatchNetWeightKg)
  const parsedBatchCount = Number(batchCount)
  if (!Number.isFinite(weight)) return null
  const repeatCount = Number.isInteger(parsedBatchCount) && parsedBatchCount > 0
    ? parsedBatchCount
    : 1
  return normalizeWeightKg(weight * repeatCount)
}

export function weightUpperLimitKg(expectedKg: number | null | undefined, tolerancePercent = 10) {
  const expected = Number(expectedKg)
  const tolerance = Number(tolerancePercent)
  if (!Number.isFinite(expected) || expected < 0 || !Number.isFinite(tolerance) || tolerance < 0) return null
  return expected * (1 + tolerance / 100)
}

export function weightVariancePercent(actualKg: number | string | null | undefined, expectedKg: number | null | undefined) {
  const actual = Number(actualKg)
  const expected = Number(expectedKg)
  if (!Number.isFinite(actual) || !Number.isFinite(expected) || expected <= 0) return null
  return ((actual - expected) / expected) * 100
}

export function weightWithinUpperLimit(
  actualKg: number | string | null | undefined,
  expectedKg: number | null | undefined,
  tolerancePercent = 10,
) {
  const actual = Number(actualKg)
  const upper = weightUpperLimitKg(expectedKg, tolerancePercent)
  return Number.isFinite(actual) && actual >= 0 && upper !== null && actual <= upper
}

/** Whole-piece upper cap for one flow-card shipment (for example 100 -> 110). */
export function processCardQuantityUpperLimit(
  standardQuantity: number | string | null | undefined,
  tolerancePercent = 10,
) {
  const standard = Number(standardQuantity)
  const tolerance = Number(tolerancePercent)
  if (!Number.isInteger(standard) || standard <= 0 || !Number.isFinite(tolerance) || tolerance < 0) return null
  return Math.floor(standard * (1 + tolerance / 100))
}

export function shipmentQuantityWithinFlowCardLimit(
  singleBatchPieces: number | string | null | undefined,
  standardQuantity: number | string | null | undefined,
  tolerancePercent = 10,
) {
  const actual = Number(singleBatchPieces)
  const upper = processCardQuantityUpperLimit(standardQuantity, tolerancePercent)
  return Number.isInteger(actual) && actual > 0 && upper !== null && actual <= upper
}
