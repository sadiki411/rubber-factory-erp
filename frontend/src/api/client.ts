import type {
  ApiList,
  AnalyticsDashboard,
  BusinessImportCommitResult,
  BusinessImportHistoryDetail,
  BusinessImportHistorySummary,
  BusinessImportPreview,
  ImportPreview,
  Machine,
  MaterialReceipt,
  ManualFinancialEntry,
  ManualPerformanceEntry,
  MoldAsset,
  MoldModel,
  MoldMovement,
  Order,
  OrderStatusChange,
  Processor,
  ProductionBoard,
  ProductionDailyLog,
  ProductionEmployee,
  ProductionImportPreview,
  ProductionMonthlyPerformance,
  ProductionRecordAudit,
  ProductionRun,
  ProductionSettlementInput,
  ProductionStation,
  ProductionSummary,
  ProductSpecification,
  QualityEmployee,
  QualityProcessCard,
  QualityProcessCardScanResult,
  QualityUnitWeight,
  QualityShipmentBatch,
  QualityShipmentBatchInput,
  QualityShipmentBatchResult,
  QualityShipmentAllocationPreview,
  QualityReworkCase,
  QualityReworkAttempt,
  QualityReturnableBatch,
  QualityShipment,
  QualityShipmentLedgerRow,
  QualityShipmentCandidate,
  QualityReturnReason,
  QualitySummary,
  RackConfigInput,
  RackLayout,
  RackSlot,
  RackSummary,
  SessionResponse,
  ReturnRework,
} from '../types'

export class ApiError extends Error {
  status: number
  data: any

  constructor(status: number, message: string, data?: any) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.data = data
  }
}

function readCookie(name: string) {
  const item = document.cookie
    .split('; ')
    .find((row) => row.startsWith(`${name}=`))
  return item ? decodeURIComponent(item.split('=').slice(1).join('=')) : ''
}

function errorMessage(data: any, fallback: string) {
  if (typeof data === 'string') {
    const value = data.trim()
    return value.startsWith('<!DOCTYPE') || value.startsWith('<html') ? fallback : value
  }
  if (typeof data?.detail === 'string') return data.detail
  if (typeof data?.message === 'string') return data.message
  if (typeof data?.error === 'string') return data.error
  if (data && typeof data === 'object') {
    const first = Object.values(data).flat()[0]
    if (typeof first === 'string') return first
  }
  return fallback
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method || 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  if (!(init.body instanceof FormData) && init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const csrf = readCookie('csrftoken')
    if (csrf) headers.set('X-CSRFToken', csrf)
  }
  headers.set('Accept', 'application/json')

  const response = await fetch(path, {
    ...init,
    headers,
    credentials: 'include',
  })
  const contentType = response.headers.get('content-type') || ''
  const data = contentType.includes('application/json')
    ? await response.json()
    : await response.text()

  if (!response.ok) {
    throw new ApiError(response.status, errorMessage(data, `请求失败（${response.status}）`), data)
  }
  return data as T
}

export function toList<T>(payload: ApiList<T> | T[]): T[] {
  return Array.isArray(payload) ? payload : payload.results || []
}

function mapList<T, U>(payload: ApiList<T> | T[], mapper: (item: T) => U): ApiList<U> | U[] {
  if (Array.isArray(payload)) return payload.map(mapper)
  return { ...payload, results: (payload.results || []).map(mapper) }
}

function normalizeMaster<T extends Record<string, any>>(item: T): T {
  return { ...item, active: item.active ?? item.is_active }
}

function masterBody<T extends Record<string, any>>(body: T) {
  const result: Record<string, any> = { ...body }
  if ('active' in result) {
    result.is_active = result.active
    delete result.active
  }
  return result
}

function normalizeMold(raw: any): MoldAsset {
  return {
    ...raw,
    asset_code: raw.asset_code || raw.code,
    mold_model: raw.mold_model || raw.model,
    slot: raw.slot ?? raw.current_slot ?? null,
    machine: raw.machine ?? raw.current_machine ?? null,
    processor: raw.processor ?? raw.current_processor ?? null,
    note: raw.note ?? raw.notes ?? '',
    can_stack: raw.can_stack ?? raw.allows_stacking ?? false,
    image: raw.image ?? raw.main_image ?? null,
  }
}

function normalizeSlot(raw: any): RackSlot {
  const mold = raw.mold ?? raw.occupant ?? undefined
  const active = raw.active ?? raw.is_enabled ?? raw.is_active ?? !raw.is_blocked
  return {
    ...raw,
    active,
    available: raw.available ?? (active && !mold),
    mold,
  }
}

