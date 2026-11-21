// Display only: the imported name remains the identity used for saving and comparing standards.
const NOTES = new Map([
  ['HD', '硬度'],
  ['SG', '密度'],
  ['ID', '内径'],
  ['OD', '外径'],
  ['THK', '厚度'],
  ['C/S-A', '轴向线径，方向待核'],
  ['C/S-R', '径向线径，方向待核'],
  ['MISMATCH', '合模错位'],
  ['FLASHT', '毛边厚度'],
  ['FLASHLEN', '毛边伸出长度'],
  ['FLASH', '毛边，量法待确认'],
  ['[1]', '图纸尺寸1，位置见图纸'],
  ['[2]', '图纸尺寸2，位置见图纸'],
  ['[3]', '图纸尺寸3，位置见图纸'],
])

export function inspectionItemLabel(item: string) {
  const note = NOTES.get(item.normalize('NFKC').trim().toUpperCase())
  return note ? `${item}（${note}）` : item
}

export function inspectionImportSummary(row: {
  inspection_item?: string; inspection_lower?: string; inspection_upper?: string; inspection_unit?: string
}) {
  const limit = (value?: string) => value === '' || value == null ? '—' : value
  return `${inspectionItemLabel(row.inspection_item || '项目待核')}：${limit(row.inspection_lower)} ～ ${limit(row.inspection_upper)} ${row.inspection_unit || ''}`.trimEnd()
}
