import dayjs, { type Dayjs } from 'dayjs'
import type { ProductionReminderStatus, ProductionRunStatus, ProductionStation, ProductionStationGroup } from './types'

type MonthlyPerformanceTotals = {
  production_days: number
  operator_day_count?: number
}

type ProductionSettlementDraft = {
  is_settled?: boolean
  actual_good_quantity?: number | null
  actual_defective_quantity?: number | null
  total_material_kg?: number | string | null
  labor_cost?: number | string | null
  energy_cost?: number | string | null
  other_cost?: number | string | null
  settlement_notes?: string
}

type ProductionSettlementRiskInput = {
  unitPrice?: number | string | null
  materialUnitPrice?: number | string | null
  totalMaterialKg?: number | string | null
  laborCost?: number | string | null
  energyCost?: number | string | null
  otherCost?: number | string | null
}

type ReminderRun = {
  id: number
  expected_change_at?: string | null
}

export function formatProductionDate(
  value: string | null | undefined,
  format = 'YYYY-MM-DD HH:mm',
  fallback = '-',
) {
  if (!value) return fallback
  const parsed = dayjs(value)
  return parsed.isValid() ? parsed.format(format) : fallback
}

export function productionStationNumber(station: Pick<ProductionStation, 'code' | 'position_no'>) {
  const code = station.code?.trim()
  return code || String(station.position_no)
}

export function productionStationGroupLabel(group: ProductionStationGroup) {
  const code = String(group || '').trim()
  return ({ A: '一组', B: '二组', C: '三组' } as Record<string, string>)[code] || (code ? `${code}组` : '未分组')
}

export function productionReminderKey(run: ReminderRun, status: ProductionReminderStatus) {
  return `${run.id}-${status}-${run.expected_change_at || 'unscheduled'}`
}

export function isKeyboardActivationKey(key: string) {
  return key === 'Enter' || key === ' '
}

export function requiresProductionUnloadTime(status: ProductionRunStatus, hasLoadedAt: boolean) {
  return status === 'COMPLETED' || (status === 'CANCELLED' && hasLoadedAt)
}

export function calculateGoodQuantity(
  producedMoldCount: number,
  cavities: number,
  defectiveQuantity: number,
) {
  if (![producedMoldCount, cavities, defectiveQuantity].every(Number.isFinite)) return 0
  return Math.max(producedMoldCount * cavities - defectiveQuantity, 0)
}

export function canSettleProductionRun(status: ProductionRunStatus, hasLoadedAt: boolean) {
  return status === 'COMPLETED' || (status === 'CANCELLED' && hasLoadedAt)
}

export function canCreateProductionDailyLog(status: ProductionRunStatus, hasLoadedAt: boolean) {
  return hasLoadedAt && (status === 'RUNNING' || status === 'COMPLETED')
}

export function defaultProductionLogDate(
  loadedAt: string | null | undefined,
  unloadedAt: string | null | undefined,
  today: string | Dayjs = dayjs(),
) {
  if (!loadedAt) return undefined
  const start = dayjs(loadedAt).startOf('day')
  const current = dayjs(today).startOf('day')
  const end = unloadedAt ? dayjs(unloadedAt).startOf('day') : current
  if (![start, current, end].every((item) => item.isValid())) return undefined
  const latestAllowed = current.isBefore(end) ? current : end
  return latestAllowed.isBefore(start) ? start : latestAllowed
}

export function settlementInitialGoodQuantity(
  isSettled: boolean | undefined,
  actualGoodQuantity: number | null | undefined,
  expectedQuantity: number,
) {
  return isSettled ? Number(actualGoodQuantity || 0) : expectedQuantity
}

export function hasProductionSettlementDraft(run: ProductionSettlementDraft) {
  const numericValues = [
    run.actual_good_quantity,
    run.actual_defective_quantity,
    run.total_material_kg,
    run.labor_cost,
    run.energy_cost,
    run.other_cost,
  ]
  return !!run.is_settled
    || numericValues.some((value) => Number.isFinite(Number(value)) && Number(value) !== 0)
    || !!run.settlement_notes?.trim()
}

export function productionSettlementInitialValues(run: ProductionSettlementDraft, expectedQuantity: number) {
  const hasDraft = hasProductionSettlementDraft(run)
  return {
    actual_good_quantity: hasDraft ? Number(run.actual_good_quantity || 0) : expectedQuantity,
    actual_defective_quantity: hasDraft ? Number(run.actual_defective_quantity || 0) : 0,
    total_material_kg: hasDraft ? Number(run.total_material_kg || 0) : 0,
    labor_cost: hasDraft ? Number(run.labor_cost || 0) : 0,
    energy_cost: hasDraft ? Number(run.energy_cost || 0) : 0,
    other_cost: hasDraft ? Number(run.other_cost || 0) : 0,
    settlement_notes: hasDraft ? run.settlement_notes || '' : '',
  }
}

export function productionSettlementRiskReasons(input: ProductionSettlementRiskInput) {
  const reasons: string[] = []
  const unitPrice = Number(input.unitPrice || 0)
  const materialUnitPrice = Number(input.materialUnitPrice || 0)
  const totalMaterialKg = Number(input.totalMaterialKg || 0)
  const costs = [input.laborCost, input.energyCost, input.otherCost].map((value) => Number(value || 0))
  if (!Number.isFinite(unitPrice) || unitPrice <= 0) reasons.push('产品单价为0')
  if (totalMaterialKg > 0 && (!Number.isFinite(materialUnitPrice) || materialUnitPrice <= 0)) reasons.push('有材料用量但材料单价为0')
  if (totalMaterialKg === 0 && costs.every((value) => Number.isFinite(value) && value === 0)) reasons.push('材料用量及各项成本全部为0')
  return reasons
}

export function productionOperatorDayCount(totals: MonthlyPerformanceTotals | null | undefined) {
  const parsed = Number(totals?.operator_day_count ?? 0)
  return Number.isFinite(parsed) ? parsed : 0
}

export function settlementExpectedQuantity(producedMoldCount: number, cavities: number) {
  if (![producedMoldCount, cavities].every(Number.isFinite)) return 0
  return Math.max(producedMoldCount * cavities, 0)
}

export function settlementQuantityMatches(
  producedMoldCount: number,
  cavities: number,
  goodQuantity: number,
  defectiveQuantity: number,
) {
  if (![goodQuantity, defectiveQuantity].every(Number.isFinite)) return false
  return goodQuantity + defectiveQuantity === settlementExpectedQuantity(producedMoldCount, cavities)
}

export function isProductionLogDateAllowed(
  value: string | Dayjs | null | undefined,
  loadedAt: string | null | undefined,
  unloadedAt: string | null | undefined,
  today: string | Dayjs = dayjs(),
) {
  if (!value || !loadedAt) return false
  const selected = dayjs(value).startOf('day')
  const start = dayjs(loadedAt).startOf('day')
  const end = dayjs(unloadedAt || today).startOf('day')
  if (![selected, start, end].every((item) => item.isValid())) return false
  return !selected.isBefore(start) && !selected.isAfter(end)
}
