import { materialLayout, parameterNumber, parameterText } from './materialLayout'

describe('saved material layout display', () => {
  it('shows one mold total, independently of finished-product weights', () => {
    expect(materialLayout({ large_strip_weight_g: '21.00', large_strip_count: 5, small_strip_weight_g: '11.00', small_strip_count: 2 }).total).toBe(127)
    expect(materialLayout({ large_strip_weight_g: '21.25', large_strip_count: 6, small_strip_weight_g: '11.12', small_strip_count: 2 }).total).toBe(149.74)
  })

  it('allows a single strip layout without inventing a small-strip zero', () => {
    expect(materialLayout({ large_strip_weight_g: '21.00', large_strip_count: 5 }).total).toBe(105)
    expect(parameterNumber(undefined, 'g')).toBe('未填写')
  })

  it('distinguishes saved zero from missing and rejects malformed numbers', () => {
    expect(materialLayout({ large_strip_weight_g: '0.00', large_strip_count: 5, strip_weight_kg: '0.021', strips_per_batch: 5 }).total).toBe(0)
    expect(parameterNumber('0', 'g')).toBe('0 g')
    expect(parameterText(0)).toBe('0')
    expect(parameterNumber(null, 'g')).toBe('未填写')
    expect(parameterNumber('', 'g')).toBe('未填写')
    expect(parameterNumber('invalid', 'g')).toBe('数据待核对')
    expect(materialLayout({}).total).toBeUndefined()
  })

  it.each([
    { large_strip_weight_g: '21' },
    { large_strip_count: 5 },
    { large_strip_weight_g: '21', large_strip_count: 5, small_strip_count: 2 },
    { large_strip_weight_g: '21', large_strip_count: 5, small_strip_weight_g: '11' },
    { small_strip_weight_g: '11', small_strip_count: 2 },
    { large_strip_weight_g: 'invalid', large_strip_count: 5 },
    { large_strip_weight_g: '21', large_strip_count: 0 },
  ])('does not report a partial total for incomplete historical parameters: %j', (record) => {
    expect(materialLayout(record)).toMatchObject({ total: undefined, incomplete: true })
  })

  it('converts legacy kg for viewing without relabelling it as new strip fields', () => {
    expect(materialLayout({ strip_weight_kg: '0.021', strips_per_batch: 5 })).toEqual({ legacy: true, total: 105, incomplete: false })
    expect(materialLayout({ strip_weight_kg: '0', strips_per_batch: 5 }).total).toBe(0)
    expect(materialLayout({ strip_weight_kg: '0.021' })).toEqual({ legacy: true, total: undefined, incomplete: true })
    expect(materialLayout({ large_strip_weight_g: '2', large_strip_count: 5, strip_weight_kg: '0.021', strips_per_batch: 5 }).total).toBe(10)
  })
})
