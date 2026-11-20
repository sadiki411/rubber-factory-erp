import { bakeForForm, bakeForSave, bakeText, processForForm, processForSave, processParameterText } from './productStandardFields'

describe('typed product process fields',()=>{
  it('keeps textual operations as text and does not invent bounds or zero',()=>{
    const p=processForSave({oil:{raw_text:'少量',state:'CONFIRMED'},venting:{raw_text:'2次；上停0.3秒'},upper_set_temperature:{_mode:'FIXED'},pressure:{_mode:'FIXED',value:'0',unit:'MPa'}})
    expect(p).toEqual({oil:{raw_text:'少量',state:'CONFIRMED'},venting:{raw_text:'2次；上停0.3秒'},pressure:{value:'0',unit:'MPa'}})
    expect(processParameterText(p.oil)).toBe('少量')
    expect(processParameterText({raw_text:'少量',lower:'1',upper:'2'},true)).toBe('少量')
  })
  it('edits equal legacy bounds as a specified value, preserves real ranges and source',()=>{
    const source={upper_set_temperature:{lower:'180',upper:'180.0',unit:'℃',source:'SYNTH'},primary_curing_time:{lower:'50',upper:'60',unit:'min'}}
    const form=processForForm(source)
    expect(form.upper_set_temperature.value).toBe('180')
    expect(processForSave(form)).toEqual({upper_set_temperature:{value:'180',unit:'℃',source:'SYNTH'},primary_curing_time:{lower:'50',upper:'60',unit:'min'}})
    expect(source.upper_set_temperature.lower).toBe('180')
    expect(processParameterText(form.upper_set_temperature)).toBe('180 ℃')
  })
  it('clears only the explicitly inactive numeric representation when switching modes',()=>{
    expect(processForSave({pressure:{_mode:'FIXED',value:'50',lower:'40',upper:'60',unit:'MPa'}}).pressure).toEqual({value:'50',unit:'MPa'})
    expect(processForSave({pressure:{_mode:'RANGE',value:'50',lower:'40',upper:'60',unit:'MPa'}}).pressure).toEqual({lower:'40',upper:'60',unit:'MPa'})
  })
  it('shows no-bake without requesting conditions, and keeps fixed and ranged legacy requirements distinct',()=>{
    const form=bakeForForm({state:'APPLICABLE',temperature_lower:'145',temperature_upper:'145',minutes_lower:'80',minutes_upper:'100'})
    expect(form._temperature_mode).toBe('FIXED');expect(form._minutes_mode).toBe('RANGE')
    expect(bakeForSave(form)).toEqual({state:'APPLICABLE',temperature_value:'145',minutes_lower:'80',minutes_upper:'100'})
    expect(bakeForSave({...form,state:'NOT_APPLICABLE'})).toEqual({state:'NOT_APPLICABLE'})
    expect(bakeForSave({state:'APPLICABLE',_temperature_mode:'FIXED',temperature_lower:'140',temperature_upper:'150'})).toEqual({state:'APPLICABLE'})
    expect(bakeText({state:'NOT_APPLICABLE'})).toBe('无需二烤')
    expect(bakeText({state:'APPLICABLE',temperature_value:'145',minutes_value:'90'})).toBe('145 ℃ × 90 min')
    expect(bakeText({state:'UNKNOWN'})).toBe('二烤要求待确认')
  })
})
