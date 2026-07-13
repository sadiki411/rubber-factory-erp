export type MoldStatus = 'IN_STOCK' | 'ON_MACHINE' | 'OUTSOURCED'

export interface User {
  id: number
  username: string
  display_name?: string
}

export interface SessionResponse {
  authenticated: boolean
  user?: User
}

export interface MoldModel {
  id: number
  code: string
  name?: string
  product_name: string
  description?: string
  active?: boolean
  is_active?: boolean
}

export interface Machine {
  id: number
  code: string
  name: string
  active?: boolean
  is_active?: boolean
  note?: string
}

export interface Processor {
  id: number
  code: string
  name: string
  active?: boolean
  is_active?: boolean
  contact?: string
  phone?: string
  note?: string
}

export interface RackSummary {
  id: number
  code: string
  name: string
  configured?: boolean
  is_configured?: boolean
  locked?: boolean
  structure_locked?: boolean
  level_count?: number
  occupied_count?: number
  active_slot_count?: number
}

export interface RackSlot {
  id: number
  code?: string
  display_code: string
  position_no: number
  stack_level: number
  active: boolean
  is_enabled?: boolean
  available?: boolean
  capacity_mode?: number
  blocking_reason?: string
  mold?: Pick<MoldAsset, 'id' | 'asset_code' | 'status'> & {
    model_code?: string
    product_name?: string
  }
}

export interface RackZone {
  id: number
  code: string
  name: string
  current_capacity: number
  allowed_capacities: number[]
  stack_levels: number
  supports_stacking: boolean
  stacking_enabled: boolean
  is_active: boolean
  blocking_reason?: string
  slots: RackSlot[]
}

export interface RackLevel {
  id: number
  level_no: number
  label?: string
  zones: RackZone[]
}

export interface RackLayout {
  rack: RackSummary
  levels: RackLevel[]
}

export interface SlotLocation {
  id: number
  display_code: string
  rack_code?: string
  level_no?: number
  zone_name?: string
  position_no?: number
  stack_level?: number
}

export interface MoldAsset {
  id: number
  asset_code: string
  code?: string
  mold_model: MoldModel
  model?: MoldModel
  status: MoldStatus
  status_display?: string
  slot?: SlotLocation | null
  machine?: Machine | null
  processor?: Processor | null
  status_changed_at?: string
  image?: string | null
  main_image?: string | null
  note?: string
  notes?: string
  can_stack?: boolean
  allows_stacking?: boolean
  created_at?: string
  updated_at?: string
}

export interface MoldMovement {
  id: number
  action: 'PUTAWAY' | 'MOVE' | 'LOAD_MACHINE' | 'SEND_OUT' | string
  action_display?: string
  from_status?: MoldStatus | null
  to_status: MoldStatus
  from_location?: string | null
  to_location?: string | null
  note?: string
  operator_name?: string
  created_at: string
}

export interface ApiList<T> {
  count?: number
  next?: string | null
  previous?: string | null
  results: T[]
}

export interface ImportIssue {
  level: 'error' | 'warning'
  sheet?: string
  row?: number
  field?: string
  message: string
}

export interface ImportPreviewRow {
  row_no: number
  row_key: string
  asset_code: string
  model_code: string
  product_name?: string
  status: MoldStatus
  location?: string
  valid?: boolean
}

export interface ImportPreview {
  token: string
  file_name: string
  source_type?: 'standard' | 'legacy'
  total_rows: number
  valid_rows: number
  error_count: number
  warning_count: number
  rows: ImportPreviewRow[]
  issues: ImportIssue[]
}

export interface RackConfigInput {
  code: string
  name: string
  level_count: number
  zone_type: 'WHOLE' | 'SPLIT'
  allowed_capacities: number[]
  default_capacity: number
  stack_levels: 1 | 2
  default_stacking_enabled: boolean
}

export type ProductionRunStatus = 'PLANNED' | 'RUNNING' | 'COMPLETED' | 'CANCELLED'
export type ProductionReminderStatus = 'IDLE' | 'PLANNED' | 'NORMAL' | 'DUE_SOON' | 'OVERDUE'
export type ProductionStationGroup = 'A' | 'B' | 'C'
export type ProductionStationPosition = 1 | 2 | 3 | 4 | 5 | 6

export interface ProductionStation {
  id: number
  group: ProductionStationGroup
  position_no: ProductionStationPosition
  code: string
  machine?: Machine | null
  is_active: boolean
}

export interface ProductionDailyLog {
  id: number
  date: string
  operator: string
  produced_mold_count: number
  notes?: string
  created_at?: string
  updated_at?: string
}

