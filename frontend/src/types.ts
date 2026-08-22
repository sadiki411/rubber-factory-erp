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
  current_mold_count?: number
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
  stage?: string
  recorded_at?: string
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

export type OrderStatus = 'OPEN' | 'COMPLETED' | 'CANCELLED'
export type OrderMaterialStatus = 'UNKNOWN' | 'NOT_RECEIVED' | 'PARTIAL' | 'SUFFICIENT' | 'OVER'
export type OrderProcessCardStatus = 'NOT_RECEIVED' | 'PARTIAL' | 'RECEIVED'

export interface ProductSpecification {
  id: number
  product_name: string
  customer_product_no?: string
  specification?: string
  material?: string
  material_length?: string
  cut_weight?: string
  strip_count?: string
  primary_curing?: string
  secondary_curing?: string
  total_cavities?: string
  effective_cavities?: string
  mold_in_stock?: string
  mold_model?: MoldModel | null
  mold_model_id?: number | null
  mold_no?: string
  mold_size?: string
  notes?: string
  normalized_key?: string
  is_active: boolean
  source_sheet?: string
  source_row?: number | null
  source_key?: string
  raw_data?: Record<string, unknown>
  latest_unit_weight_g?: number | string | null
  latest_unit_weight_measured_on?: string | null
  unit_weight_history_count?: number
  created_at?: string
  updated_at?: string
}

export interface Order {
  id: number
  order_no: string
  item_no?: string
  batch_no: string
  product_code: string
  product_name: string
  specification: string
  material: string
  order_quantity: number
  order_date: string | null
  due_date?: string | null
  mold_size?: string
  status: OrderStatus
  status_display?: string
  product_specification?: ProductSpecification | null
  /** Latest finished-piece unit weight returned by shipment candidates. */
  unit_weight_g?: number | string | null
  product_specification_id?: number | null
  forming_hours?: number | string | null
  production_required?: boolean | null
  legacy_shipment_text?: string
  required_material_kg?: number | string | null
  manual_received_material_kg?: number | string | null
  imported_received_material_kg?: number | string | null
  received_material_kg?: number | string | null
  material_gap_kg?: number | string | null
  material_status?: OrderMaterialStatus
  process_card_count?: number | null
  process_card_covered_quantity?: number | null
  process_card_status?: OrderProcessCardStatus
  process_card_text?: string
  production_quantity?: string
  shipment_date?: string
  shipped_quantity?: string
  weighted_shipped_quantity?: number
  weighted_remaining_quantity?: number
  shipment_status?: 'UNSHIPPED' | 'PARTIAL' | 'SHIPPED' | 'CANCELLED' | string
  remaining_quantity?: number | string | null
  last_data_updated_at?: string | null
  notes?: string
  source_sheet?: string
  source_row?: number | null
  source_key?: string
  raw_data?: Record<string, unknown>
  created_by_name?: string
  created_at?: string
  updated_at?: string
}

export type QualityOrder = Order

export interface MaterialReceipt {
  id: number
  order?: Pick<Order, 'id' | 'order_no' | 'item_no' | 'specification' | 'material' | 'order_quantity'> | null
  order_id?: number | null
  order_no: string
  item_no?: string
  finished_product_name?: string
  specification?: string
  material?: string
  batch_no?: string
  sheet_size?: string
  weight_kg: number | string
  issued_on?: string | null
  manufactured_on?: string | null
  source_sheet?: string
  source_row?: number | null
  source_key?: string
  raw_data?: Record<string, unknown>
  created_at?: string
  updated_at?: string
}

export type BusinessImportRecordType = 'PRODUCT_SPECIFICATION' | 'ORDER' | 'MATERIAL_RECEIPT' | 'INSPECTION_CRITERION' | string

export interface BusinessImportCounts {
  product_specifications: number
  orders: number
  material_receipts: number
  inspection_criteria: number
}

