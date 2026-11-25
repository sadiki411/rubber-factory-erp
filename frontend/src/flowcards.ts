import { apiFetch, ApiError } from './api/client'
import type { CustomerInspection } from './customerInspection'

export type PaperMaterial = { actual_material_length?: string; weight_g?: string; weight_source?: string; length_source?: string; weight_reference?: string; large_strip_weight_g?: string; small_strip_weight_g?: string }

export type FlowSnapshot = {
  product: { id: number; name?: string; specification: string; material: string; customer_product_no?: string }
  orders: Array<{ id: number; order_no: string; item_no: string; customer?: string; customer_code?: string; customer_project_no?: string }>
  customer_inspections?: CustomerInspection[]
  run_id?: number | null; mold_id?: number | null
  effective_cavities?: number | null; confirmed_total_cavities?: number | null
  diagram_id?: number | null; whole_mold: string
  paper_material?: PaperMaterial
  whole_mold_basis?: string; cavities_basis?: string; material_evidence?: string; requirements_review?: string; appearance?: Array<{ id: number; name: string }>
  template: { version: string | number }
  process_overrides?: Record<string, Record<string, unknown>>
  card_molds?: number[]
  plan_source?: string
  append_confirmed?: boolean
  temperature_plan?: {mode:string;basis?:string;reference?:string}
  guidance?: PreparationGuidance[]
  process_parameters?: Record<string,{state?:string;value?:string;lower?:string;upper?:string;unit?:string;raw_text?:string}>
  standard?: { id: number; version: number; source: string; customer_key?:string; snapshot: { criteria: Criterion[]; hardness?: Record<string, { object: string; scale?: string }>; bake?: Record<string, unknown>; dimension_count?: number | null;process_parameters?:FlowSnapshot['process_parameters']; packaging?: {molds_per_card?:number; machine_settings?: Array<{station_id?:number|string; station_code?:string; lower?:string; upper?:string; unit?:string}>} } } | null
}
export type Criterion = { id: number; name: string; stage: string; lower: string; upper: string; unit: string; method: string; numeric?: boolean; limit_mode?: 'UPPER' | 'LOWER' | 'RANGE' | 'PENDING' | 'NONE'; upper_inclusive?: boolean; lower_inclusive?: boolean; requirement_state?: string; follow_package?: boolean; critical?: boolean; provenance?: Record<string, unknown>; customer_item?: string }
export const applicableFlowCriteria = (criteria: Criterion[] = [], stage?: string) => criteria.filter((c) => c.limit_mode !== 'NONE' && c.follow_package !== false && (!stage || c.stage === stage))
export function flowLimitText(c: Partial<Criterion>) {
  const mode = c.limit_mode || (c.numeric ? 'RANGE' : 'PENDING')
  if (mode === 'NONE') return '不要求测'
  if (mode === 'PENDING') return '界限待确认'
  const unit = c.unit || '单位待确认'
  if (mode === 'UPPER') return `${c.upper_inclusive === false ? '<' : '≤'} ${c.upper} ${unit}`
  if (mode === 'LOWER') return `${c.lower_inclusive === false ? '>' : '≥'} ${c.lower} ${unit}`
  return c.lower_inclusive === false || c.upper_inclusive === false ? `${c.lower_inclusive === false ? '(' : '['}${c.lower}, ${c.upper}${c.upper_inclusive === false ? ')' : ']'} ${unit}` : `${c.lower}～${c.upper} ${unit}`
}
export type TemplateStatus = { renderer: string; version: string | number; published: boolean; write_enabled: boolean; history: Array<{ renderer: string; version: number; confirmation: string; created_at: string }> }
export type FlowGroup = { id: string; product_id: number; run_id: number | null; planned_molds: number | null; molds_per_card: number; whole_mold: string; plan_basis: string; status: string; snapshot: FlowSnapshot; request_key: string }
export type PreparationGroup = FlowGroup & { revision: number; range_start: number | null; range_end: number | null; updated_at: string; updated_by: number | null }
export type PreparationStandard = FlowContext['standards'][number] & { status: string; customer_key: string; source_kind: string; applicable_order_id: number | null; scope: string; snapshot: FlowContext['standards'][number]['snapshot'] & { import_source?: Record<string, unknown>; customer_reference?: {key:string}; applicability?: Record<string, unknown>; route?: Record<string, boolean>; packaging?: {molds_per_card?: number; patrol_every_molds?: number; temperature_plan?:FlowSnapshot['temperature_plan']}; process_parameters?: Record<string, {state: string; lower?: string; upper?: string; value?: string; unit?: string; source?: string; raw_text?: string}> } }
export type PreparationSource = FlowContext['criteria'][number] & { order_id: number | null; customer: string; category?:string; source_file: string; source_url?: string | null; source_batch_id: string | null; source_sheet: string; source_row: number | null; version: string; stage?: string; raw_data?: Record<string, unknown>; customer_order_source?: boolean }
export type PreparationContext = Omit<FlowContext, 'standards' | 'criteria' | 'existing_groups'> & {
  guidance?: PreparationGuidance[]
  standard_candidates?: Array<{id:number;version:number;customer_key:string;source:string;source_kind:string;guidance:PreparationGuidance[]}>
  original_reference_reviews?: Record<string,PreparationGuidance[]>
  order_plan?: boolean
  preparation_enabled: true; can_maintain_standard: boolean; standards: PreparationStandard[]; criteria: PreparationSource[]; existing_groups: PreparationGroup[]
  runs: Array<{id: number; segment_no: number; status: string; status_display: string; station_id: number | null; mold_id: number | null; planned_molds: number; historical: boolean}>
  available_ranges: number[][]; occupied_ranges: Array<{id: string; range_start: number; range_end: number; status: string}>; preferred_group: PreparationGroup | null
  missing: string[]; auto_standard_id: number | null; conflicts: Array<{key: string; name: string; stage: string; sources: PreparationSource[]; selected_ids: number[]; missing_order_ids: number[]}>; suggestion_id: number | null
  criteria_count?: number
  paper_material_defaults?: PaperMaterial
}
export type PreparationGuidance = {key:string;label:string;value:string;state:string;instruction?:string;warning?:string;raw_reference?:string;owner:string}
export type PlanPreview = {count:number;tail_molds:number|null;planned_molds:number|null;card_molds?:number[];snapshot?:{plan_basis?:string}}
export function splitPlannedMolds(total: number | null | undefined, per: number) {
  if (!Number.isInteger(total) || !total || total < 1 || !Number.isInteger(per) || per < 1 || Math.ceil(total / per) > 500) return []
  return Array.from({length: Math.ceil(total / per)}, (_, i) => Math.min(per, total - i * per))
}
export type FlowCard = { id: string; card_no: string; group_id: string; code: string; sequence: number; denominator: number; planned_molds: number; original_date: string; printed_at?: string | null; pending_print_job_id?: string | null; has_usage?: boolean | null; reprint_count: number; status: string; process_todos?: Array<{stage:string;label:string;role:string;state:string;notice:string;employees:string[];fact_ids:string[]}> }
export type FlowCardDetail = FlowCard & { snapshot: FlowSnapshot; write_enabled?: boolean; can_add_work_guidance?:boolean; whole_mold_status: string; unresolved_anomalies: number; source_status: string; paper_status: string; measurement_alerts?: Array<FlowEvent & { review_target?: FlowEvent }>; measurement_status?: Array<{ criterion: Criterion; limit_text: string; message: string; event: FlowEvent | null }>; bake_measurement_status?: Array<{id: string; business_at: string | null; scope: string; trays?: number[]; notice: string; checks: Array<{sample_ref: string; material_ref: string; trays: number[]; message: string; actual: string | null; unit: string | null}>}>; baked_product_measurements?: FlowEvent[]; baked_measurement_notice?: string }
export type FlowEvent = { id: string; kind: string; values: Record<string, unknown>; scope: string; card_ids?: string[]; card_refs?: Array<{ id: string; card_no: string }>; followups?: Array<{ id: string; employee_name: string; business_at: string | null; message: string; judgment: string }>; employee_name: string; recorded_by?:number; evidence: string; created_at: string; business_at: string | null; replaces_id: string | null }
export type FlowPage<T> = { count: number; results: T[] }
export type FlowReturn = { id: number; case_no: string; status: string; return_round: number | null; reshipment_batch_id: number | null }
export type FlowTrace = { id: number; batch_id: number; shipment_no: string; current_shipment_status: string; state: string; scope: string; related_returns: FlowReturn[]; related_return_count: number }
export type PaperLayout = 'A4_TWO_UP' | 'SINGLE'
export const DEFAULT_PAPER_LAYOUT: PaperLayout = 'A4_TWO_UP'
export const PAPER_LAYOUT_LABEL: Record<PaperLayout, string> = { A4_TWO_UP: 'A4 双卡打印', SINGLE: '原尺寸单卡' }
export type PrintJob = { id: string; status: string; request_key: string; pdf_url: string; file_prepared: boolean; pdf_sha256: string; second_inspection?:boolean; paper_layout?: PaperLayout; items: Array<{ card_id: string; group_id?: string; card_no: string; sequence: number; denominator: number; proposed_reprint: number; status: string; active: boolean }> }
export type FlowContext = {
  preparation_enabled?: boolean
  write_enabled: boolean; product: FlowSnapshot['product']; run_id: number | null; mold_id: number | null; order_ids: number[]; orders: FlowSnapshot['orders']; suggested_molds: number | null
  standards: Array<{ id: number; version: number; whole_mold: string; source: string; snapshot: { criteria: Criterion[]; appearance?: Array<{ id: number; name: string }>; hardness?: Record<string, { object: string; scale?: string }>; bake?: Record<string, unknown>; dimension_count?: number | null } }>
  requirement_warnings?: string[]
  raw_process_reference?: { primary_curing: string; secondary_curing: string; total_cavities: string; effective_cavities: string }
  existing_groups: FlowGroup[]; appearance: Array<{ id: number; name: string }>
  criteria: Array<{ id: number; inspection_item: string; lower_limit: string; upper_limit: string; unit: string; method?: string; limit_mode?: Criterion['limit_mode']; upper_inclusive?: boolean; lower_inclusive?: boolean }>
  diagrams: Array<{ id: number; version: number; direction: string }>
}
export type InternalSource = { card_id: string; request_key: string; scope: 'SET_UNKNOWN' | 'WHOLE' | 'PARTIAL'; evidence: string; raw_code: string; shipment_unit_no?: number }
export function isInternalCardCode(code: string) { return /^(ERPFC:|FC-)/i.test(code.trim()) }
const post = <T,>(path: string, body: unknown) => apiFetch<T>(`/api/flow-cards/${path}`, { method: 'POST', body: JSON.stringify(body) })
async function previewPdf(path: string, body: unknown) {
  const csrf = document.cookie.split('; ').find(v => v.startsWith('csrftoken='))?.split('=').slice(1).join('=') || ''
  const response = await fetch(`/api/flow-cards/${path}`, { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': decodeURIComponent(csrf) }, body: JSON.stringify(body) })
  if (!response.ok) {
    const data = await response.json().catch(() => null)
    throw new ApiError(response.status, String(data?.detail || Object.values(data || {})[0] || '纸卡预览生成失败'), data)
  }
  if (!response.headers.get('Content-Type')?.includes('application/pdf')) throw new Error('未收到有效的预览PDF，请核对登录后重试。')
  return URL.createObjectURL(await response.blob())
}
export const flowApi = {
  importStandards: (file: File) => { const body = new FormData(); body.append('file', file); return apiFetch<StandardImportPreview>('/api/flow-cards/standards/import-preview/', { method: 'POST', body }) },
  context: (params: URLSearchParams) => apiFetch<FlowContext>(`/api/flow-cards/context/?${params}`),
  groups: (params: URLSearchParams, page = 1, q = '') => apiFetch<FlowPage<FlowGroup>>(`/api/flow-cards/groups/?${params}&page=${page}&q=${encodeURIComponent(q)}`),
  standard: (body: unknown) => post('standards/', body),
  saveStandard: (body: unknown) => post<PreparationStandard>('standards/', body),
  activateStandard: (id: number) => post<PreparationStandard>(`standards/${id}/activate/`, {}),
  preparationSources: (params: URLSearchParams, page = 1) => apiFetch<FlowPage<PreparationSource>>(`/api/flow-cards/preparation-sources/?${params}&page=${page}&page_size=50`),
  appearance: (body: unknown) => post('appearance/', body),
  preview: (body: unknown) => post<PlanPreview>('groups/preview/', body),
  sample: (body: unknown) => previewPdf('groups/sample/', body),
  create: (body: unknown) => post<FlowGroup>('groups/', body),
  generate: (id: string, expectedRevision?: number) => post<FlowGroup>(`groups/${id}/generate/`, expectedRevision == null ? {} : {expected_revision:expectedRevision}),
  revise: (id: string, body: unknown) => post<FlowGroup>(`groups/${id}/revise/`, body),
  group: (id: string) => apiFetch<FlowGroup>(`/api/flow-cards/groups/${id}/`),
  cards: (id: string, page = 1) => apiFetch<FlowPage<FlowCard>>(`/api/flow-cards/groups/${id}/cards/?page=${page}&page_size=100`),
  card: (id: string) => apiFetch<FlowCardDetail>(`/api/flow-cards/cards/${id}/`),
  scan: (code: string) => apiFetch<FlowCard & { url: string; product: FlowSnapshot['product'] }>(`/api/flow-cards/cards/scan/?code=${encodeURIComponent(code)}`),
  events: (id: string, page: number) => apiFetch<FlowPage<FlowEvent>>(`/api/flow-cards/cards/${id}/events/?page=${page}`),
  bakeSamples: (id: string, page = 1) => apiFetch<FlowPage<FlowEvent>>(`/api/flow-cards/cards/${id}/bake-samples/?page=${page}`),
  record: (body: unknown, entryCard?: string) => post<FlowEvent>(entryCard ? `cards/${entryCard}/record/` : 'events/', body),
  attachments: (id: string, page = 1) => apiFetch<FlowPage<{ id: string; url: string; original_name: string; replaces_id: string | null }>>(`/api/flow-cards/cards/${id}/attachments/?page=${page}`),
  upload: (body: FormData) => apiFetch<{ id: string }>('/api/flow-cards/attachments/', { method: 'POST', body }),
  diagram: (body: unknown) => post('diagrams/', body),
  diagramDetail: (id: number) => apiFetch<{ url: string; version: number; direction: string; scope: string }>(`/api/flow-cards/diagrams/?id=${id}`),
  trace: (id: string, page: number) => apiFetch<FlowPage<FlowTrace>>(`/api/flow-cards/cards/${id}/trace/?page=${page}`),
  traceReturns: (id: string, source: number, page: number) => apiFetch<FlowPage<FlowReturn>>(`/api/flow-cards/cards/${id}/trace-returns/?source_id=${source}&page=${page}`),
  print: (body: unknown) => post<PrintJob>('print-jobs/', body),
  printPreview: (body: unknown) => previewPdf('print-jobs/preview/', body),
  prints: (cardId: string, page = 1) => apiFetch<FlowPage<PrintJob>>(`/api/flow-cards/print-jobs/?card_id=${cardId}&page=${page}`),
  pendingPrints: (groupId: string, page = 1) => apiFetch<FlowPage<PrintJob>>(`/api/flow-cards/print-jobs/?group_id=${groupId}&pending=1&page_size=20&page=${page}`),
  printJob: (id: string) => apiFetch<PrintJob>(`/api/flow-cards/print-jobs/${id}/`),
  printResult: (id: string, outcomes: Record<string, string>) => post<PrintJob>(`print-jobs/${id}/confirm/`, { outcomes }),
  preparePrint: (id: string) => post<PrintJob>(`print-jobs/${id}/prepare/`, {}),
  templates: () => apiFetch<TemplateStatus>('/api/flow-cards/templates/publish/'),
  publish: (renderer: string, confirmation: string) => post('templates/publish/', { renderer, confirmation, print_confirmed: true }),
  sources: (batchId: number | string, page = 1) => apiFetch<FlowPage<{ id: number; card_id: string; card_no: string; state: string; scope: string }>>(`/api/flow-cards/shipments/${batchId}/sources/?page=${page}`),
  reviewSource: (id: number, body: unknown) => post(`shipment-sources/${id}/review/`, body),
}

export type StandardImportCandidate = {sheet: string; source: string; customer_key?: string; customer_product_no: string; specification?: string; material?: string; mold_no?: string; version?: string; base_version?: string; order_no?: string; item_no?: string; scope: string; criteria: Array<Partial<Criterion> & {name: string; stage: string}>; warnings: string[]; hardness?: Record<string, Record<string, string>>; bake?: Record<string, unknown>; process_parameters?: Record<string, Record<string, unknown>>; route?: Record<string, boolean>; raw_rows?: string[][]; import_source?: Record<string, unknown>}
export type StandardImportPreview = {candidates: StandardImportCandidate[]; notice: string; filename: string}

/** Survives a reload/timeout; clear only after the server returns a known result. */
export function flowRequestKey(slot: string, body: unknown) {
  const storageKey = `erp-flow-request:${slot}`
  const signature = JSON.stringify(body)
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey) || 'null') as { signature: string; key: string } | null
    if (saved?.signature === signature) return saved.key
  } catch { /* malformed local state must never become a business result */ }
  const key = crypto.randomUUID()
  sessionStorage.setItem(storageKey, JSON.stringify({ signature, key }))
  return key
}
export const finishFlowRequest = (slot: string) => sessionStorage.removeItem(`erp-flow-request:${slot}`)