export interface ProductionSettlementInput {
  actual_good_quantity: number
  actual_defective_quantity: number
  total_material_kg: number | string
  labor_cost: number | string
  energy_cost: number | string
  other_cost: number | string
  settlement_notes?: string
}

export interface ProductionRun {
  id: number
  station: ProductionStation
  station_id?: number
  order_no: string
  specification: string
  material: string
  mold?: MoldAsset | null
  mold_id?: number | null
  order_quantity: number
  cavities: number
  estimated_defect_rate: number | string
  planned_mold_count: number
  compound_size?: string
  strip_weight_kg?: number | string | null
  strips_per_batch?: number | null
  curing_seconds?: number | string
  estimated_hours: number | string
  loaded_at?: string | null
  expected_change_at?: string | null
  unloaded_at?: string | null
  status: ProductionRunStatus
  status_display?: string
  operator?: string
  unit_price?: number | string
  material_unit_price?: number | string
  notes?: string
  daily_logs?: ProductionDailyLog[]
  produced_mold_count?: number
  actual_good_quantity?: number | null
  actual_defective_quantity?: number | null
  total_material_kg?: number | string | null
  labor_cost?: number | string | null
  energy_cost?: number | string | null
  other_cost?: number | string | null
  is_settled?: boolean
  settled_at?: string | null
  settled_by_name?: string | null
  settlement_notes?: string
  good_quantity?: number | null
  defective_quantity?: number | null
  material_kg?: number | string | null
  actual_hours?: number | string | null
  progress_percent?: number | string
  remaining_mold_count?: number
  revenue?: number | string
  total_cost?: number | string
  profit?: number | string
  hourly_efficiency?: number | string | null
  created_by_name?: string
  created_at?: string
  updated_at?: string
}

export interface ProductionBoardRun {
  id: number
  order_no: string
  station_id: number
  station_code: string
  mold_id?: number | null
  mold_code?: string | null
  specification: string
  material: string
  order_quantity: number
  planned_mold_count: number
  produced_mold_count: number
  good_quantity: number
  progress_percent: number | string
  remaining_mold_count: number
  operator?: string
  status: ProductionRunStatus
  loaded_at?: string | null
  expected_change_at?: string | null
  estimated_hours: number | string
}

export interface ProductionBoardStation extends ProductionStation {
  run?: ProductionBoardRun | null
  reminder_status: ProductionReminderStatus
  minutes_to_change?: number | null
}

export interface ProductionBoardGroup {
  group: ProductionStationGroup
  stations: ProductionBoardStation[]
}

export interface ProductionSummary {
  period?: { date_from?: string; date_to?: string }
  run_count: number
  completed_run_count: number
  planned_quantity: number
  produced_mold_count: number
  good_quantity: number
  defective_quantity: number
  material_kg: number | string
  actual_hours?: number | string
  revenue: number | string
  total_cost: number | string
  profit: number | string
  average_progress_percent?: number | string
  average_hourly_efficiency?: number | string
  status_counts?: Record<ProductionRunStatus, number>
}

export interface ProductionMonthlyPerformanceOperator {
  operator: string
  total_mold_count: number
  production_days: number
  participated_run_count: number
  average_daily_mold_count: number | string
  production_hours: number | string
}

export interface ProductionMonthlyPerformance {
  month: string
  operators: ProductionMonthlyPerformanceOperator[]
  totals: {
    operator_count: number
    total_mold_count: number
    production_days: number
    operator_day_count?: number
    participated_run_count: number
    production_hours: number | string
  }
}

export interface ProductionBoard {
  generated_at: string
  reminder_window_minutes: number
  counts: {
    total: number
    idle: number
    planned: number
    running: number
    due_soon: number
    overdue: number
  }
  groups: ProductionBoardGroup[]
}

export interface ProductionImportIssue {
  level: 'error' | 'warning'
  sheet?: string
  row?: number
  field?: string
  message: string
}

export interface ProductionImportPreviewRow {
  row_key: string
  sheet: string
  order_no: string
  station_code?: string
  mold_code?: string
  status?: ProductionRunStatus
  specification?: string
  material?: string
  order_quantity?: number
  cavities?: number
  planned_mold_count?: number
  estimated_hours?: number | string | null
  loaded_at?: string | null
  expected_change_at?: string | null
  unloaded_at?: string | null
  daily_log_count?: number
  is_settled?: boolean
  actual_good_quantity?: number | null
  actual_defective_quantity?: number | null
  total_material_kg?: number | string | null
  labor_cost?: number | string | null
  energy_cost?: number | string | null
  other_cost?: number | string | null
  settlement_notes?: string
  valid?: boolean
  [key: string]: unknown
}

export interface ProductionImportPreview {
  token: string
  source_type: 'order_cards'
  sheet_count: number
  total_rows: number
  daily_log_count?: number
  error_count: number
  warning_count: number
  rows: ProductionImportPreviewRow[]
  issues: ProductionImportIssue[]
}

