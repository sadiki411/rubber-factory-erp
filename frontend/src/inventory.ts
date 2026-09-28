const LOCATION_CODE_PATTERN = /^K\d{2}-L\d{2}-P\d{2}$/
export const INVENTORY_PUBLIC_ORIGIN = 'https://erp.qfylagent.org'

export function normalizeInventoryLocationCode(rawValue: string | null | undefined) {
  const raw = String(rawValue || '').trim()
  if (!raw) return ''
  try {
    const url = new URL(raw)
    const queryValue = url.searchParams.get('location') || url.searchParams.get('code')
    if (queryValue?.trim()) return queryValue.trim().toUpperCase()
    const lastSegment = decodeURIComponent(url.pathname.split('/').filter(Boolean).at(-1) || '')
    if (lastSegment) return lastSegment.trim().toUpperCase()
  } catch {
    // Scanner guns normally provide the plain location code.
  }
  return raw.toUpperCase()
}

export function isInventoryLocationCode(value: string | null | undefined) {
  return LOCATION_CODE_PATTERN.test(normalizeInventoryLocationCode(value))
}

export function inventoryLocationDetailUrl(code: string) {
  return `${INVENTORY_PUBLIC_ORIGIN}/inventory/locations/${encodeURIComponent(normalizeInventoryLocationCode(code))}`
}
