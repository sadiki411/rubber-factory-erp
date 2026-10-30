import type { OrderMaterialStatus } from './types'

export const ORDER_MATERIAL_META: Record<OrderMaterialStatus, { text: string; color: string }> = {
  UNKNOWN: { text: '未核算', color: 'default' },
  NOT_RECEIVED: { text: '未收到', color: 'error' },
  PARTIAL: { text: '未发够', color: 'warning' },
  SUFFICIENT: { text: '已发够', color: 'success' },
  // Accept cached responses from older releases without restoring a separate status.
  OVER: { text: '已发够', color: 'success' },
}

export const ORDER_MATERIAL_FILTER_OPTIONS = [
  { value: '', label: '全部胶料状态' },
  ...Object.entries(ORDER_MATERIAL_META)
    .filter(([value]) => value !== 'OVER')
    .map(([value, meta]) => ({ value, label: meta.text })),
]