function normalizeLayout(raw: any): RackLayout {
  const rackRaw = raw.rack || raw
  const rack: RackSummary = {
    ...rackRaw,
    configured: rackRaw.configured ?? rackRaw.is_configured,
    locked: rackRaw.locked ?? rackRaw.structure_locked,
  }
  const levels = (raw.levels || rackRaw.levels || []).map((level: any) => ({
    ...level,
    zones: (level.zones || []).map((zone: any) => {
      const currentCapacity = zone.current_capacity ?? zone.capacity_mode ?? zone.default_capacity
      const supportsStacking = zone.supports_stacking ?? (zone.stack_levels ?? 1) > 1
      const stackingEnabled = zone.stacking_enabled
        ?? zone.default_stacking_enabled
        ?? (zone.stack_levels ?? 1) > 1
      return {
        ...zone,
        name: zone.name || zone.label || zone.code,
        current_capacity: currentCapacity,
        supports_stacking: supportsStacking,
        stacking_enabled: supportsStacking && stackingEnabled,
        stack_levels: supportsStacking && stackingEnabled ? 2 : 1,
        is_active: zone.is_active ?? zone.active ?? true,
        slots: (zone.slots || []).filter((slot: any) => slot.capacity_mode === undefined || slot.capacity_mode === currentCapacity).map(normalizeSlot),
      }
    }),
  }))
  return { rack, levels }
}

export const authApi = {
  session: () => apiFetch<SessionResponse>('/api/auth/session/'),
  login: (username: string, password: string) =>
    apiFetch<SessionResponse>('/api/auth/login/', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  logout: () => apiFetch<void>('/api/auth/logout/', { method: 'POST' }),
}

export interface MoldFilters {
  q?: string
  status?: string
  page?: number
  page_size?: number
}

function queryString(values: object) {
  const params = new URLSearchParams()
  Object.entries(values as Record<string, unknown>).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  })
  const text = params.toString()
  return text ? `?${text}` : ''
}

export const moldApi = {
  list: (filters: MoldFilters = {}) =>
    apiFetch<ApiList<MoldAsset> | MoldAsset[]>(`/api/molds/${queryString(filters)}`).then((payload) => mapList(payload, normalizeMold)),
  detail: (id: number | string) => apiFetch<MoldAsset>(`/api/molds/${id}/`).then(normalizeMold),
  history: (id: number | string) =>
    apiFetch<ApiList<MoldMovement> | MoldMovement[]>(`/api/molds/${id}/history/`),
  create: (body: FormData) => apiFetch<MoldAsset>('/api/molds/', { method: 'POST', body }).then(normalizeMold),
  update: (id: number, body: FormData) => apiFetch<MoldAsset>(`/api/molds/${id}/`, { method: 'PATCH', body }).then(normalizeMold),
  remove: (id: number, confirmWarnings = false) => apiFetch<void>(`/api/molds/${id}/`, {
    method: 'DELETE',
    body: JSON.stringify({ confirm_warnings: confirmWarnings }),
  }),
  action: (id: number, action: string, body: Record<string, unknown>) =>
    apiFetch<MoldAsset>(`/api/molds/${id}/actions/${action}/`, {
      method: 'POST',
      body: JSON.stringify(body),
    }).then(normalizeMold),
}

function rackConfigPayload(body: RackConfigInput) {
  const defaultStackingEnabled = body.default_stacking_enabled ?? body.stack_levels > 1
  const definitions = body.zone_type === 'SPLIT'
    ? [{ code: 'A', label: '左区' }, { code: 'B', label: '右区' }]
    : [{ code: 'F', label: '整层' }]
  return {
    level_count: body.level_count,
    zones: definitions.map((zone) => ({
      ...zone,
      allowed_capacities: body.allowed_capacities,
      default_capacity: body.default_capacity,
      supports_stacking: true,
      stack_levels: defaultStackingEnabled ? 2 : 1,
      default_stacking_enabled: defaultStackingEnabled,
    })),
  }
}

