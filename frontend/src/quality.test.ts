import {
  isHighReworkCount,
  reworkQuantitiesValid,
  shipmentQuantitiesMatch,
  shipmentQuantityAllowed,
} from './quality'

describe('quality quantity validation', () => {
  it('reconciles inspection quantities', () => {
    expect(shipmentQuantitiesMatch(100, 96, 4)).toBe(true)
    expect(shipmentQuantitiesMatch(100, 96, 5)).toBe(false)
  })

  it('does not allow shipping more than qualified quantity', () => {
    expect(shipmentQuantityAllowed(96, 96)).toBe(true)
    expect(shipmentQuantityAllowed(97, 96)).toBe(false)
  })

  it('keeps rework disposition inside returned and reworked quantities', () => {
    expect(reworkQuantitiesValid(20, 18, 16, 2)).toBe(true)
    expect(reworkQuantitiesValid(20, 21, 16, 2)).toBe(false)
    expect(reworkQuantitiesValid(20, 18, 17, 2)).toBe(false)
  })

  it('raises a warning only after three reworks', () => {
    expect(isHighReworkCount(3)).toBe(false)
    expect(isHighReworkCount(4)).toBe(true)
  })
})