export interface BusinessImportPreviewRow {
  row_key: string
  record_type: BusinessImportRecordType
  sheet?: string
  row?: number
  action?: 'CREATE' | 'UPDATE' | 'SKIP' | string
  order_no?: string
  item_no?: string
  specification?: string
  material?: string
  summary?: string
  changes?: Record<string, { from?: unknown; to?: unknown }>
  valid?: boolean
  [key: string]: unknown
}

export interface BusinessImportPreview {
  token: string
  source_type?: string
  total_rows: number
  counts: BusinessImportCounts
  error_count: number
  warning_count: number
  rows: BusinessImportPreviewRow[]
  issues: ImportIssue[]
}

export interface BusinessImportCommitResult {
  created?: Partial<BusinessImportCounts>
  updated?: Partial<BusinessImportCounts>
  imported?: Partial<BusinessImportCounts>
  skipped?: Partial<BusinessImportCounts>
  counts?: Partial<BusinessImportCounts>
  created_count?: number
  updated_count?: number
  imported_count?: number
  skipped_count?: number
  [key: string]: unknown
}

export interface BusinessImportHistoryActions {
  CREATE: number
  UPDATE: number
  SKIP: number
}

export interface BusinessImportHistorySummary {
  token: string
  original_name: string
  source_type: string
  source_type_display: string
  parser: string
  status: 'PREVIEWED' | 'COMMITTING' | 'COMMITTED' | 'FAILED' | string
  status_display: string
  created_at: string
  committed_at?: string | null
  total_rows: number
  counts: BusinessImportCounts
  actions: BusinessImportHistoryActions
  error_count: number
  warning_count: number
}

export interface BusinessImportHistoryRow extends BusinessImportPreviewRow {
  skip_reason?: string
  skip_reason_code?: string
  reasons: string[]
  issues: ImportIssue[]
}

