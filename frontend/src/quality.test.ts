import {
  expectedWeightKg,
  isHighReworkCount,
  normalizeWeightKg,
  orderUnitWeightG,
  piecesPerBatchFromTotal,
  piecesFromBatchCount,
  piecesFromWeight,
  processCardQuantityUpperLimit,
  repeatedBatchNetWeightKg,
  resolvedProcessCardReworkCount,
  reworkQuantitiesValid,
  shipmentPieceQuantity,
  shipmentQuantitiesMatch,
  shipmentQuantityAllowed,
  shipmentQuantityWithinFlowCardLimit,
  weightUpperLimitKg,
  weightVariancePercent,
  weightWithinUpperLimit,
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

  it('does not double count return cases already included by the process-card API', () => {
    expect(resolvedProcessCardReworkCount({ rework_count: 2 } as any, 2)).toBe(2)
    expect(resolvedProcessCardReworkCount({} as any, 2)).toBe(2)
  })

  it('calculates flow-card weight without mixing in material issue weight', () => {
    expect(expectedWeightKg(907, 18)).toBeCloseTo(16.326)
    expect(weightUpperLimitKg(16.326, 10)).toBeCloseTo(17.9586)
    expect(weightVariancePercent(17.3, 16.326)).toBeCloseTo(5.97, 1)
    expect(weightWithinUpperLimit(17.3, 16.326, 10)).toBe(true)
    expect(weightWithinUpperLimit(18.1, 16.326, 10)).toBe(false)
  })

  it('derives whole pieces from total net weight and finished unit weight', () => {
    expect(piecesFromWeight(2.5, 31.25)).toBe(80)
    expect(piecesFromWeight('2.500', '31.25')).toBe(80)
    expect(piecesFromWeight(0, 31.25)).toBeNull()
    expect(piecesFromWeight(2.5, 0)).toBeNull()
  })

  it('multiplies a single weighing by the entered batch count', () => {
    expect(piecesFromBatchCount(3, 24)).toBe(72)
    expect(piecesPerBatchFromTotal(3301, 3)).toBe(1100)
    expect(shipmentPieceQuantity({ totalNetWeightKg: 2.5, unitWeightG: 31.25, batchCount: 3, piecesPerBatch: 24 })).toBe(240)
    expect(shipmentPieceQuantity({ totalNetWeightKg: 2.5, unitWeightG: 31.25 })).toBe(80)
    expect(repeatedBatchNetWeightKg(10.2, 34)).toBe(346.8)
    expect(String(repeatedBatchNetWeightKg(10.2, 34))).not.toContain('999999')
    expect(normalizeWeightKg(1 / 3)).toBe(0.333)
    expect(repeatedBatchNetWeightKg(1 / 3, 3)).toBe(1)
    expect(normalizeWeightKg(null)).toBeNull()
    expect(normalizeWeightKg(undefined)).toBeNull()
    expect(normalizeWeightKg('')).toBeNull()
    expect(normalizeWeightKg('   ')).toBeNull()
    expect(normalizeWeightKg(-1.2346)).toBe(-1.235)
  })

  it('enforces the flow-card single-batch quantity cap at exactly ten percent', () => {
    expect(processCardQuantityUpperLimit(100, 10)).toBe(110)
    expect(shipmentQuantityWithinFlowCardLimit(110, 100, 10)).toBe(true)
    expect(shipmentQuantityWithinFlowCardLimit(111, 100, 10)).toBe(false)
  })

  it('reads only explicit unit-weight fields from product specifications', () => {
    expect(orderUnitWeightG({ unit_weight_g: '0.21', product_specification: { latest_unit_weight_g: '0.18' } } as any)).toBe(0.21)
    expect(orderUnitWeightG({ product_specification: { raw_data: { unit_weight_g: '0.18' } } } as any)).toBe(0.18)
    expect(orderUnitWeightG({ product_specification: { raw_data: { 胶料重量: 16.9 } } } as any)).toBe(null)
  })
})