export const rackApi = {
  list: () => apiFetch<ApiList<RackSummary> | RackSummary[]>('/api/racks/').then((payload) => mapList(payload, (rack) => ({ ...rack, configured: rack.configured ?? rack.is_configured, locked: rack.locked ?? rack.structure_locked }))),
  create: (body: Pick<RackSummary, 'code' | 'name'>) =>
    apiFetch<RackSummary>('/api/racks/', { method: 'POST', body: JSON.stringify(body) }),
  layout: (id: number | string) => apiFetch<RackLayout>(`/api/racks/${id}/layout/`).then(normalizeLayout),
  preview: (body: RackConfigInput) =>
    apiFetch<RackLayout>('/api/racks/config-preview/', { method: 'POST', body: JSON.stringify({ rack_code: body.code, ...rackConfigPayload(body) }) }).then(normalizeLayout),
  configure: (id: number, body: RackConfigInput) =>
    apiFetch<RackLayout>(`/api/racks/${id}/configure/`, { method: 'POST', body: JSON.stringify(rackConfigPayload(body)) }).then(normalizeLayout),
  switchCapacity: (rackId: number, zoneId: number, capacity: number) =>
    apiFetch<RackLayout>(`/api/racks/${rackId}/zones/${zoneId}/capacity/`, {
      method: 'POST',
      body: JSON.stringify({ capacity }),
    }).then(normalizeLayout),
  switchStacking: (rackId: number, zoneId: number, enabled: boolean) =>
    apiFetch<RackLayout>(`/api/racks/${rackId}/zones/${zoneId}/stacking/`, {
      method: 'POST',
      body: JSON.stringify({ enabled }),
    }).then(normalizeLayout),
}

export const slotApi = {
  list: (available = false) =>
    apiFetch<ApiList<RackSlot> | RackSlot[]>(`/api/slots/${queryString({ available: available || undefined })}`).then((payload) => mapList(payload, normalizeSlot)),
}

type MasterRecord = MoldModel | Machine | Processor

export function masterApi<T extends MasterRecord>(resource: 'mold-models' | 'machines' | 'processors') {
  return {
    list: () => apiFetch<ApiList<T> | T[]>(`/api/${resource}/`).then((payload) => mapList(payload, normalizeMaster) as ApiList<T> | T[]),
    create: (body: Partial<T>) =>
      apiFetch<T>(`/api/${resource}/`, { method: 'POST', body: JSON.stringify(masterBody(body)) }).then(normalizeMaster),
    update: (id: number, body: Partial<T>) =>
      apiFetch<T>(`/api/${resource}/${id}/`, { method: 'PATCH', body: JSON.stringify(masterBody(body)) }).then(normalizeMaster),
    remove: (id: number) => apiFetch<void>(`/api/${resource}/${id}/`, { method: 'DELETE' }),
  }
}

export const importApi = {
  preview: (file: File) => {
    const body = new FormData()
    body.append('file', file)
    return apiFetch<ImportPreview>('/api/imports/preview/', { method: 'POST', body })
  },
  commit: (token: string, rows?: Array<Pick<ImportPreview['rows'][number], 'row_key' | 'asset_code'>>) =>
    apiFetch<{ imported_count: number }>('/api/imports/commit/', {
      method: 'POST',
      body: JSON.stringify({ token, rows }),
    }),
  templateUrl: '/api/imports/template/',
  errorReportUrl: (token: string) => `/api/imports/${token}/errors/`,
}

export interface ProductSpecificationFilters {
  q?: string
  active?: boolean
  page?: number
  page_size?: number
}

export const productSpecificationApi = {
  list: (filters: ProductSpecificationFilters = {}) =>
    apiFetch<ApiList<ProductSpecification> | ProductSpecification[]>(`/api/orders/product-specifications/${queryString(filters)}`),
  detail: (id: number | string) => apiFetch<ProductSpecification>(`/api/orders/product-specifications/${id}/`),
  create: (body: Partial<ProductSpecification>) => apiFetch<ProductSpecification>('/api/orders/product-specifications/', {
    method: 'POST', body: JSON.stringify(body),
  }),
  update: (id: number, body: Partial<ProductSpecification>) => apiFetch<ProductSpecification>(`/api/orders/product-specifications/${id}/`, {
    method: 'PATCH', body: JSON.stringify(body),
  }),
}

export interface OrderFilters {
  q?: string
  status?: string
  production_required?: boolean
  material_status?: string
  process_card_status?: string
  /** Comma-separated, stable ordering levels (maximum three). */
  ordering?: string
  page?: number
  page_size?: number
}

export const orderApi = {
  list: (filters: OrderFilters = {}) => apiFetch<ApiList<Order> | Order[]>(`/api/orders/orders/${queryString(filters)}`),
  detail: (id: number | string) => apiFetch<Order>(`/api/orders/orders/${id}/`),
  create: (body: Partial<Order>) => apiFetch<Order>('/api/orders/orders/', {
    method: 'POST', body: JSON.stringify(body),
  }),
  update: (id: number, body: Partial<Order>) => apiFetch<Order>(`/api/orders/orders/${id}/`, {
    method: 'PATCH', body: JSON.stringify(body),
  }),
  statusHistory: (id: number | string) => apiFetch<OrderStatusChange[]>(`/api/orders/orders/${id}/status-history/`),
}

