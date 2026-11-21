import { apiFetch } from './api/client'

type Condition = { raw: string; state: string; time_lower?: string; time_upper?: string; temperature_lower?: string; temperature_upper?: string; unit?: string; corrected_unit?: boolean }
export type CustomerInspectionGroup = {
  customer_code: string; project_no: string; category: string; version: string
  rows: { item: string; lower: string; upper: string; unit: string; file: string; batch: string; sheet: string; row: number | null }[]
}
export type CustomerInspection = {
  order_id: number; order_no: string; item_no: string; customer_code: string; project_no: string; source: string; supplier: string
  status: 'PENDING' | 'CONFLICT' | 'CONFIRMED' | 'INHERITED' | 'MISSING'
  digest: string; groups: CustomerInspectionGroup[]; problems: string[]; can_review?: boolean
  available?: { id: number; customer_code: string; project_no: string; versions: {category: string; version: string}[] }[]
  changes?: { category: string; item: string; previous: [string,string,string,string] | null; incoming: [string,string,string,string] | null }[]
  reference: { primary: Condition; secondary: Condition; mold_no: string; mold_size: string; material_length: string; cut_weight: string }
}
type ProductReview = Pick<CustomerInspection, 'order_id' | 'order_no' | 'item_no' | 'customer_code' | 'project_no' | 'groups'> & {status: string; key: string}

const path = (id: number) => `/api/orders/orders/${id}/customer-inspection/`
export const customerInspectionApi = {
  get: (id: number) => apiFetch<{inspection: CustomerInspection | null}>(path(id)),
  print: (id: number) => `${path(id)}print/`,
  update: (id: number, data: {action: 'confirm' | 'inherit'; digest?: string; review_id?: number}) =>
    apiFetch<{inspection: CustomerInspection}>(path(id), {method: 'POST', body: JSON.stringify(data)}),
  product: (id: number, page = 1) => apiFetch<{results: ProductReview[]; has_next?: boolean}>(`/api/orders/product-specifications/${id}/customer-inspection/?page=${page}`),
}

export function conditionText(data: Condition) {
  if (data.state === 'NOT_REQUIRED') return '不需要二烤'
  if (data.state !== 'REFERENCE') return data.raw ? `${data.raw}（待核）` : '原表未提供'
  const span = (a?: string, b?: string) => a === b ? a : `${a}–${b}`
  return `${span(data.time_lower, data.time_upper)} ${data.unit} / ${span(data.temperature_lower, data.temperature_upper)} °C${data.corrected_unit ? '（原表单位误写，按秒读取，数字不换算）' : ''}`
}
