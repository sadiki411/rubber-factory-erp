import { describe, expect, it } from 'vitest'
import { moldRackLocationDetailUrl, moldRackSlotIdFromUrl } from './moldRack'

describe('mold rack location QR URLs', () => {
  it('uses a stable slot id and keeps the detail route separate from inventory locations', () => {
    expect(moldRackLocationDetailUrl(42)).toBe('https://erp.qfylagent.org/mold-rack/slots/42')
    expect(moldRackLocationDetailUrl('007')).toBe('https://erp.qfylagent.org/mold-rack/slots/007')
  })

  it('accepts only the trusted public slot URL shape', () => {
    expect(moldRackSlotIdFromUrl('https://erp.qfylagent.org/mold-rack/slots/42')).toBe('42')
    expect(moldRackSlotIdFromUrl('/mold-rack/slots/007/')).toBe('007')
    expect(moldRackSlotIdFromUrl('https://erp.qfylagent.org/mold-rack/slots/42?mode=manage')).toBe('42')
    expect(moldRackSlotIdFromUrl('https://evil.example/mold-rack/slots/42')).toBeNull()
    expect(moldRackSlotIdFromUrl('https://erp.qfylagent.org/molds/42')).toBeNull()
  })
})