export interface MaterialReceiptFilters {
  q?: string
  order_id?: number
  order_no?: string
  batch_no?: string
  date_from?: string
  date_to?: string
  linked?: boolean
  page?: number
  page_size?: number
}

export const materialReceiptApi = {
  list: (filters: MaterialReceiptFilters = {}) => apiFetch<ApiList<MaterialReceipt> | MaterialReceipt[]>(`/api/orders/material-receipts/${queryString(filters)}`),
  detail: (id: number | string) => apiFetch<MaterialReceipt>(`/api/orders/material-receipts/${id}/`),
  create: (body: Partial<MaterialReceipt>) => apiFetch<MaterialReceipt>('/api/orders/material-receipts/', {
    method: 'POST', body: JSON.stringify(body),
  }),
  update: (id: number, body: Partial<MaterialReceipt>) => apiFetch<MaterialReceipt>(`/api/orders/material-receipts/${id}/`, {
    method: 'PATCH', body: JSON.stringify(body),
  }),
}

export const businessImportApi = {
  preview: (file: File) => {
    const body = new FormData()
    body.append('file', file)
    return apiFetch<BusinessImportPreview>('/api/orders/imports/preview/', { method: 'POST', body })
  },
  commit: (token: string) => apiFetch<BusinessImportCommitResult>('/api/orders/imports/commit/', {
    method: 'POST', body: JSON.stringify({ token }),
  }),
  history: (filters: { q?: string; status?: string; source_type?: string; page?: number; page_size?: number } = {}) =>
    apiFetch<ApiList<BusinessImportHistorySummary> | BusinessImportHistorySummary[]>(`/api/orders/imports/history/${queryString(filters)}`),
  historyDetail: (token: string) => apiFetch<BusinessImportHistoryDetail>(`/api/orders/imports/history/${token}/`),
  templateUrl: (type: 'orders' | 'product_specifications') => `/api/orders/imports/template/${queryString({ type })}`,
  errorReportUrl: (token: string) => `/api/orders/imports/${token}/errors/`,
}

export interface ProductionRunFilters {
  q?: string
  status?: string
  station?: number
  group?: string
  mold?: number
  date_from?: string
  date_to?: string
  page?: number
  page_size?: number
}

export interface ProductionSummaryFilters {
  date_from?: string
  date_to?: string
  group?: string
}

