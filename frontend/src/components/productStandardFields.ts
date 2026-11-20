export type ProcessParameter = { state?: string; lower?: string; upper?: string; value?: string; unit?: string; source?: string; raw_text?: string; _mode?: string; [key: string]: unknown }
export const NUMERIC_PROCESS_FIELDS = [
  ['upper_set_temperature', '上模设定温度', '℃'], ['lower_set_temperature', '下模设定温度', '℃'],
  ['upper_contact_temperature', '上模接触温度目标', '℃'], ['lower_contact_temperature', '下模接触温度目标', '℃'],
  ['primary_curing_time', '一次硫化时间', 's / min，以原表为准'], ['pressure', '压力', '注明压力类别及原单位'],
] as const
export const TEXT_PROCESS_FIELDS = [
  ['oil', '毛边洒油', ['不洒油', '少量', '适量', '禁止']],
  ['release_agent', '成型脱模剂', ['不使用', '少量', '按客户要求']],
  ['venting', '排气要求', ['不排气', '1次', '2次', '按原表要求']],
  ['arrangement', '排列 / 排料', []], ['machine_type', '机型 / 吨位', []], ['experience', '生产注意事项', []],
] as const
const present = (v: unknown) => v !== undefined && v !== null && v !== ''
const sameNumber = (a: unknown, b: unknown) => present(a) && present(b) && Number.isFinite(Number(a)) && Number(a) === Number(b)

export function parameterMode(p: ProcessParameter = {}) {
  if (p._mode) return p._mode
  return (present(p.lower) || present(p.upper)) && !sameNumber(p.lower, p.upper) ? 'RANGE' : 'FIXED'
}
export function processForForm(parameters: Record<string, ProcessParameter> = {}) {
  return Object.fromEntries(Object.entries(parameters).map(([key, p]) => {
    if (!NUMERIC_PROCESS_FIELDS.some(([name]) => name === key)) return [key, { ...p }]
    const mode = parameterMode(p)
    return [key, { ...p, _mode: mode, value: p.value ?? (sameNumber(p.lower, p.upper) ? p.lower : undefined) }]
  }))
}
export function processForSave(parameters: Record<string, ProcessParameter> = {}) {
  return Object.fromEntries(Object.entries(parameters).map(([key, input]) => {
    const p = { ...input }
    if (NUMERIC_PROCESS_FIELDS.some(([name]) => name === key)) {
      if (parameterMode(p) === 'RANGE') delete p.value
      else { delete p.lower; delete p.upper }
    }
    delete p._mode
    return [key, p] as const
  }).filter(([, p]) => Object.values(p).some(present)))
}
export function bakeForForm(bake: Record<string, unknown> = { state: 'UNKNOWN' }) {
  const result = { ...bake }
  for (const prefix of ['temperature', 'minutes']) {
    const hasRange = (present(bake[`${prefix}_lower`]) || present(bake[`${prefix}_upper`])) && !sameNumber(bake[`${prefix}_lower`], bake[`${prefix}_upper`])
    result[`_${prefix}_mode`] = hasRange ? 'RANGE' : 'FIXED'
    result[`${prefix}_value`] = bake[`${prefix}_value`] ?? (sameNumber(bake[`${prefix}_lower`], bake[`${prefix}_upper`]) ? bake[`${prefix}_lower`] : undefined)
  }
  return result
}
export function bakeForSave(input: Record<string, unknown> = { state: 'UNKNOWN' }) {
  const result = { ...input }
  for (const prefix of ['temperature', 'minutes']) {
    if (result[`_${prefix}_mode`] === 'RANGE') delete result[`${prefix}_value`]
    else { delete result[`${prefix}_lower`]; delete result[`${prefix}_upper`] }
    delete result[`_${prefix}_mode`]
    if (result.state !== 'APPLICABLE') {
      delete result[`${prefix}_value`]; delete result[`${prefix}_lower`]; delete result[`${prefix}_upper`]
    }
  }
  return result
}
export function processParameterText(p: ProcessParameter, textOnly = false) {
  if (textOnly && p.raw_text) return p.raw_text
  const value = present(p.value) ? String(p.value) : sameNumber(p.lower, p.upper) ? String(p.lower)
    : present(p.lower) && present(p.upper) ? `${p.lower}～${p.upper}` : present(p.lower) ? `≥ ${p.lower}` : present(p.upper) ? `≤ ${p.upper}` : ''
  return value ? `${value}${p.unit ? ` ${p.unit}` : ''}` : p.raw_text || (p.state === 'NOT_APPLICABLE' ? '不适用' : '未提供')
}
export function bakeText(bake: Record<string, unknown> = {}) {
  if (bake.state === 'NOT_APPLICABLE') return '无需二烤'
  if (bake.state !== 'APPLICABLE') return '二烤要求待确认'
  const format = (prefix: string) => processParameterText({ value: bake[`${prefix}_value`] as string, lower: bake[`${prefix}_lower`] as string, upper: bake[`${prefix}_upper`] as string })
  return `${format('temperature')} ℃ × ${format('minutes')} min`
}
