import { inspectionImportSummary, inspectionItemLabel } from './inspectionItemLabel'

it.each([
  ['HD', '硬度'], ['SG', '密度'], ['ID', '内径'], ['OD', '外径'], ['THK', '厚度'],
  ['C/S-A', '轴向线径，方向待核'], ['C/S-R', '径向线径，方向待核'], ['MISMATCH', '合模错位'],
  ['FLASHT', '毛边厚度'], ['FLASHLEN', '毛边伸出长度'], ['FLASH', '毛边，量法待确认'],
  ['[1]', '图纸尺寸1，位置见图纸'], ['[2]', '图纸尺寸2，位置见图纸'], ['[3]', '图纸尺寸3，位置见图纸'],
])('shows the original %s with its Chinese reading aid', (code, note) => {
  expect(inspectionItemLabel(code)).toBe(`${code}（${note}）`)
})

it('preserves categories, unfamiliar or already annotated codes, limits, and units', () => {
  for (const code of ['2001', '6001', 'UNKNOWN', 'HD（其他解释）', '原有中文']) {
    expect(inspectionItemLabel(code)).toBe(code)
  }
  expect(inspectionItemLabel('ｈｄ')).toBe('ｈｄ（硬度）')
  expect(inspectionImportSummary({ inspection_item: 'FLASH', inspection_lower: '0', inspection_upper: '0.24', inspection_unit: 'mm' }))
    .toBe('FLASH（毛边，量法待确认）：0 ～ 0.24 mm')
  expect(inspectionImportSummary({ inspection_item: 'ID', inspection_lower: '', inspection_upper: '9' }))
    .toBe('ID（内径）：— ～ 9')
})
