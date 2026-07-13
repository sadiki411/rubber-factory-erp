import { moldLocation } from './types'
import type { MoldAsset } from './types'

const base = {
  id: 1,
  asset_code: 'MJ-001',
  mold_model: { id: 1, code: 'A', product_name: '产品A' },
} as MoldAsset

describe('moldLocation', () => {
  it('returns exact slot for in-stock mold', () => {
    expect(moldLocation({ ...base, status: 'IN_STOCK', slot: { id: 2, display_code: 'J01-L01-A-P01' } })).toBe('J01-L01-A-P01')
  })

  it('returns machine for on-machine mold', () => {
    expect(moldLocation({ ...base, status: 'ON_MACHINE', machine: { id: 3, code: 'MC-03', name: '3号机' } })).toBe('MC-03 · 3号机')
  })

  it('shows customer returned for an outsourced mold', () => {
    expect(moldLocation({ ...base, status: 'OUTSOURCED', processor: { id: 4, code: 'P01', name: '外协厂' } })).toBe('客户收回')
  })
})
