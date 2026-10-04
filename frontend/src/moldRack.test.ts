import { describe, expect, it } from 'vitest'
import { moldRackLocationDetailUrl } from './moldRack'

describe('mold rack location QR URLs', () => {
  it('uses a stable slot id and keeps the detail route separate from inventory locations', () => {
    expect(moldRackLocationDetailUrl(42)).toBe('https://erp.qfylagent.org/mold-rack/slots/42')
    expect(moldRackLocationDetailUrl('007')).toBe('https://erp.qfylagent.org/mold-rack/slots/007')
  })
})
