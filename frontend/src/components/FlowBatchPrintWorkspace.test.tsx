import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { FlowBatchPrintWorkspace } from './FlowBatchPrintWorkspace'
import { flowApi, type FlowPrintPlan, type PrintJob } from '../flowcards'

vi.mock('../flowPrintWindow', async original => ({
  ...await original<typeof import('../flowPrintWindow')>(), withFlowPrintWindow: (task:()=>Promise<unknown>)=>task(),
}))
vi.mock('../flowcards', async original => ({
  ...await original<typeof import('../flowcards')>(),
  flowApi:{ printOrderOptions:vi.fn(), printOrderPlan:vi.fn(), print:vi.fn(), printResult:vi.fn(), printJob:vi.fn(), printPreview:vi.fn() },
}))
globalThis.ResizeObserver=class {observe(){} unobserve(){} disconnect(){}}
Object.defineProperty(window,'matchMedia',{writable:true,value:(media:string)=>({matches:false,media,addListener:vi.fn(),removeListener:vi.fn(),addEventListener:vi.fn(),removeEventListener:vi.fn()})})
const selected = [{id:1, order_no:'SYNTH-A', item_no:'01', customer:'合成客户', status:'进行中', has_cards:true},
  {id:2, order_no:'SYNTH-B', item_no:'02', customer:'合成客户', status:'进行中', has_cards:true}]
const cards = selected.map((order,index)=>({id:'card-'+order.id,card_no:'FC-SYNTH-'+order.id,sequence:1,denominator:1,group_id:'group-'+index,reprint:false,orders:[order]}))
const plan:FlowPrintPlan = {packets:[cards],card_count:2,write_enabled:true,pending_jobs:[],counts:{pending:0,reprint:0,identity_mismatch:0,first_print:2}}
const job:PrintJob = {id:'test-job',status:'PENDING',request_key:'synthetic',pdf_url:'/print.pdf',file_prepared:true,pdf_sha256:'test',paper_layout:'A5_SINGLE',
  items:cards.map(c=>({card_id:c.id,card_no:c.card_no,group_id:c.group_id,sequence:1,denominator:1,status:'UNKNOWN',active:true,proposed_reprint:0,
    order_refs:c.orders.map(o=>o.order_no+'/'+o.item_no)}))}

it('selects explicit items and sends only existing selected card ids with A5',async()=>{
  vi.mocked(flowApi.printOrderOptions).mockResolvedValue({count:2,results:selected})
  vi.mocked(flowApi.printOrderPlan).mockResolvedValue(plan)
  vi.mocked(flowApi.print).mockResolvedValue(job)
  render(<QueryClientProvider client={new QueryClient()}><App><FlowBatchPrintWorkspace specification="SYNTH-DN125" material="SYNTH-E6514" readOnly={false}/></App></QueryClientProvider>)
  expect(await screen.findByRole('checkbox',{name:/SYNTH-A/})).not.toBeChecked()
  fireEvent.click(screen.getByRole('button',{name:'全选本页已有卡的订单'}))
  fireEvent.click(screen.getByRole('button',{name:'汇总所选订单的卡'}))
  expect(await screen.findByText('本册已选 2 张')).toBeInTheDocument()
  const scope={specification:'SYNTH-DN125',material:'SYNTH-E6514',order_ids:[1,2]}
  expect(flowApi.printOrderPlan).toHaveBeenCalledWith({order_scope:scope,paper_layout:'A5_SINGLE',include_reprints:false})
  expect(flowApi.print).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button',{name:'打印本册所选原卡'}))
  await waitFor(()=>expect(flowApi.print).toHaveBeenCalledWith(expect.objectContaining({card_ids:['card-1','card-2'],order_scope:scope,paper_layout:'A5_SINGLE',include_reprints:false})))
  expect(await screen.findByText(/A5 横向，实际大小 100%/)).toBeInTheDocument()
  expect(screen.getByRole('checkbox',{name:/SYNTH-A\/01 · 第 1\/1 批/})).toBeInTheDocument()
  expect(flowApi.printResult).not.toHaveBeenCalled()
})