export type QualityEmployeeRole = 'INSPECTOR' | 'REWORKER' | 'BOTH'
export type QualityOrderStatus = 'OPEN' | 'COMPLETED' | 'CANCELLED'
export type ReturnReworkStatus = 'PENDING' | 'PROCESSING' | 'COMPLETED'
export type ReturnReasonCategory = 'APPEARANCE' | 'DIMENSION' | 'MATERIAL' | 'MIXED' | 'PACKAGING' | 'OTHER'

export interface QualityEmployee {
  id: number
  employee_no: string
  name: string
  team?: string
  role: QualityEmployeeRole
  role_display?: string
  is_active: boolean
  notes?: string
}

export interface QualityOrder {
  id: number
  order_no: string
  batch_no: string
  product_code: string
  product_name: string
  specification: string
  material: string
  order_quantity: number
  order_date: string
  due_date?: string | null
  mold_size?: string
  status: QualityOrderStatus
  status_display?: string
  notes?: string
}

export interface QualityShipment {
  id: number
  shipment_no: string
  shipment_date: string
  order: QualityOrder
  order_id?: number
  inspector: QualityEmployee
  inspector_id?: number
  inspection_quantity: number
  qualified_quantity: number
  defective_quantity: number
  shipped_quantity: number
  rework_count: number
  returned_quantity: number
  notes?: string
}

export interface ReturnRework {
  id: number
  shipment: QualityShipment
  shipment_id?: number
  rework_date: string
  reason_category: ReturnReasonCategory
  reason_category_display?: string
  reason: string
  responsible_inspector: QualityEmployee
  responsible_inspector_id?: number
  rework_employee: QualityEmployee
  rework_employee_id?: number
  returned_quantity: number
  reworked_quantity: number
  recovered_quantity: number
  scrap_quantity: number
  status: ReturnReworkStatus
  status_display?: string
  work_hours: number | string
  notes?: string
}

export interface QualityDailyTrend {
  date: string
  inspection_quantity: number
  qualified_quantity: number
  defective_quantity: number
  shipped_quantity: number
  returned_quantity: number
  reworked_quantity: number
  recovered_quantity: number
  scrap_quantity: number
}

export interface QualityOrderStatistics {
  order_id: number
  order_no: string
  batch_no: string
  product_code: string
  product_name: string
  specification: string
  material: string
  inspection_quantity: number
  qualified_quantity: number
  defective_quantity: number
  shipped_quantity: number
  returned_quantity: number
  reworked_quantity: number
  recovered_quantity: number
  scrap_quantity: number
  shipment_count: number
  rework_count: number
  first_pass_rate: number | string
  return_rate: number | string
  rework_pass_rate: number | string
}

export interface QualityEmployeeStatistics {
  employee_id: number
  employee_no: string
  name: string
  team?: string
  role: QualityEmployeeRole
  inspection_quantity: number
  qualified_quantity: number
  defective_quantity: number
  shipped_quantity: number
  inspection_days: number
  shipment_count: number
  responsible_return_quantity: number
  reworked_quantity: number
  recovered_quantity: number
  scrap_quantity: number
  first_pass_rate: number | string
  return_rate: number | string
  rework_pass_rate: number | string
}

export interface QualitySummary {
  period: { date_from: string; date_to: string }
  totals: {
    inspection_quantity: number
    qualified_quantity: number
    defective_quantity: number
    shipped_quantity: number
    returned_quantity: number
    reworked_quantity: number
    recovered_quantity: number
    scrap_quantity: number
    shipment_count: number
    order_count: number
    first_pass_rate: number | string
    return_rate: number | string
    rework_pass_rate: number | string
  }
  daily_trend: QualityDailyTrend[]
  order_stats: QualityOrderStatistics[]
  employee_stats: QualityEmployeeStatistics[]
}

export const STATUS_META: Record<MoldStatus, { text: string; color: string }> = {
  IN_STOCK: { text: '在库', color: 'success' },
  ON_MACHINE: { text: '上机', color: 'processing' },
  OUTSOURCED: { text: '客户收回', color: 'warning' },
}

export function moldCode(mold: MoldAsset) {
  return mold.asset_code || mold.code || ''
}

export function moldModelOf(mold: MoldAsset) {
  return mold.mold_model || mold.model
}

export function moldLocation(mold: MoldAsset) {
  if (mold.status === 'IN_STOCK') return mold.slot?.display_code || '在库，位置未设置'
  if (mold.status === 'ON_MACHINE') return mold.machine ? `${mold.machine.code} · ${mold.machine.name}` : '上机中'
  return '客户收回'
}
