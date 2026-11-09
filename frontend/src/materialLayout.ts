import type { ProductSpecification, ProductionRun } from './types'

export type MaterialLayoutRecord = Pick<ProductSpecification,
  'large_strip_weight_g' | 'large_strip_count' | 'small_strip_weight_g' | 'small_strip_count'
> & Pick<Partial<ProductionRun>, 'strip_weight_kg' | 'strips_per_batch'>

export function parameterText(value: unknown) {
  return value === null || value === undefined || String(value).trim() === '' ? '未填写' : String(value)
}

export function parameterNumber(value: number | string | null | undefined, unit: string) {
  if (value === null || value === undefined || String(value).trim() === '') return '未填写'
  const number = Number(value)
  return Number.isFinite(number) ? `${number.toLocaleString('zh-CN', { maximumFractionDigits: 2 })} ${unit}` : '数据待核对'
}

function present(value: unknown) {
  return value !== null && value !== undefined && value !== ''
}

function completePair(weight: unknown, count: unknown) {
  return present(weight) && present(count) && Number.isFinite(Number(weight)) && Number(weight) >= 0
    && Number.isInteger(Number(count)) && Number(count) > 0
}

/** Display only: never fills task snapshots from the latest product reference. */
export function materialLayout(record: MaterialLayoutRecord) {
  const explicit = [record.large_strip_weight_g, record.large_strip_count, record.small_strip_weight_g, record.small_strip_count].some(present)
  const hasSmall = [record.small_strip_weight_g, record.small_strip_count].some(present)
  const legacy = !explicit && [record.strip_weight_kg, record.strips_per_batch].some(present)
  let total: number | undefined
  if (explicit && completePair(record.large_strip_weight_g, record.large_strip_count)
    && (!hasSmall || completePair(record.small_strip_weight_g, record.small_strip_count))) {
    // Persisted gram weights have two decimal places. Work in hundredths to
    // avoid floating-point artefacts, including an explicitly saved true zero.
    total = (Math.round(Number(record.large_strip_weight_g) * 100) * Number(record.large_strip_count)
      + (hasSmall ? Math.round(Number(record.small_strip_weight_g) * 100) * Number(record.small_strip_count) : 0)) / 100
  } else if (legacy && completePair(record.strip_weight_kg, record.strips_per_batch)) {
    total = Math.round(Number(record.strip_weight_kg) * 100000) * Number(record.strips_per_batch) / 100
  }
  return { legacy, total, incomplete: (explicit || legacy) && total === undefined }
}
