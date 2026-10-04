export const MOLD_RACK_PUBLIC_ORIGIN = 'https://erp.qfylagent.org'

/**
 * The QR code identifies the physical rack slot, not the mold currently in it.
 * Keeping the slot id in the URL means the printed label never changes when a
 * mold is moved in, out, or replaced.
 */
export function moldRackLocationDetailUrl(slotId: number | string) {
  return `${MOLD_RACK_PUBLIC_ORIGIN}/mold-rack/slots/${encodeURIComponent(String(slotId))}`
}

/** Return the fixed slot id carried by a printed mold-rack QR code. */
export function moldRackSlotIdFromUrl(value: string | null | undefined) {
  const text = String(value || '').trim()
  if (!text) return null
  try {
    const url = new URL(text, MOLD_RACK_PUBLIC_ORIGIN)
    const pathMatch = url.pathname.match(/^\/mold-rack\/slots\/(\d+)\/?$/)
    if (!pathMatch || url.origin !== MOLD_RACK_PUBLIC_ORIGIN) return null
    return pathMatch[1]
  } catch {
    return null
  }
}

