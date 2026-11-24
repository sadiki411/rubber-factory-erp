import { App } from 'antd'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { FlowPrintResults, FlowVoidDialog } from './FlowPrintResults'
import { flowApi, type FlowCard, type PrintJob } from '../flowcards'

vi.mock('../flowcards',async (original)=>({...await original<typeof import('../flowcards')>(),
  flowApi:{printResult:vi.fn(),printJob:vi.fn(),record:vi.fn()}}))
Object.defineProperty(window,'matchMedia',{writable:true,value:(media:string)=>({matches:false,media,addListener:vi.fn(),removeListener:vi.fn(),addEventListener:vi.fn(),removeEventListener:vi.fn()})})
globalThis.ResizeObserver=class {observe(){} unobserve(){} disconnect(){}}

const cards:FlowCard[]=['first','second'].map((id,i)=>({id,card_no:`FC-SYNTH-${id}`,code:`ERPFC:${id}`,group_id:'group',sequence:i+1,denominator:2,
  planned_molds:100,original_date:'2026-11-24',reprint_count:0,status:'ACTIVE'}))
const job:PrintJob={id:'job',status:'PENDING',request_key:'synth',pdf_url:'/synth.pdf',file_prepared:true,pdf_sha256:'synth',paper_layout:'A4_TWO_UP',
  items:cards.map(c=>({card_id:c.id,card_no:c.card_no,group_id:c.group_id,sequence:c.sequence,denominator:c.denominator,proposed_reprint:0,status:'UNKNOWN',active:true}))}

beforeEach(()=>vi.clearAllMocks())

it('cancels only this batch from the original multi-card job, after explicit confirmation',async()=>{
  vi.mocked(flowApi.printResult).mockResolvedValue(job)
  render(<App><FlowPrintResults job={job} cardId="second" changed={vi.fn()} disabled={false}/></App>)
  expect(screen.getByText(/下方确认或取消仅作用于本批/)).toBeInTheDocument()
  expect(screen.queryByRole('checkbox',{name:/第 1\/2 批/})).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button',{name:'取消未确认打印'}))
  expect(flowApi.printResult).not.toHaveBeenCalled()
  fireEvent.click(await screen.findByRole('button',{name:'确认取消打印'}))
  await waitFor(()=>expect(flowApi.printResult).toHaveBeenCalledWith('job',{second:'CANCELLED'}))
})

it('confirms a printed card without changing the outcome for unchecked cards',async()=>{
  vi.mocked(flowApi.printResult).mockResolvedValue(job)
  render(<App><FlowPrintResults job={job} changed={vi.fn()} disabled={false}/></App>)
  fireEvent.click(screen.getByRole('checkbox',{name:/第 1\/2 批/}))
  fireEvent.click(screen.getByRole('button',{name:'确认所选打印成功'}))
  await waitFor(()=>expect(flowApi.printResult).toHaveBeenCalledWith('job',{first:'SUCCESS'}))
})

it('void requires a reason and confirmation that the selected card is unused',async()=>{
  vi.mocked(flowApi.record).mockResolvedValue({id:'event'} as never)
  render(<App><FlowVoidDialog cards={[cards[1]]} close={vi.fn()} changed={vi.fn()}/></App>)
  const submit=screen.getByRole('button',{name:'确认作废'})
  expect(submit).toBeDisabled()
  fireEvent.change(screen.getByRole('textbox',{name:'作废原因'}),{target:{value:'合成误发'}})
  expect(submit).toBeDisabled()
  fireEvent.click(screen.getByRole('checkbox',{name:/确认误发、从未使用/}))
  expect(submit).toBeEnabled()
  fireEvent.click(submit)
  await waitFor(()=>expect(flowApi.record).toHaveBeenCalledWith(expect.objectContaining({
    card_ids:['second'],kind:'VOID',values:{reason:'合成误发',unused_confirmed:true}
  })))
})
