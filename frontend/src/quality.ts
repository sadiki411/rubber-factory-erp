import dayjs from 'dayjs'

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

