import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiFetch } from './client'

describe('API validation error messages', () => {
  afterEach(() => vi.unstubAllGlobals())

  it.each([
    [{ product: { non_field_errors: ['相同产品资料已经存在'] } }, '相同产品资料已经存在'],
    [{ lines: [{ quantity: ['库存数量不足'] }] }, '库存数量不足'],
    [{ product: { unit_weight_g: ['单重必须大于零'] } }, '单重必须大于零'],
    [{ ignored: [], inspector_id: ['请选择品检员'] }, '请选择品检员'],
    [{ detail: '目标库位已经有在用容器' }, '目标库位已经有在用容器'],
    [{ message: '请重新登录' }, '请重新登录'],
    [{ error: '保存失败' }, '保存失败'],
    [['第一条原因', '第二条原因'], '第一条原因'],
    [{ product: {} }, '请求失败（400）'],
  ])('surfaces nested or direct JSON validation errors: %j', async (data, message) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(data), {
      status: 400, headers: { 'Content-Type': 'application/json' },
    })))
    await expect(apiFetch('/api/inventory/receipts/', { method: 'POST', body: '{}' }))
      .rejects.toMatchObject({ name: 'ApiError', status: 400, message, data })
  })

  it('does not display an HTML server error as a business message', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<!DOCTYPE html><html>server error</html>', {
      status: 502, headers: { 'Content-Type': 'text/html' },
    })))
    await expect(apiFetch('/api/inventory/receipts/')).rejects.toEqual(
      expect.objectContaining({ message: '请求失败（502）', status: 502 }),
    )
  })

  it('retains successful JSON responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 17 }), {
      status: 201, headers: { 'Content-Type': 'application/json' },
    })))
    await expect(apiFetch('/api/inventory/receipts/')).resolves.toEqual({ id: 17 })
    expect(new ApiError(400, '原因').status).toBe(400)
  })
})
