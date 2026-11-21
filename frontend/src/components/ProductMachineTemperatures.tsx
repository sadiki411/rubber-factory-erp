import { Alert, App, Button, Card, InputNumber, Select, Space, Typography } from 'antd'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { apiFetch } from '../api/client'
import { useReadOnly } from '../access'

type Station = {id:number; code:string; active:boolean; temperature_c:string|null}
type Suggestions = {can_edit:boolean; stations:Station[]}

export function ProductMachineTemperatures({ productId }: {productId:number}) {
  const readOnly=useReadOnly()
  const [expanded,setExpanded]=useState(false)
  const [station,setStation]=useState<number>()
  const [value,setValue]=useState<string|null>(null)
  const {message}=App.useApp()
  const url=`/api/orders/product-specifications/${productId}/machine-temperatures/`
  const query=useQuery({queryKey:['product-machine-temperatures',productId],queryFn:()=>apiFetch<Suggestions>(url),enabled:expanded})
  const save=useMutation({mutationFn:(temperature_c:string|null)=>apiFetch<Suggestions>(url,{method:'PUT',body:JSON.stringify({station_id:station,temperature_c})}),onSuccess:()=>{void query.refetch();message.success('已更新这台机台的建议值')},onError:(error:Error)=>message.error(error.message)})
  const saved=query.data?.stations.filter(s=>s.temperature_c!==null)||[]
  const selected=query.data?.stations.find(s=>s.id===station)
  return <Card size="small" title="机台建议设定模温">
    <details onToggle={event=>setExpanded(event.currentTarget.open)}><summary>按机台查看 / 修改{query.data ? `（${saved.length} 台已填写）` : ''}</summary>
      <Typography.Paragraph type="secondary">用实测模温调机后，可把合适的机台设定值记下来。不同机台分别保存；它不是客户标准，也不是实测值。</Typography.Paragraph>
      {query.isError ? <Alert type="error" title="建议值读取失败" action={<Button onClick={()=>void query.refetch()}>重试</Button>} /> : query.isPending ? <Typography.Text>正在读取机台…</Typography.Text> : <>
        {saved.length ? <ul>{saved.map(s=><li key={s.id}>{s.code}：{s.temperature_c} °C{s.active?'':'（已停用）'}</li>)}</ul> : <Typography.Paragraph>还没有记录机台建议值。</Typography.Paragraph>}
        {!readOnly&&query.data.can_edit&&<Space wrap>
          <Select aria-label="生产机台" placeholder="选择机台" style={{minWidth:130}} value={station} onChange={id=>{setStation(id);setValue(query.data.stations.find(s=>s.id===id)?.temperature_c??null)}} options={query.data.stations.map(s=>({value:s.id,label:`${s.code}${s.active?'':'（已停用）'}`}))} />
          <InputNumber stringMode aria-label="建议设定模温（℃）" placeholder="设定模温 °C" min="0.01" precision={2} value={value} onChange={setValue} />
          <Button type="primary" disabled={!selected?.active||!value} loading={save.isPending} onClick={()=>save.mutate(value)}>保存建议值</Button>
          {selected?.temperature_c!=null&&<Button loading={save.isPending} onClick={()=>{setValue(null);save.mutate(null)}}>清除这台的建议值</Button>}
        </Space>}
      </>}
    </details>
  </Card>
}
