import { apiFetch } from './api/client'

export type FlowSnapshot = {
  product: { id: number; name?: string; specification: string; material: string }
  orders: Array<{ id: number; order_no: string; item_no: string; customer?: string }>
  run_id?: number | null; mold_id?: number | null
  effective_cavities?: number | null; confirmed_total_cavities?: number | null
  diagram_id?: number | null; whole_mold: string
  whole_mold_basis?: string; cavities_basis?: string; material_evidence?: string; requirements_review?: string; appearance?: Array<{ id: number; name: string }>
  template: { version: string | number }
  standard?: { id: number; version: number; source: string; snapshot: { criteria: Criterion[] } } | null
}
export type Criterion = { id: number; name: string; stage: string; lower: string; upper: string; unit: string; method: string }
export type FlowGroup = { id: string; product_id: number; run_id: number | null; planned_molds: number | null; molds_per_card: number; whole_mold: string; plan_basis: string; status: string; snapshot: FlowSnapshot; request_key: string }
export type FlowCard = { id: string; card_no: string; group_id: string; code: string; sequence: number; denominator: number; planned_molds: number; original_date: string; reprint_count: number; status: string }
export type FlowCardDetail = FlowCard & { snapshot: FlowSnapshot; write_enabled?: boolean; whole_mold_status: string; unresolved_anomalies: number; source_status: string; paper_status: string }
export type FlowEvent = { id: string; kind: string; values: Record<string, unknown>; scope: string; employee_name: string; evidence: string; created_at: string; business_at: string | null; replaces_id: string | null }
export type FlowPage<T> = { count: number; results: T[] }
export type FlowReturn = { id: number; case_no: string; status: string; return_round: number | null; reshipment_batch_id: number | null }
export type FlowTrace = { id: number; batch_id: number; shipment_no: string; current_shipment_status: string; state: string; scope: string; related_returns: FlowReturn[]; related_return_count: number }
export type PrintJob = { id: string; status: string; request_key: string; pdf_url: string; file_prepared: boolean; pdf_sha256: string; items: Array<{ card_id: string; card_no: string; sequence: number; denominator: number; proposed_reprint: number; status: string; active: boolean }> }
export type FlowContext = {
  write_enabled: boolean; product: FlowSnapshot['product']; run_id: number | null; mold_id: number | null; order_ids: number[]; orders: FlowSnapshot['orders']; suggested_molds: number | null
  standards: Array<{ id: number; version: number; whole_mold: string; source: string; snapshot: { criteria: Criterion[]; appearance?: Array<{ id: number; name: string }> } }>
  requirement_warnings?: string[]
  raw_process_reference?: { primary_curing: string; secondary_curing: string; total_cavities: string; effective_cavities: string }
  existing_groups: FlowGroup[]; appearance: Array<{ id: number; name: string }>
  criteria: Array<{ id: number; inspection_item: string; lower_limit: string; upper_limit: string; unit: string }>
  diagrams: Array<{ id: number; version: number; direction: string }>
}
export type InternalSource = { card_id: string; request_key: string; scope: 'SET_UNKNOWN' | 'WHOLE' | 'PARTIAL'; evidence: string; raw_code: string; shipment_unit_no?: number }
export function isInternalCardCode(code: string) { return /^(ERPFC:|FC-)/i.test(code.trim()) }
const post = <T,>(path: string, body: unknown) => apiFetch<T>(`/api/flow-cards/${path}`, { method: 'POST', body: JSON.stringify(body) })
export const flowApi = {
  context: (params: URLSearchParams) => apiFetch<FlowContext>(`/api/flow-cards/context/?${params}`),
  standard: (body: unknown) => post('standards/', body),
  appearance: (body: unknown) => post('appearance/', body),
  preview: (body: unknown) => post<{ count: number; tail_molds: number | null; planned_molds: number | null }>('groups/preview/', body),
  create: (body: unknown) => post<FlowGroup>('groups/', body),
  generate: (id: string) => post<FlowGroup>(`groups/${id}/generate/`, {}),
  revise: (id: string, body: unknown) => post<FlowGroup>(`groups/${id}/revise/`, body),
  group: (id: string) => apiFetch<FlowGroup>(`/api/flow-cards/groups/${id}/`),
  cards: (id: string, page = 1) => apiFetch<FlowPage<FlowCard>>(`/api/flow-cards/groups/${id}/cards/?page=${page}&page_size=100`),
  card: (id: string) => apiFetch<FlowCardDetail>(`/api/flow-cards/cards/${id}/`),
  scan: (code: string) => apiFetch<FlowCard & { url: string; product: FlowSnapshot['product'] }>(`/api/flow-cards/cards/scan/?code=${encodeURIComponent(code)}`),
  events: (id: string, page: number) => apiFetch<FlowPage<FlowEvent>>(`/api/flow-cards/cards/${id}/events/?page=${page}`),
  record: (body: unknown) => post<FlowEvent>('events/', body),
  attachments: (id: string, page = 1) => apiFetch<FlowPage<{ id: string; url: string; original_name: string }>>(`/api/flow-cards/cards/${id}/attachments/?page=${page}`),
  upload: (body: FormData) => apiFetch<{ id: string }>('/api/flow-cards/attachments/', { method: 'POST', body }),
  diagram: (body: unknown) => post('diagrams/', body),
  diagramDetail: (id: number) => apiFetch<{ url: string; version: number; direction: string; scope: string }>(`/api/flow-cards/diagrams/?id=${id}`),
  trace: (id: string, page: number) => apiFetch<FlowPage<FlowTrace>>(`/api/flow-cards/cards/${id}/trace/?page=${page}`),
  traceReturns: (id: string, source: number, page: number) => apiFetch<FlowPage<FlowReturn>>(`/api/flow-cards/cards/${id}/trace-returns/?source_id=${source}&page=${page}`),
  print: (body: unknown) => post<PrintJob>('print-jobs/', body),
  prints: (cardId: string, page = 1) => apiFetch<FlowPage<PrintJob>>(`/api/flow-cards/print-jobs/?card_id=${cardId}&page=${page}`),
  printResult: (id: string, outcomes: Record<string, string>) => post<PrintJob>(`print-jobs/${id}/confirm/`, { outcomes }),
  preparePrint: (id: string) => post<PrintJob>(`print-jobs/${id}/prepare/`, {}),
  publish: (confirmation: string) => post('templates/publish/', { confirmation }),
  sources: (batchId: number | string, page = 1) => apiFetch<FlowPage<{ id: number; card_id: string; card_no: string; state: string; scope: string }>>(`/api/flow-cards/shipments/${batchId}/sources/?page=${page}`),
  reviewSource: (id: number, body: unknown) => post(`shipment-sources/${id}/review/`, body),
}

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
