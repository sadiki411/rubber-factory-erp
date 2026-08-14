import dayjs from 'dayjs'
import type { QualityOrder } from './types'

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

/**
 * Return the product unit weight in grams when it is explicitly present in
 * the product specification payload.  The flow card's material/胶料重量 is
 * deliberately not used here: it is a material issue quantity, not a
 * finished-piece weight.
 */
export function orderUnitWeightG(order: Pick<QualityOrder, 'product_specification'>): number | null {
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
