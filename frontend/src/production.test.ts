import { calculateGoodQuantity, canCreateProductionDailyLog, canSettleProductionRun, defaultProductionLogDate, formatProductionDate, isKeyboardActivationKey, isProductionLogDateAllowed, productionOperatorDayCount, productionReminderKey, productionSettlementInitialValues, productionSettlementRiskReasons, productionStationGroupLabel, productionStationNumber, requiresProductionUnloadTime, settlementExpectedQuantity, settlementInitialGoodQuantity, settlementQuantityMatches } from './production'

describe('production helpers', () => {
  it('uses the station code as the visible global machine number', () => {
    expect(productionStationNumber({ code: '6', position_no: 5 })).toBe('6')
    expect(productionStationNumber({ code: '  ', position_no: 5 })).toBe('5')
  })

  it('uses the shop-floor names for the three machine groups', () => {
    expect(productionStationGroupLabel('A')).toBe('一组')
    expect(productionStationGroupLabel('B')).toBe('二组')
    expect(productionStationGroupLabel('C')).toBe('三组')
  })

  it('never renders invalid or missing timestamps as Invalid Date', () => {
    expect(formatProductionDate(null)).toBe('-')
    expect(formatProductionDate('not-a-date')).toBe('-')
    expect(formatProductionDate('2026-07-11T08:30:00', 'MM-DD HH:mm')).toBe('07-11 08:30')
  })

  it('allows a rescheduled run to trigger a new reminder', () => {
    const first = productionReminderKey({ id: 9, expected_change_at: '2026-07-11T10:00:00+08:00' }, 'DUE_SOON')
    const rescheduled = productionReminderKey({ id: 9, expected_change_at: '2026-07-11T12:00:00+08:00' }, 'DUE_SOON')
    expect(first).not.toBe(rescheduled)
  })

  it('recognizes keyboard activation keys used by mobile order cards', () => {
    expect(isKeyboardActivationKey('Enter')).toBe(true)
    expect(isKeyboardActivationKey(' ')).toBe(true)
    expect(isKeyboardActivationKey('Escape')).toBe(false)
  })

  it('requires an unload time for completed runs and for cancelled runs that were loaded', () => {
    expect(requiresProductionUnloadTime('COMPLETED', true)).toBe(true)
    expect(requiresProductionUnloadTime('CANCELLED', true)).toBe(true)
    expect(requiresProductionUnloadTime('CANCELLED', false)).toBe(false)
    expect(requiresProductionUnloadTime('PLANNED', false)).toBe(false)
  })

  it('calculates good quantity from molds, cavities and defects', () => {
    expect(calculateGoodQuantity(10, 6, 3)).toBe(57)
    expect(calculateGoodQuantity(1, 6, 9)).toBe(0)
  })

  it('only settles completed runs or cancelled runs that were loaded', () => {
    expect(canSettleProductionRun('COMPLETED', true)).toBe(true)
    expect(canSettleProductionRun('CANCELLED', true)).toBe(true)
    expect(canSettleProductionRun('CANCELLED', false)).toBe(false)
    expect(canSettleProductionRun('RUNNING', true)).toBe(false)
  })

  it('only creates daily logs for loaded running or completed runs', () => {
    expect(canCreateProductionDailyLog('RUNNING', true)).toBe(true)
    expect(canCreateProductionDailyLog('COMPLETED', true)).toBe(true)
    expect(canCreateProductionDailyLog('CANCELLED', true)).toBe(false)
    expect(canCreateProductionDailyLog('PLANNED', false)).toBe(false)
  })

  it('defaults historical daily logs to the latest date inside the run interval', () => {
    expect(defaultProductionLogDate('2026-05-01T08:00:00', '2026-05-03T18:00:00', '2026-07-11')?.format('YYYY-MM-DD')).toBe('2026-05-03')
    expect(defaultProductionLogDate('2026-07-10T08:00:00', null, '2026-07-11')?.format('YYYY-MM-DD')).toBe('2026-07-11')
    expect(defaultProductionLogDate(null, null, '2026-07-11')).toBeUndefined()
  })

  it('uses expected output for an unsettled run even when API compatibility fields are zero', () => {
    expect(settlementInitialGoodQuantity(false, 0, 60)).toBe(60)
    expect(settlementInitialGoodQuantity(true, 57, 60)).toBe(57)
  })

  it('restores retained settlement draft values after settlement is invalidated', () => {
    expect(productionSettlementInitialValues({
      is_settled: false,
      actual_good_quantity: 57,
      actual_defective_quantity: 3,
      total_material_kg: '12.500',
      labor_cost: '80.00',
      energy_cost: '25.00',
      other_cost: '5.00',
      settlement_notes: '日报模数变更后待复核',
    }, 66)).toEqual({
      actual_good_quantity: 57,
      actual_defective_quantity: 3,
      total_material_kg: 12.5,
      labor_cost: 80,
      energy_cost: 25,
      other_cost: 5,
      settlement_notes: '日报模数变更后待复核',
    })
  })

  it('only applies expected-output defaults when an unsettled run has no draft', () => {
    expect(productionSettlementInitialValues({
      is_settled: false,
      actual_good_quantity: 0,
      actual_defective_quantity: 0,
      total_material_kg: '0.000',
      labor_cost: '0.00',
      energy_cost: '0.00',
      other_cost: '0.00',
      settlement_notes: '',
    }, 60)).toEqual({
      actual_good_quantity: 60,
      actual_defective_quantity: 0,
      total_material_kg: 0,
      labor_cost: 0,
      energy_cost: 0,
      other_cost: 0,
      settlement_notes: '',
    })
  })

  it('requires explicit confirmation for zero-price or all-zero-cost settlements', () => {
    expect(productionSettlementRiskReasons({
      unitPrice: 0,
      materialUnitPrice: 0,
      totalMaterialKg: 0,
      laborCost: 0,
      energyCost: 0,
      otherCost: 0,
    })).toEqual(['产品单价为0', '材料用量及各项成本全部为0'])
    expect(productionSettlementRiskReasons({
      unitPrice: 2,
      materialUnitPrice: 0,
      totalMaterialKg: 12,
      laborCost: 100,
      energyCost: 20,
      otherCost: 0,
    })).toEqual(['有材料用量但材料单价为0'])
    expect(productionSettlementRiskReasons({
      unitPrice: 2,
      materialUnitPrice: 10,
      totalMaterialKg: 12,
      laborCost: 100,
      energyCost: 20,
      otherCost: 0,
    })).toEqual([])
  })

  it('uses operator-day count instead of distinct factory production dates for performance', () => {
    expect(productionOperatorDayCount({ production_days: 12, operator_day_count: 31 })).toBe(31)
    expect(productionOperatorDayCount({ production_days: 12 })).toBe(0)
  })

  it('checks final good and defective quantities against molds and cavities', () => {
    expect(settlementExpectedQuantity(10, 6)).toBe(60)
    expect(settlementQuantityMatches(10, 6, 57, 3)).toBe(true)
    expect(settlementQuantityMatches(10, 6, 56, 3)).toBe(false)
  })

  it('keeps daily production dates inside the run interval', () => {
    expect(isProductionLogDateAllowed('2026-07-11', '2026-07-10T23:00:00', '2026-07-11T08:00:00')).toBe(true)
    expect(isProductionLogDateAllowed('2026-07-09', '2026-07-10T23:00:00', '2026-07-11T08:00:00')).toBe(false)
    expect(isProductionLogDateAllowed('2026-07-12', '2026-07-10T23:00:00', '2026-07-11T08:00:00')).toBe(false)
    expect(isProductionLogDateAllowed('2026-07-11', null, null)).toBe(false)
  })
})
