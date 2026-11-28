import { Alert, Button, Form, Input, Select, Space, Typography } from 'antd'
import { useState } from 'react'
import { flowApi } from '../flowcards'

type Issue = { id: number; name: string }

export function FlowAppearancePicker({ items, inherited = [], disabled, canAdd, changed, saving }: {
  items: Issue[]; inherited?: Issue[]; disabled?:boolean; canAdd?:boolean
  changed?:()=>void; saving?:(value:boolean)=>void
}) {
  const form = Form.useFormInstance()
  const ids: number[] = Form.useWatch('appearance_ids', form) || []
  const [added, setAdded] = useState<Issue[]>([])
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const entries = [...new Map([...inherited, ...items, ...added].map(i => [i.id,i])).values()]
  const add = async () => {
    if (!name.trim() || busy || ids.length >= 20) return
    setBusy(true); setError(''); saving?.(true)
    try {
      const issue = await flowApi.appearance({name:name.trim(), reuse_existing:true})
      setAdded(prior => [...prior.filter(i=>i.id!==issue.id), issue])
      const next = new Set<number>(form.getFieldValue('appearance_ids') || [])
      if (next.size >= 20 && !next.has(issue.id)) throw new Error('外观项目最多20项，请先移除一项。')
      form.setFieldValue('appearance_ids',[...next, ...(!next.has(issue.id)?[issue.id]:[])])
      setName(''); changed?.()
    } catch (e) { setError((e as Error).message) }
    finally {setBusy(false); saving?.(false)}
  }
  return <>
    <Form.Item name="appearance_ids" label="外观检查项目" rules={[{type:'array',max:20,message:'最多20项'}]} style={{marginBottom:8}}>
      <Select mode="multiple" allowClear maxCount={20} optionFilterProp="label" disabled={disabled||busy}
        placeholder="选中的项目才会印到流转卡" options={entries.map(i=>({value:i.id,label:i.name}))} />
    </Form.Item>
    {canAdd && <Space.Compact style={{width:'100%',marginBottom:8}}>
      <Input aria-label="新增外观检查项目" placeholder="输入一项，例如黑点、拔破" maxLength={40} value={name}
        disabled={disabled||busy} onChange={e=>setName(e.target.value)} onPressEnter={e=>{e.preventDefault();void add()}} />
      <Button loading={busy} disabled={disabled||!name.trim()||ids.length>=20} onClick={()=>void add()}>新增并选入</Button>
    </Space.Compact>}
    {error && <Alert type="error" showIcon title="外观项目未添加" description={error} />}
    <Typography.Paragraph type="secondary">不用凑四项；点项目右侧的 × 只从当前选择移除，不删除项目库或旧卡。开启「巡检」才会在纸上打印外观栏。</Typography.Paragraph>
  </>
}
