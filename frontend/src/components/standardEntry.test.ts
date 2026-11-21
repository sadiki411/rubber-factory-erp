import { mergeStandardEntries, standardItemKey } from './standardEntry'

it('matches aliases within a stage, without combining direction, stage or unknown flash geometry',()=>{
  const key=(name:string,stage='DIMENSION')=>standardItemKey(name,stage)
  expect(key('内径')).toBe(key('ID'))
  expect(key('毛边厚度')).not.toBe(key('FLASH'))
  expect(key('C/S-A')).not.toBe(key('C/S-R'))
  expect(key('线径')).not.toBe(key('C/S-R'))
  expect(key('硬度','MOLDING_HARDNESS')).not.toBe(key('HD','BAKED_HARDNESS'))
})

it('adopts a source identity without appending a row and rejects conflicting duplicate input',()=>{
  const old=[{name:'内径',stage:'DIMENSION',criterion_id:5,lower:'9',upper:'10'}]
  const incoming={name:'ID',stage:'DIMENSION',criterion_id:7,lower:'9.1',upper:'10',unit:'mm'}
  const merged=mergeStandardEntries(old,[incoming])
  expect(merged).toEqual([incoming])
  expect(mergeStandardEntries(merged,[incoming])).toEqual(merged)
  expect(old[0].criterion_id).toBe(5)
  expect(()=>mergeStandardEntries([],[incoming,{...incoming,upper:'11'}])).toThrow('同一阶段有不同要求')
})