export interface BusinessImportHistoryDetail extends BusinessImportHistorySummary {
  rows: BusinessImportHistoryRow[]
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
export type ProductionReminderStatus = 'IDLE' | 'MOUNTED' | 'PLANNED' | 'NORMAL' | 'DUE_SOON' | 'OVERDUE'
export type ProductionStationGroup = string
export type ProductionStationPosition = number

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

export interface ProductionMold {
  id: number
  asset_code: string
  model_code: string
  product_name: string
  status: MoldStatus
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
  order?: Order | null
  order_id?: number | null
  product_specification?: ProductSpecification | null
  product_specification_id?: number | null
  order_no: string
  specification: string
  material: string
  mold?: ProductionMold | null
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
  material_changed_at?: string | null
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
  mold_model_code?: string | null
  mold_product_name?: string | null
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
  material_changed_at?: string | null
  estimated_hours: number | string
}

export interface ProductionBoardStation extends ProductionStation {
  run?: ProductionBoardRun | null
  mounted_molds: ProductionMold[]
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
    occupied: number
    mounted: number
    planned: number
    running: number
    normal: number
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
export type QualityOrderStatus = OrderStatus
export type ReturnReworkStatus = 'PENDING' | 'PROCESSING' | 'COMPLETED'
export type ReturnReasonCategory = 'APPEARANCE' | 'STICKING' | 'DIMENSION' | 'MATERIAL' | 'MIXED' | 'PACKAGING' | 'OTHER'

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

export interface QualityShipment {
  id: number
  shipment_no: string
  shipment_date: string
  order: QualityOrder
  order_id?: number
  product_specification_id?: number | null
  product_name?: string
  product_name_snapshot?: string
  customer?: string
  delivery_info?: string
  backfill_reason?: string
  inspector: QualityEmployee
  inspector_id?: number
  /** Weighted shipments may be assigned to more than one inspector. */
  inspectors?: QualityEmployee[]
  inspector_ids?: number[]
  /** Snapshots are used when the shipment was entered without an order link. */
  specification_snapshot?: string
  material_snapshot?: string
  specification?: string
  material?: string
  unit_weight_g?: number | string | null
  unit_weight_g_snapshot?: number | string | null
  total_net_weight_kg?: number | string | null
  single_batch_net_weight_kg?: number | string | null
  net_weight_kg?: number | string | null
  product_batch_count?: number | null
  batch_count?: number | null
  pieces_per_batch?: number | null
  process_card_shipment_quantity?: number | null
  piece_quantity?: number | null
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

/**
 *一期流程卡/批量出货契约。
 *
 * These shapes are additive.  Older deployments do not expose these routes;
 * the quality page falls back to the existing order/shipment endpoints until
 * the backend migration is installed.
 */
export type QualityProcessCardStatus = 'OPEN' | 'PARTIAL_SHIPPED' | 'SHIPPED' | 'CANCELLED' | 'READY' | 'PARTIAL' | 'REWORK'

export interface QualityUnitWeight {
  id: number | string
  product_specification_id?: number | null
  product_specification?: ProductSpecification | null
  mold_model_id?: number | null
  mold_model?: MoldModel | null
  unit_weight_g: number | string | null
  sample_count?: number | null
  sample_total_weight_g?: number | string | null
  measured_on?: string | null
  is_active?: boolean
  notes?: string
}

export interface QualityProcessCard {
  id: string | number
  card_no: string
  order_id: number
  order?: QualityOrder
  source_item_no?: string
  source_order_no?: string
  quantity: number
  unit_weight_g?: number | string | null
  theoretical_weight_kg?: number | string | null
  max_allowed_weight_kg?: number | string | null
  shipped_net_weight_kg?: number | string | null
  delivered_net_weight_kg?: number | string | null
  remaining_weight_kg?: number | string | null
  expected_weight_kg?: number | string | null // legacy alias
  shipped_quantity?: number // optional piece-based summary
  returned_piece_quantity?: number
  delivered_piece_quantity?: number
  shipped_weight_kg?: number | string | null // legacy alias
  due_date?: string | null
  demand_date?: string | null
  received_on?: string | null
  material_issue_weight_kg?: number | string | null
  material_weight_g?: number | string | null // legacy UI alias
  product_code_snapshot?: string
  formula_code_snapshot?: string
  product_name_snapshot?: string
  specification_snapshot?: string
  material_snapshot?: string
  customer_snapshot?: string
  department_snapshot?: string
  special_requirements?: string
  qr_text?: string
  original_image?: string | null
  backfill_reason?: string
  reprint_count?: number
  unit_weight_config_id?: string | number | null
  sample_count_snapshot?: number | null
  sample_total_weight_g_snapshot?: number | string | null
  measured_on_snapshot?: string | null
  mold_model_code_snapshot?: string
  raw_data?: Record<string, unknown>
  status?: QualityProcessCardStatus
  rework_count?: number
  source?: string
  notes?: string
}

export interface QualityShipmentBatchLineInput {
  process_card_id?: string | number
  order_id?: number
  /** Optional snapshots for a manually entered order/specification. */
  order_no?: string
  product_name?: string
  product_name_snapshot?: string
  specification?: string
  material?: string
  specification_snapshot?: string
  material_snapshot?: string
  quantity?: number
  unit_weight_g?: number | null
  unit_weight_g_snapshot?: number | null
  expected_weight_kg?: number | null
  actual_weight_kg?: number
  net_weight_kg?: number
  piece_quantity?: number
  total_net_weight_kg?: number
  single_batch_net_weight_kg?: number
  product_batch_count?: number
  batch_count?: number
  pieces_per_batch?: number
  process_card_shipment_quantity?: number
  tolerance_percent?: number
  notes?: string
}

export interface QualityShipmentBatchInput {
  shipment_no?: string
  shipment_date?: string | null
  order_id?: number | null
  order_ids?: number[]
  order_no?: string
  product_name?: string
  product_specification_id?: number | null
  specification?: string
  material?: string
  specification_snapshot?: string
  material_snapshot?: string
  unit_weight_g?: number | null
  unit_weight_g_snapshot?: number | null
  total_net_weight_kg?: number | null
  /** One scale reading; the server multiplies it by product_batch_count. */
  single_batch_net_weight_kg?: number | null
  net_weight_kg?: number | null
  product_batch_count?: number | null
  batch_count?: number | null
  pieces_per_batch?: number | null
  /** Standard quantity shown on one flow card / repeated weighed batch. */
  process_card_shipment_quantity?: number | null
  piece_quantity?: number | null
  inspector_id?: number | null
  inspector_ids?: number[]
  client_key?: string
  notes?: string
  confirm_warnings?: boolean
  lines: QualityShipmentBatchLineInput[]
}

export interface QualityShipmentBatchResult {
  id?: string | number
  shipment_no?: string
  client_key?: string
  shipment_date?: string | null
  order_id?: number | null
  order?: QualityOrder | null
  order_no?: string
  product_name?: string
  product_name_snapshot?: string
  product_specification_id?: number | null
  product_specification?: ProductSpecification | null
  specification?: string
  material?: string
  specification_snapshot?: string
  material_snapshot?: string
  unit_weight_g?: number | string | null
  unit_weight_g_snapshot?: number | string | null
  total_net_weight_kg?: number | string | null
  single_batch_net_weight_kg?: number | string | null
  net_weight_kg?: number | string | null
  product_batch_count?: number | null
  batch_count?: number | null
  pieces_per_batch?: number | null
  process_card_shipment_quantity?: number | null
  piece_quantity?: number | null
  inspector_id?: number | null
  inspector?: QualityEmployee | null
  inspector_ids?: number[]
  inspectors?: QualityEmployee[]
  line_count?: number
  shipped_quantity?: number
  actual_weight_kg?: number | string
  warnings?: string[]
  status?: 'DRAFT' | 'CONFIRMED' | 'VOID'
}

export interface QualityShipmentBatch extends QualityShipmentBatchResult {
  id: string | number
  shipment_no: string
  status?: 'DRAFT' | 'CONFIRMED' | 'VOID'
  inspector_id?: number | null
  inspector_ids?: number[]
  inspectors?: QualityEmployee[]
  date_pending?: boolean
  customer?: string
  delivery_info?: string
  net_weight_kg?: number | string
  backfill_reason?: string
  notes?: string
  lines?: QualityShipmentBatchLine[]
}

/** Candidate order returned by the weighted shipment entry helper endpoint. */
export interface QualityShipmentCandidate {
  id?: number | string
  order_id?: number | null
  order?: QualityOrder | null
  order_no?: string
  item_no?: string
  product_code?: string
  product_name?: string
  specification?: string
  material?: string
  remaining_quantity?: number | string | null
  remaining_weight_kg?: number | string | null
  unit_weight_g?: number | string | null
  is_candidate?: boolean
  reason?: string
}

/** One server-calculated step in a weighted shipment's order allocation. */
export interface QualityShipmentAllocation {
  order_id: number
  order_no: string
  item_no?: string
  due_date?: string | null
  remaining_before: number
  allocated_quantity: number
  remaining_after: number
  is_source: boolean
  /** True when all matching open orders are full and the source order is intentionally exceeded. */
  is_overflow: boolean
}

/** Read-only preview; confirmation recalculates the same plan transactionally. */
export interface QualityShipmentAllocationPreview {
  source_order_id: number
  requested_quantity: number
  specification: string
  material: string
  allocations: QualityShipmentAllocation[]
  matching_allocated_quantity: number
  overflow_quantity: number
  total_allocated_quantity: number
}

export interface QualityShipmentBatchLine {
  id: string | number
  process_card_id?: string | number
  process_card?: QualityProcessCard
  order_id?: number | null
  order?: QualityOrder | null
  product_specification_id?: number | null
  product_specification?: ProductSpecification | null
  card_no?: string
  unit_weight_g?: number | string | null
  unit_weight_g_snapshot?: number | string | null
  quantity?: number | null
  remaining_quantity?: number | null
  specification_snapshot?: string
  material_snapshot?: string
  net_weight_kg: number | string
  single_batch_net_weight_kg?: number | string | null
  product_batch_count?: number | null
  pieces_per_batch?: number | null
  process_card_shipment_quantity?: number | null
  piece_quantity?: number | null
  theoretical_weight_kg_snapshot?: number | string | null
  max_allowed_weight_kg_snapshot?: number | string | null
  notes?: string
}

export interface QualityReturnableBatchLineSummary {
  shipment_line_id?: number | string | null
  process_card_id?: number | string | null
  order_id?: number | null
  order_no?: string
  item_no?: string
  card_no?: string
  piece_quantity?: number | null
  net_weight_kg?: number | string | null
}

export type QualityEmployeeSummary = Pick<QualityEmployee, 'id' | 'employee_no' | 'name'> & Partial<Pick<QualityEmployee, 'role' | 'is_active' | 'team'>>

/** One confirmed physical weighed-batch group that can be selected for a whole-batch return. */
export interface QualityReturnableBatch {
  key: string
  source_type: 'WEIGHTED'
  shipment_batch_id: number | string
  shipment_line_id?: number | string | null
  shipment_no: string
  shipment_date?: string | null
  order_ids?: number[]
  order_no?: string
  item_no?: string
  product_name?: string
  specification?: string
  material?: string
  single_batch_net_weight_kg: number | string
  pieces_per_batch: number
  total_batches: number
  available_batches: number
  available_batch_numbers: number[]
  returned_batches: number
  rework_count: number
  next_return_no: number
  inspectors?: QualityEmployeeSummary[]
  lines?: QualityReturnableBatchLineSummary[]
}

export interface QualityReworkSource {
  shipment_batch_id: number | string
  shipment_line_id?: number | string | null
  shipment_unit_no: number
  shipment_no: string
  shipment_date?: string | null
  order_ids?: number[]
  order_no?: string
  item_no?: string
  product_name?: string
  specification?: string
  material?: string
  single_batch_net_weight_kg: number | string
  pieces_per_batch: number
  total_batches: number
  inspectors?: QualityEmployeeSummary[]
  lines?: QualityReturnableBatchLineSummary[]
}

export type QualityShipmentLedgerSource = 'LEGACY' | 'WEIGHTED'
export type QualityShipmentLedgerStatus = 'CONFIRMED' | 'DRAFT' | 'VOID'

/** A read-only row from the unified historical and weighted shipment ledger. */
export interface QualityShipmentLedgerRow {
  key: string
  source_type: QualityShipmentLedgerSource
  source_id: number | string
  status: QualityShipmentLedgerStatus
  status_display?: string
  shipment_no: string
  shipment_date?: string | null
  order_nos?: string[]
  item_nos?: string[]
  product_names?: string[]
  specifications?: string[]
  materials?: string[]
  due_dates?: string[]
  order_statuses?: string[]
  delivery_statuses?: string[]
  customers?: string[]
  inspectors?: QualityEmployee[]
  inspection_quantity?: number | null
  qualified_quantity?: number | null
  defective_quantity?: number | null
  shipped_quantity?: number | null
  returned_quantity?: number | null
  rework_count?: number | null
  net_weight_kg?: number | string | null
  line_count?: number
  shipment?: QualityShipment | null
  batch?: QualityShipmentBatch | null
  record?: QualityShipment | QualityShipmentBatch | null
}

export type QualityReworkOrigin = 'INTERNAL' | 'CUSTOMER_RETURN'
export type QualityReworkCaseStatus = 'OPEN' | 'PROCESSING' | 'WAITING_REINSPECTION' | 'COMPLETED' | 'SCRAPPED' | 'CANCELLED'

export interface QualityReworkCase {
  id: number | string
  case_no: string
  origin: QualityReworkOrigin
  process_card_id?: number | string | null
  shipment_batch_id?: number | string | null
  shipment_line_id?: number | string | null
  shipment_unit_no?: number | null
  opened_on: string
  backfill_reason?: string
  responsible_inspector_id?: number | null
  reason_category: ReturnReasonCategory
  reason_category_display?: string
  reason?: string
  affected_quantity?: number | null
  affected_weight_kg?: number | string | null
  status: QualityReworkCaseStatus
  attempt_count?: number
  notes?: string
  attempts?: QualityReworkAttempt[]
  source?: QualityReworkSource | null
}

export interface QualityReworkAttempt {
  id: number | string
  case_id?: number | string
  attempt_no?: number
  attempt_date: string
  backfill_reason?: string
  rework_employee_id?: number | null
  input_quantity: number
  reworked_quantity: number
  recovered_quantity: number
  scrap_quantity: number
  status: QualityReworkCaseStatus
  input_weight_kg?: number | string
  reworked_weight_kg?: number | string
  recovered_weight_kg?: number | string
  scrap_weight_kg?: number | string
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

export type AnalyticsSource = 'AUTOMATIC' | 'MANUAL' | 'COMBINED'
export type ManualPerformanceEntryType = 'PRODUCTION' | 'QUALITY' | 'REWORK'

export interface AnalyticsFinanceMetrics {
  revenue: number | string
  material_cost: number | string
  labor_cost: number | string
  energy_cost: number | string
  other_cost: number | string
  total_cost: number | string
  profit: number | string
  profit_margin?: number | string | null
}

export interface AnalyticsProductionMetrics {
  produced_mold_count: number
  theoretical_output_quantity: number
  automatic_equivalent_hours: number | string
  automatic_actual_machine_hours: number | string
  manual_reported_hours: number | string
  molds_per_equivalent_hour?: number | string | null
  molds_per_reported_hour?: number | string | null
  efficiency_percent?: number | string | null
}

export interface AnalyticsQualityMetrics {
  inspection_quantity: number
  qualified_quantity: number
  defective_quantity: number
  shipped_quantity: number
  returned_quantity: number
  reworked_quantity: number
  recovered_quantity: number
  scrap_quantity: number
  first_pass_rate?: number | string | null
  return_rate?: number | string | null
  rework_pass_rate?: number | string | null
}

export interface AnalyticsDailyTrend extends AnalyticsQualityMetrics, AnalyticsFinanceMetrics {
  date: string
  automatic_produced_mold_count: number
  manual_produced_mold_count: number
  produced_mold_count: number
  theoretical_output_quantity: number
  automatic_equivalent_hours: number | string
  manual_reported_hours: number | string
  automatic_revenue: number | string
  manual_revenue: number | string
  automatic_total_cost: number | string
  manual_total_cost: number | string
  automatic_profit: number | string
  manual_profit: number | string
}

export interface AnalyticsOperatorPerformance {
  operator: string
  automatic_mold_count: number
  manual_mold_count: number
  total_mold_count: number
  theoretical_output_quantity: number
  automatic_equivalent_hours: number | string
  manual_reported_hours: number | string
  automatic_molds_per_equivalent_hour?: number | string | null
  manual_molds_per_reported_hour?: number | string | null
  average_daily_mold_count?: number | string | null
  production_days: number
  participated_run_count: number
  automatic_record_count: number
  manual_record_count: number
  source: AnalyticsSource
}

export interface AnalyticsStationPerformance extends AnalyticsFinanceMetrics {
  machine_id?: number | null
  machine_code: string
  machine_name: string
  station_id?: number | null
  station_code: string
  group?: string | null
  automatic_mold_count: number
  manual_mold_count: number
  total_mold_count: number
  theoretical_output_quantity: number
  automatic_equivalent_hours: number | string
  automatic_actual_machine_hours: number | string
  manual_reported_hours: number | string
  automatic_molds_per_equivalent_hour?: number | string | null
  manual_molds_per_reported_hour?: number | string | null
  automatic_efficiency_percent?: number | string | null
  run_count: number
  automatic_record_count: number
  manual_record_count: number
  source: AnalyticsSource
}

export interface AnalyticsQualityEmployeePerformance extends AnalyticsQualityMetrics {
  employee_id?: number | null
  employee_no: string
  name: string
  team: string
  role?: QualityEmployeeRole | null
  responsible_return_quantity: number
  handled_returned_quantity: number
  rework_hours: number | string
  inspection_days: number
  automatic_record_count: number
  manual_record_count: number
  source: AnalyticsSource
}

export interface AnalyticsDefectReason {
  reason_category: string
  reason_category_display: string
  returned_quantity: number
  reworked_quantity: number
  recovered_quantity: number
  scrap_quantity: number
  rework_hours: number | string
  share_of_returns?: number | string | null
  rework_pass_rate?: number | string | null
  automatic_record_count: number
  manual_record_count: number
  source: AnalyticsSource
}

export interface AnalyticsOrderPerformance extends AnalyticsQualityMetrics, AnalyticsFinanceMetrics {
  row_key: string
  order_id?: number | null
  order_no: string
  product_name: string
  specification: string
  material: string
  automatic_produced_mold_count: number
  manual_produced_mold_count: number
  produced_mold_count: number
  theoretical_output_quantity: number
  production_run_count: number
  automatic_record_count: number
  manual_record_count: number
  source: AnalyticsSource
  link_type?: 'ORDER' | 'LEGACY' | string
}

export interface ManualPerformanceEntry {
  id: number
  entry_date: string
  entry_type: ManualPerformanceEntryType
  entry_type_display?: string
  staff_name: string
  order_no?: string
  machine?: Machine | null
  machine_id?: number | null
  quality_employee?: QualityEmployee | null
  quality_employee_id?: number | null
  produced_mold_count: number
  production_hours: number | string
  inspection_quantity: number
  qualified_quantity: number
  defective_quantity: number
  shipped_quantity: number
  returned_quantity: number
  reason_category?: string
  reason_category_display?: string
  reworked_quantity: number
  recovered_quantity: number
  scrap_quantity: number
  rework_hours: number | string
  notes?: string
  created_by_name?: string
  voided_at?: string | null
  voided_by_name?: string | null
  void_reason?: string
  created_at?: string
  updated_at?: string
}

export interface ManualFinancialEntry {
  id: number
  occurred_on: string
  direction: 'INCOME' | 'EXPENSE'
  category: 'SALES' | 'MATERIAL' | 'LABOR' | 'ENERGY' | 'OTHER' | 'ADJUSTMENT'
  direction_display?: string
  category_display?: string
  amount: number | string
  profit_effect?: number | string
  order_no?: string
  machine?: Machine | null
  machine_id?: number | null
  staff_name?: string
  description?: string
  notes?: string
  created_by_name?: string
  voided_at?: string | null
  voided_by_name?: string | null
  void_reason?: string
  created_at?: string
  updated_at?: string
}

export interface AnalyticsDashboard {
  period: { date_from: string; date_to: string; month?: string | null; group?: string | null; machine_id?: number | null }
  data_basis: {
    production_quantity_date: string
    automatic_production_hours: string
    automatic_actual_machine_hours: string
    manual_production_hours: string
    automatic_finance_date: string
    manual_finance_date: string
    quality_date: string
    rework_date: string
    order_link: string
    quality_filter_scope: string
    zero_denominator_rate: null
  }
  sources: Record<string, { automatic: number; manual: number; total: number; [key: string]: number }>
  production: {
    automatic: AnalyticsProductionMetrics
    manual: AnalyticsProductionMetrics
    total: AnalyticsProductionMetrics
    production_days: number
    operator_count: number
    run_count: number
    settled_run_count: number
    unsettled_completed_run_count: number
    status_counts: Record<ProductionRunStatus, number>
    settled_good_quantity: number
    settled_defective_quantity: number
    settled_defect_rate?: number | string | null
  }
  finance?: {
    automatic: AnalyticsFinanceMetrics
    manual: AnalyticsFinanceMetrics
    total: AnalyticsFinanceMetrics
    combined?: AnalyticsFinanceMetrics
  }
  quality: {
    automatic: AnalyticsQualityMetrics
    manual: AnalyticsQualityMetrics
    total: AnalyticsQualityMetrics
    shipment_count: number
    rework_count: number
  }
  daily_trend: AnalyticsDailyTrend[]
  operator_performance: AnalyticsOperatorPerformance[]
  station_performance: AnalyticsStationPerformance[]
  quality_employee_performance: AnalyticsQualityEmployeePerformance[]
  defect_reason_breakdown: AnalyticsDefectReason[]
  order_performance: AnalyticsOrderPerformance[]
  manual_entries?: ManualPerformanceEntry[]
  manual_financial_entries?: ManualFinancialEntry[]
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
