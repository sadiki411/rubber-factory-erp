import { ORDER_MATERIAL_FILTER_OPTIONS, ORDER_MATERIAL_META } from './orderStatus'

describe('unified sufficient material status', () => {
  it('offers just one sufficient filter, without a separate excess option', () => {
    expect(ORDER_MATERIAL_FILTER_OPTIONS).toEqual([
      { value: '', label: '全部胶料状态' },
      { value: 'UNKNOWN', label: '未核算' },
      { value: 'NOT_RECEIVED', label: '未收到' },
      { value: 'PARTIAL', label: '未发够' },
      { value: 'SUFFICIENT', label: '已发够' },
    ])
  })

  it('renders cached excess responses as sufficient on lists and details', () => {
    expect(ORDER_MATERIAL_META.OVER).toEqual(ORDER_MATERIAL_META.SUFFICIENT)
    expect(ORDER_MATERIAL_META.OVER.text).toBe('已发够')
  })
})
