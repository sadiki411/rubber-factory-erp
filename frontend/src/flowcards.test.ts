import { beforeEach, describe, expect, it } from 'vitest'
import { finishFlowRequest, flowRequestKey, isInternalCardCode } from './flowcards'
import { isLikelyProcessCardNo, normalizeProcessCardQrText } from './quality'

describe('internal flow-card identity and request recovery', () => {
  beforeEach(() => sessionStorage.clear())
  it('preserves the internal type instead of stripping the ERPFC URI prefix', () => {
    const code = 'ERPFC:0feb996e-6ab5-414a-b23e-616a9ccab3e2'
    expect(normalizeProcessCardQrText(code)).toBe(code.toUpperCase())
    expect(isInternalCardCode(code)).toBe(true)
    expect(isLikelyProcessCardNo(code)).toBe(false)
    expect(isLikelyProcessCardNo('FC-20261118-1234567890ABCDEF')).toBe(false)
    expect(isLikelyProcessCardNo('04-M003-2609300006')).toBe(true)
  })
  it('reuses the same request after rerender/reload and starts new only on acknowledged completion', () => {
    const first = flowRequestKey('group:run10', { molds: 100 })
    expect(flowRequestKey('group:run10', { molds: 100 })).toBe(first)
    finishFlowRequest('group:run10')
    expect(flowRequestKey('group:run10', { molds: 100 })).not.toBe(first)
  })
  it('different contents receive a different request and cannot impersonate the old result', () => {
    const key = flowRequestKey('event:1', { actual: 0 })
    expect(flowRequestKey('event:1', { actual: null })).not.toBe(key)
  })
})
