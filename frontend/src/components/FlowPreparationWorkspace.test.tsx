import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { ReadOnlyContext } from '../access'
import { FlowPreparationWorkspace } from './FlowPreparationWorkspace'
import { flowApi, type PreparationContext, type PreparationGroup } from '../flowcards'

Object.defineProperty(window,'matchMedia',{writable:true,value:vi.fn().mockImplementation((query:string)=>({matches:false,media:query,addListener:vi.fn(),removeListener:vi.fn(),addEventListener:vi.fn(),removeEventListener:vi.fn()}))})
globalThis.ResizeObserver=class { observe(){} unobserve(){} disconnect(){} }

vi.mock('../flowcards', async (original) => ({...await original<typeof import('../flowcards')>(),flowApi:{create:vi.fn(),generate:vi.fn(),preview:vi.fn(),cards:vi.fn(),group:vi.fn()}}))
const standard={id:1,version:1,status:'ACTIVE',customer_key:'翔邦',source_kind:'FACTORY_PROCESS',applicable_order_id:null,scope:'',whole_mold:'NOT_REQUIRED',source:'合成复核标准',snapshot:{criteria:[],appearance:[{id:1,name:'毛刺'},{id:2,name:'杂质'},{id:3,name:'包风'},{id:4,name:'缺料'}],route:{confirmed:true,inspection:true},packaging:{molds_per_card:100,patrol_every_molds:50}}}
const data:PreparationContext={write_enabled:true,preparation_enabled:true,can_maintain_standard:false,product:{id:1,specification:'SYNTH-201',material:'SYNTH'},run_id:1,mold_id:null,order_ids:[1],orders:[{id:1,order_no:'SYNTH-ORDER',item_no:'1'}],suggested_molds:201,standards:[standard],appearance:standard.snapshot.appearance,criteria:[],diagrams:[],existing_groups:[],runs:[{id:1,segment_no:1,status:'PLANNED',status_display:'待上机',station_id:null,mold_id:null,planned_molds:201,historical:false}],available_ranges:[[1,201]],occupied_ranges:[],preferred_group:null,missing:[],auto_standard_id:1,conflicts:[],suggestion_id:1}
const draft:PreparationGroup={id:'same-draft',revision:3,range_start:1,range_end:201,updated_at:'2026-11-19',updated_by:1,product_id:1,run_id:1,planned_molds:201,molds_per_card:100,whole_mold:'NOT_REQUIRED',plan_basis:'明确生产段',status:'DRAFT',request_key:'saved',snapshot:{product:data.product,orders:data.orders,standard,whole_mold:'NOT_REQUIRED',template:{version:'草稿'}}}
function mount(context:PreparationContext=data,readonly=false){return render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><MemoryRouter initialEntries={['/flow-cards?order_id=1']}><ReadOnlyContext.Provider value={readonly}><App><FlowPreparationWorkspace data={context} refresh={vi.fn()} lookup={null} settings={null} printResults={null} print={vi.fn()} /></App></ReadOnlyContext.Provider></MemoryRouter></QueryClientProvider>)}
describe('server preparation workspace',()=>{
  beforeEach(()=>{vi.clearAllMocks();vi.mocked(flowApi.create).mockResolvedValue(draft);vi.mocked(flowApi.generate).mockResolvedValue({...draft,status:'GENERATED'});vi.mocked(flowApi.cards).mockResolvedValue({count:0,results:[]});vi.mocked(flowApi.preview).mockResolvedValue({count:3,tail_molds:1,planned_molds:201})})
  it('generates from auto inherited standard without entering parameters',async()=>{mount();const button=await screen.findByRole('button',{name:'生成 3 张并准备打印'});fireEvent.click(button);await waitFor(()=>expect(flowApi.generate).toHaveBeenCalledWith('same-draft',3));expect(vi.mocked(flowApi.create).mock.calls[0][0]).toMatchObject({run_id:1,order_ids:[1],standard_id:1,planned_molds:201,molds_per_card:100,card_molds:[100,100,1]});expect(await screen.findByText(/本组已发号/)).toBeInTheDocument()})
  it('resolves a lost generation response by the same group instead of creating again',async()=>{vi.mocked(flowApi.generate).mockRejectedValueOnce(new Error('network timeout'));vi.mocked(flowApi.group).mockResolvedValueOnce({...draft,status:'GENERATED'});mount();fireEvent.click(await screen.findByRole('button',{name:'生成 3 张并准备打印'}));expect(await screen.findByText(/本组已发号/)).toBeInTheDocument();expect(flowApi.group).toHaveBeenCalledWith('same-draft');expect(flowApi.create).toHaveBeenCalledTimes(1)})
  it('normal save carries the same draft id and concurrency version',async()=>{mount({...data,preferred_group:draft,existing_groups:[draft]});await screen.findByText(/同一草稿ID/);fireEvent.click(screen.getByRole('button',{name:'保存本次准备'}));await waitFor(()=>expect(flowApi.create).toHaveBeenCalled());expect(vi.mocked(flowApi.create).mock.calls[0][0]).toMatchObject({group_id:'same-draft',expected_revision:3});expect(flowApi.generate).not.toHaveBeenCalled()})
  it('restores this-segment process adjustments without changing the master',async()=>{const process_overrides={primary_curing_time:{state:'CONFIRMED',lower:'55',upper:'60',unit:'min',source:'仅本段合成依据'}};mount({...data,preferred_group:{...draft,snapshot:{...draft.snapshot,process_overrides}}});await screen.findByText(/同一草稿ID/);fireEvent.click(screen.getByRole('button',{name:'保存本次准备'}));await waitFor(()=>expect(flowApi.create).toHaveBeenCalled());expect(vi.mocked(flowApi.create).mock.calls[0][0]).toMatchObject({process_overrides});expect(standard.snapshot).not.toHaveProperty('process_overrides')})
  it('readonly can inspect sources and preview, but cannot save or generate',async()=>{mount(data,true);await screen.findByLabelText('第3张预计模数');const preview=await screen.findByRole('button',{name:'预览（不保存，只读可用）'});expect(preview).toBeEnabled();expect(screen.getByRole('button',{name:'保存本次准备'})).toBeDisabled();expect(screen.getByRole('button',{name:'查看标准、候选与来源 / 维护'})).toBeEnabled();fireEvent.click(preview);await screen.findByText(/不落库预览：3张/);expect(flowApi.create).not.toHaveBeenCalled()})
  it('order accepts 360 directly, edits tail and rejects a mismatched allocation',async()=>{
    mount({...data,run_id:null,suggested_molds:null,available_ranges:[],runs:[]})
    await waitFor(()=>expect(screen.getByLabelText('默认每张卡模数（自动分卡）')).toHaveValue('100'))
    const planned=screen.getByLabelText('本次预计生产模数')
    expect(planned).toBeEnabled()
    fireEvent.change(planned,{target:{value:'360'}})
    expect(await screen.findByLabelText('第4张预计模数')).toHaveValue('60')
    fireEvent.change(screen.getByLabelText('第4张预计模数'),{target:{value:'70'}})
    await waitFor(()=>expect(screen.getByRole('button',{name:'生成 4 张并准备打印'})).toBeDisabled())
    fireEvent.change(screen.getByLabelText('第3张预计模数'),{target:{value:'90'}})
    await waitFor(()=>expect(screen.getByRole('button',{name:'生成 4 张并准备打印'})).toBeEnabled())
    fireEvent.click(screen.getByRole('button',{name:'保存本次准备'}))
    await waitFor(()=>expect(flowApi.create).toHaveBeenCalled())
    expect(vi.mocked(flowApi.create).mock.calls[0][0]).toMatchObject({order_plan:true,run_id:null,planned_molds:360,card_molds:[100,100,90,70]})
  })
  it('restores order-only saved allocation instead of clearing expected molds',async()=>{
    mount({...data,run_id:null,suggested_molds:null,available_ranges:[],preferred_group:{...draft,run_id:null,planned_molds:360,snapshot:{...draft.snapshot,card_molds:[100,100,90,70]}}})
    expect(await screen.findByLabelText('第4张预计模数')).toHaveValue('70')
    expect(screen.getByLabelText('本次预计生产模数')).toHaveValue('360')
  })
})
