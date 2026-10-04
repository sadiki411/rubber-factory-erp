const MOLD_RACK_PUBLIC_ORIGIN = 'https://erp.qfylagent.org'

/**
 * The QR code identifies the physical rack slot, not the mold currently in it.
 * Keeping the slot id in the URL means the printed label never changes when a
 * mold is moved in, out, or replaced.
 */
export function moldRackLocationDetailUrl(slotId: number | string) {
  return `${MOLD_RACK_PUBLIC_ORIGIN}/mold-rack/slots/${encodeURIComponent(String(slotId))}`
}

