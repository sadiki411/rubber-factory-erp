export type WorkbenchKind = 'ORDER_SHORTAGE' | 'RETURN_PENDING' | 'INVENTORY_QUALITY' | 'RECEIPT_UNLINKED'
export interface WorkbenchTask {
  id: string
  kind: WorkbenchKind
  title: string
  document_no: string
  specification: string
  material: string
  quantity: number | string | null
  unit: string
  due_date: string | null
  order_id: number | null
  owner: string | null
  reason: string
  source: string
  url: string
}
export interface WorkbenchResult {
  count: number
  page: number
  page_size: number
  results: WorkbenchTask[]
  summary: Record<WorkbenchKind, number>
  summary_scope: string
  generated_at: string
  limitations: string[]
}
export type OrderTraceSection = 'production' | 'receipts' | 'cards' | 'shipments' | 'piece_shipments' | 'returns' | 'piece_returns' | 'inventory'
export interface OrderTraceRow {
  id: number
  source: string
  document_no: string
  date: string | null
  status: string
  physical_quantity: number | string | null
  own_quantity: number | string | null
  unit: string
  details: string
  cards: string[]
  url: string | null
}
export interface OrderOverviewResult {
  order: { id: number; order_no: string; item_no: string; specification: string; material: string; product_name: string; status: string; due_date: string | null; order_quantity: number }
  summary: { net_delivered: number; delivery_remaining: number; production_progress_quantity: number; production_status: string }
  counts: Record<OrderTraceSection, number>
  section: OrderTraceSection
  count: number
  page: number
  page_size: number
  results: OrderTraceRow[]
  generated_at: string
  limitations: string[]
}
export const TRACE_SECTIONS: { key: OrderTraceSection; label: string }[] = [
  { key: 'production', label: '生产任务' }, { key: 'receipts', label: '关联发料' },
  { key: 'cards', label: '来源流程卡' }, { key: 'shipments', label: '重量/卡出货' },
  { key: 'piece_shipments', label: '件数出货' }, { key: 'returns', label: '退回/再发' },
  { key: 'piece_returns', label: '件数退回' }, { key: 'inventory', label: '明确引用的出库' },
]