export const productionApi = {
  stations: () => apiFetch<ApiList<ProductionStation> | ProductionStation[]>('/api/production/stations/'),
  board: (reminderMinutes = 60) => apiFetch<ProductionBoard>(`/api/production/board/${queryString({ reminder_minutes: reminderMinutes })}`),
  summary: (filters: ProductionSummaryFilters = {}) => apiFetch<ProductionSummary>(`/api/production/summary/${queryString(filters)}`),
  listRuns: (filters: ProductionRunFilters = {}) =>
    apiFetch<ApiList<ProductionRun> | ProductionRun[]>(`/api/production/runs/${queryString(filters)}`),
  detailRun: (id: number | string) => apiFetch<ProductionRun>(`/api/production/runs/${id}/`),
  createRun: (body: Record<string, unknown>) => apiFetch<ProductionRun>('/api/production/runs/', {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  updateRun: (id: number, body: Record<string, unknown>) => apiFetch<ProductionRun>(`/api/production/runs/${id}/`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  }),
  startRun: (id: number, body: Record<string, unknown> = {}) => apiFetch<ProductionRun>(`/api/production/runs/${id}/start/`, {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  addLog: (id: number, body: Partial<ProductionDailyLog>) => apiFetch<ProductionRun>(`/api/production/runs/${id}/daily-logs/`, {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  updateLog: (runId: number, logId: number, body: Partial<ProductionDailyLog>) => apiFetch<ProductionRun>(`/api/production/runs/${runId}/daily-logs/${logId}/`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  }),
  listEmployees: (filters: { q?: string; active?: boolean } = {}) =>
    apiFetch<ProductionEmployee[]>(`/api/production/employees/${queryString(filters)}`),
  createEmployee: (body: Pick<ProductionEmployee, 'name'> & Partial<ProductionEmployee>) =>
    apiFetch<ProductionEmployee>('/api/production/employees/', { method: 'POST', body: JSON.stringify(body) }),
  updateEmployee: (id: number, body: Partial<ProductionEmployee>) =>
    apiFetch<ProductionEmployee>(`/api/production/employees/${id}/`, { method: 'PATCH', body: JSON.stringify(body) }),
  addCounterLog: (runId: number, body: Record<string, unknown>) =>
    apiFetch<ProductionDailyLog>(`/api/production/runs/${runId}/counter-logs/`, { method: 'POST', body: JSON.stringify(body) }),
  updateCounterLog: (runId: number, logId: number, body: Record<string, unknown>) =>
    apiFetch<ProductionDailyLog>(`/api/production/runs/${runId}/counter-logs/${logId}/`, { method: 'PATCH', body: JSON.stringify(body) }),
  cancelCounterLog: (runId: number, logId: number, reason: string) =>
    apiFetch<ProductionDailyLog>(`/api/production/runs/${runId}/counter-logs/${logId}/cancel/`, { method: 'POST', body: JSON.stringify({ reason }) }),
  resetCounter: (runId: number, note = '') =>
    apiFetch<ProductionRun>(`/api/production/runs/${runId}/reset-counter/`, { method: 'POST', body: JSON.stringify({ note }) }),
  completeLedger: (runId: number, body: { note?: string; confirm_below_target?: boolean } = {}) =>
    apiFetch<ProductionRun>(`/api/production/runs/${runId}/complete-ledger/`, { method: 'POST', body: JSON.stringify(body) }),
  pauseRun: (runId: number, body: Record<string, unknown>) =>
    apiFetch<ProductionRun>(`/api/production/runs/${runId}/pause/`, { method: 'POST', body: JSON.stringify(body) }),
  resumeRun: (runId: number, body: Record<string, unknown> = {}) =>
    apiFetch<ProductionRun>(`/api/production/runs/${runId}/resume/`, { method: 'POST', body: JSON.stringify(body) }),
  recordAudits: (runId: number) =>
    apiFetch<ProductionRecordAudit[]>(`/api/production/runs/${runId}/record-audits/`),
  completeRun: (id: number, body: Record<string, unknown> = {}) => apiFetch<ProductionRun>(`/api/production/runs/${id}/complete/`, {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  completeAndPutaway: (id: number, body: Record<string, unknown>) => apiFetch<ProductionRun>(`/api/production/runs/${id}/complete-and-putaway/`, {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  settleRun: (id: number, body: ProductionSettlementInput) => apiFetch<ProductionRun>(`/api/production/runs/${id}/settlement/`, {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  monthlyPerformance: (month: string) => apiFetch<ProductionMonthlyPerformance>(`/api/production/performance/monthly/${queryString({ month })}`),
}

export const productionImportApi = {
  preview: (file: File) => {
    const body = new FormData()
    body.append('file', file)
    return apiFetch<ProductionImportPreview>('/api/production/imports/preview/', { method: 'POST', body })
  },
  commit: (token: string) => apiFetch<{ imported_count: number; log_count: number; settled_count?: number }>('/api/production/imports/commit/', {
    method: 'POST',
    body: JSON.stringify({ token }),
  }),
  templateUrl: '/api/production/imports/template/',
  errorReportUrl: (token: string) => `/api/production/imports/${token}/errors/`,
  ledgerPreview: (file: File) => {
    const body = new FormData()
    body.append('file', file)
    return apiFetch<Record<string, any>>('/api/production/ledger-imports/preview/', { method: 'POST', body })
  },
  ledgerCommit: (token: string, confirmWarnings = false) => apiFetch<Record<string, any>>('/api/production/ledger-imports/commit/', {
    method: 'POST',
    body: JSON.stringify({ token, confirm_warnings: confirmWarnings }),
  }),
  ledgerTemplateUrl: '/api/production/ledger-imports/template/',
  ocrPreview: (files: File[]) => {
    const body = new FormData()
    files.forEach((file) => body.append('files', file))
    return apiFetch<Record<string, any>>('/api/production/ocr/preview/', { method: 'POST', body })
  },
}

export interface QualityListFilters {
  q?: string
  status?: string
  shipment_status?: string
  order_status?: string
  delivery_status?: string
  role?: string
  active?: boolean
  order?: number
  order_id?: number
  shipment?: number
  inspector?: number
  employee?: number
  responsible_inspector?: number
  rework_employee?: number
  reason_category?: string
  date_from?: string
  date_to?: string
  due_date_from?: string
  due_date_to?: string
  ordering?: string
  page?: number
  page_size?: number
  shipment_no?: string
  order_no?: string
  specification?: string
  material?: string
  candidate?: boolean
  card_no?: string
  card_suffix?: string
  item_no?: string
  return_round?: number | string
  current?: boolean
  reason?: string
}

export const qualityApi = {
  listEmployees: (filters: QualityListFilters = {}) =>
    apiFetch<ApiList<QualityEmployee> | QualityEmployee[]>(`/api/quality/employees/${queryString(filters)}`),
  createEmployee: (body: Record<string, unknown>) => apiFetch<QualityEmployee>('/api/quality/employees/', {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  updateEmployee: (id: number, body: Record<string, unknown>) => apiFetch<QualityEmployee>(`/api/quality/employees/${id}/`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  }),

  listShipments: (filters: QualityListFilters = {}) =>
    apiFetch<ApiList<QualityShipment> | QualityShipment[]>(`/api/quality/shipments/${queryString(filters)}`),
  listShipmentLedger: (filters: QualityListFilters = {}) =>
    apiFetch<ApiList<QualityShipmentLedgerRow> | QualityShipmentLedgerRow[]>(`/api/quality/shipment-ledger/${queryString(filters)}`),
  createShipment: (body: Record<string, unknown>) => apiFetch<QualityShipment>('/api/quality/shipments/', {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  updateShipment: (id: number, body: Record<string, unknown>) => apiFetch<QualityShipment>(`/api/quality/shipments/${id}/`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  }),
  /**
   * Lightweight duplicate check used by the weighted shipment drawer.  The
   * endpoint is additive; older servers can simply return an empty result and
   * the drawer still performs a client-side check against loaded records.
   */
  checkShipmentNo: (shipmentNo: string, excludeId?: number | string) =>
    apiFetch<{ exists?: boolean; duplicate?: boolean; shipment?: QualityShipment }>(`/api/quality/shipments/check-shipment-no/${queryString({ shipment_no: shipmentNo, exclude_id: excludeId })}`),
  listShipmentCandidates: (filters: QualityListFilters = {}) =>
    apiFetch<ApiList<QualityShipmentCandidate> | QualityShipmentCandidate[]>(`/api/quality/shipments/candidates/${queryString(filters)}`),

  listReworks: (filters: QualityListFilters = {}) =>
    apiFetch<ApiList<ReturnRework> | ReturnRework[]>(`/api/quality/reworks/${queryString(filters)}`),
  createRework: (body: Record<string, unknown>) => apiFetch<ReturnRework>('/api/quality/reworks/', {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  updateRework: (id: number, body: Record<string, unknown>) => apiFetch<ReturnRework>(`/api/quality/reworks/${id}/`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  }),

  summary: (filters: Pick<QualityListFilters, 'date_from' | 'date_to'>) =>
    apiFetch<QualitySummary>(`/api/quality/summary/${queryString(filters)}`),
}

/**
 * New一期 workflow contract.  Kept separate from qualityApi so an older
 * server can continue serving the legacy tables while the UI falls back.
 */
export const qualityWorkflowApi = {
  listUnitWeights: (filters: QualityListFilters = {}) =>
    apiFetch<ApiList<QualityUnitWeight> | QualityUnitWeight[]>(`/api/quality/product-unit-weights/${queryString(filters)}`),
  createUnitWeight: (body: Partial<QualityUnitWeight>) => apiFetch<QualityUnitWeight>('/api/quality/product-unit-weights/', { method: 'POST', body: JSON.stringify(body) }),
  updateUnitWeight: (id: number | string, body: Partial<QualityUnitWeight>) => apiFetch<QualityUnitWeight>(`/api/quality/product-unit-weights/${id}/`, { method: 'PATCH', body: JSON.stringify(body) }),
  listProcessCards: (filters: QualityListFilters = {}) =>
    apiFetch<ApiList<QualityProcessCard> | QualityProcessCard[]>(`/api/quality/process-cards/${queryString(filters)}`),
  createProcessCard: (body: Record<string, unknown>) => apiFetch<QualityProcessCard>('/api/quality/process-cards/', { method: 'POST', body: JSON.stringify(body) }),
  updateProcessCard: (id: number | string, body: Record<string, unknown>) => apiFetch<QualityProcessCard>(`/api/quality/process-cards/${id}/`, { method: 'PATCH', body: JSON.stringify(body) }),
  scanProcessCard: (code: string) =>
    apiFetch<QualityProcessCardScanResult>(`/api/quality/process-cards/scan/${queryString({ code })}`),
  replaceProcessCard: (id: number | string, body: { new_card_no: string; notes?: string }) =>
    apiFetch<QualityProcessCard>(`/api/quality/process-cards/${id}/replace/`, { method: 'POST', body: JSON.stringify(body) }),
  listShipmentBatches: (filters: QualityListFilters = {}) => apiFetch<ApiList<QualityShipmentBatch> | QualityShipmentBatch[]>(`/api/quality/shipment-batches/${queryString(filters)}`),
  listShipmentCandidates: (filters: QualityListFilters = {}) =>
    apiFetch<ApiList<QualityShipmentCandidate> | QualityShipmentCandidate[]>(`/api/quality/shipment-batches/candidates/${queryString(filters)}`),
  previewShipmentAllocation: (body: { order_id: number; piece_quantity: number }) =>
    apiFetch<QualityShipmentAllocationPreview>('/api/quality/shipment-batches/allocation-preview/', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  checkShipmentNo: (shipmentNo: string, excludeId?: number | string) =>
    apiFetch<{ exists?: boolean; duplicate?: boolean; shipment?: QualityShipmentBatch }>(`/api/quality/shipment-batches/check-shipment-no/${queryString({ shipment_no: shipmentNo, exclude_id: excludeId })}`),
  getShipmentBatch: (id: number | string) => apiFetch<QualityShipmentBatch>(`/api/quality/shipment-batches/${id}/`),
  createShipmentBatch: (body: QualityShipmentBatchInput) =>
    apiFetch<QualityShipmentBatchResult>('/api/quality/shipment-batches/', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  updateShipmentBatch: (id: number | string, body: Record<string, unknown>) =>
    apiFetch<QualityShipmentBatch>(`/api/quality/shipment-batches/${id}/`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  confirmShipmentBatch: (id: number | string, processCardBindings: NonNullable<QualityShipmentBatchInput['process_card_bindings']> = []) => apiFetch<QualityShipmentBatch>(`/api/quality/shipment-batches/${id}/confirm/`, {
    method: 'POST',
    body: JSON.stringify(processCardBindings.length ? { process_card_bindings: processCardBindings } : {}),
  }),
  assignShipmentBatchInspectors: (id: number | string, inspectorIds: number[]) =>
    apiFetch<QualityShipmentBatch>(`/api/quality/shipment-batches/${id}/assign-inspectors/`, {
      method: 'POST',
      body: JSON.stringify({ inspector_ids: inspectorIds }),
    }),
  bindShipmentProcessCards: (id: number | string, cards: Array<{ shipment_unit_no: number; card_no: string; order_id?: number | null }>) =>
    apiFetch<QualityShipmentBatch>(`/api/quality/shipment-batches/${id}/bind-process-cards/`, {
      method: 'POST',
      body: JSON.stringify({ cards }),
    }),
  voidShipmentBatch: (id: number | string) => apiFetch<QualityShipmentBatch>(`/api/quality/shipment-batches/${id}/void/`, { method: 'POST', body: JSON.stringify({}) }),
  createAndConfirmShipmentBatch: async (body: QualityShipmentBatchInput) => {
    const { process_card_bindings: processCardBindings = [], ...createBody } = body
    const draft = await qualityWorkflowApi.createShipmentBatch(createBody as QualityShipmentBatchInput)
    if (!draft.id) throw new Error('出货批次创建成功但未返回批次 ID，无法确认')
    return qualityWorkflowApi.confirmShipmentBatch(draft.id, processCardBindings)
  },
  listReworkCases: (filters: QualityListFilters = {}) => apiFetch<ApiList<QualityReworkCase> | QualityReworkCase[]>(`/api/quality/rework-cases/${queryString(filters)}`),
  listReturnableBatches: (filters: Pick<QualityListFilters, 'q' | 'order_id' | 'page' | 'page_size'> = {}) =>
    apiFetch<ApiList<QualityReturnableBatch> | QualityReturnableBatch[]>(`/api/quality/rework-cases/returnable-batches/${queryString(filters)}`),
  createReworkCase: (body: Record<string, unknown>) => apiFetch<QualityReworkCase>('/api/quality/rework-cases/', { method: 'POST', body: JSON.stringify(body) }),
  scanReturn: (body: Record<string, unknown>) => apiFetch<QualityReworkCase>('/api/quality/rework-cases/scan-return/', { method: 'POST', body: JSON.stringify(body) }),
  bulkScanReturn: (body: Record<string, unknown>) => apiFetch<QualityReworkCase[] | { results?: QualityReworkCase[]; cases?: QualityReworkCase[] }>('/api/quality/rework-cases/bulk-scan-return/', { method: 'POST', body: JSON.stringify(body) }),
  updateReworkCase: (id: number | string, body: Record<string, unknown>) => apiFetch<QualityReworkCase>(`/api/quality/rework-cases/${id}/`, { method: 'PATCH', body: JSON.stringify(body) }),
  reshipReworkCase: (id: number | string, body: Record<string, unknown>) => apiFetch<QualityShipmentBatch>(`/api/quality/rework-cases/${id}/reship/`, { method: 'POST', body: JSON.stringify(body) }),
  listReworkAttempts: (filters: QualityListFilters = {}) => apiFetch<ApiList<QualityReworkAttempt> | QualityReworkAttempt[]>(`/api/quality/rework-attempts/${queryString(filters)}`),
  createReworkAttempt: (body: Record<string, unknown>) => apiFetch<QualityReworkAttempt>('/api/quality/rework-attempts/', { method: 'POST', body: JSON.stringify(body) }),
  updateReworkAttempt: (id: number | string, body: Record<string, unknown>) => apiFetch<QualityReworkAttempt>(`/api/quality/rework-attempts/${id}/`, { method: 'PATCH', body: JSON.stringify(body) }),
  listReworkTimeline: (processCardId: string | number) =>
    apiFetch<QualityReworkCase[]>(`/api/quality/process-cards/${processCardId}/rework-timeline/`),
  listReturnReasons: (filters: QualityListFilters = {}) =>
    apiFetch<ApiList<QualityReturnReason> | QualityReturnReason[]>(`/api/quality/defect-reasons/${queryString(filters)}`),
}

export interface AnalyticsFilters {
  month?: string
  date_from?: string
  date_to?: string
  group?: string
  machine_id?: number
}

export interface AnalyticsEntryFilters extends AnalyticsFilters {
  entry_type?: string
  direction?: string
  include_voided?: boolean
  page?: number
  page_size?: number
}

export const analyticsApi = {
  dashboard: (filters: AnalyticsFilters = {}) =>
    apiFetch<AnalyticsDashboard>(`/api/analytics/dashboard/${queryString(filters)}`),
  listManualEntries: (filters: AnalyticsEntryFilters = {}) =>
    apiFetch<ApiList<ManualPerformanceEntry> | ManualPerformanceEntry[]>(`/api/analytics/manual-entries/${queryString(filters)}`),
  createManualEntry: (body: Record<string, unknown>) => apiFetch<ManualPerformanceEntry>('/api/analytics/manual-entries/', {
    method: 'POST', body: JSON.stringify(body),
  }),
  updateManualEntry: (id: number, body: Record<string, unknown>) => apiFetch<ManualPerformanceEntry>(`/api/analytics/manual-entries/${id}/`, {
    method: 'PATCH', body: JSON.stringify(body),
  }),
  voidManualEntry: (id: number, reason = '用户作废') => apiFetch<ManualPerformanceEntry>(`/api/analytics/manual-entries/${id}/void/`, {
    method: 'POST', body: JSON.stringify({ void_reason: reason }),
  }),
  restoreManualEntry: (id: number) => apiFetch<ManualPerformanceEntry>(`/api/analytics/manual-entries/${id}/restore/`, { method: 'POST', body: JSON.stringify({}) }),
  listFinancialEntries: (filters: AnalyticsEntryFilters = {}) =>
    apiFetch<ApiList<ManualFinancialEntry> | ManualFinancialEntry[]>(`/api/analytics/financial-entries/${queryString(filters)}`),
  createFinancialEntry: (body: Record<string, unknown>) => apiFetch<ManualFinancialEntry>('/api/analytics/financial-entries/', {
    method: 'POST', body: JSON.stringify(body),
  }),
  updateFinancialEntry: (id: number, body: Record<string, unknown>) => apiFetch<ManualFinancialEntry>(`/api/analytics/financial-entries/${id}/`, {
    method: 'PATCH', body: JSON.stringify(body),
  }),
  voidFinancialEntry: (id: number, reason = '用户作废') => apiFetch<ManualFinancialEntry>(`/api/analytics/financial-entries/${id}/void/`, {
    method: 'POST', body: JSON.stringify({ void_reason: reason }),
  }),
  restoreFinancialEntry: (id: number) => apiFetch<ManualFinancialEntry>(`/api/analytics/financial-entries/${id}/restore/`, { method: 'POST', body: JSON.stringify({}) }),
}
