import { describe, expect, it } from 'vitest'
import { inventoryLocationDetailUrl, isInventoryLocationCode, normalizeInventoryLocationCode } from './inventory'

describe('inventory location scan values', () => {
  it('accepts scanner text and printed detail URLs', () => {
    expect(normalizeInventoryLocationCode('k01-l04-p05')).toBe('K01-L04-P05')
    expect(normalizeInventoryLocationCode('https://erp.qfylagent.org/inventory/locations/K09-L05-P02')).toBe('K09-L05-P02')
    expect(isInventoryLocationCode('K09-L05-P02')).toBe(true)
    expect(isInventoryLocationCode('2A-6-2-2')).toBe(false)
  })

  it('prints a stable production URL in every QR label', () => {
    expect(inventoryLocationDetailUrl('K01-L01-P01')).toBe('https://erp.qfylagent.org/inventory/locations/K01-L01-P01')
  })
})
