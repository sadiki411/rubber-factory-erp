import { safeBusinessReturn } from './navigation'

it('preserves exact internal section/page context and rejects external or ambiguous returns', () => {
  const target = '/order-overview/162?section=production&page=2&return_to=%2Forders%3Fpage%3D3'
  expect(safeBusinessReturn(target)).toBe(target)
  for (const value of ['https://evil.test', '//evil.test/orders', '/\\evil.test/orders', '/orders\\evil', '/orders\n', '/login', '/orders/../admin', '/%2f%2fevil.test', '/order-overview/nope']) expect(safeBusinessReturn(value)).toBe('/operations')
  expect(safeBusinessReturn(null, '/production')).toBe('/production')
})
