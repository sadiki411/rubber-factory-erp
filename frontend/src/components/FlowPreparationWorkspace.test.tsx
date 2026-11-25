import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, configure, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { ReadOnlyContext } from '../access'
import { FlowPreparationWorkspace } from './FlowPreparationWorkspace'
import { flowApi, type PreparationContext, type PreparationGroup } from '../flowcards'

Object.defineProperty(window,'matchMedia',{writable:true,value:vi.fn().mockImplementation((query:string)=>({matches:false,media:query,addListener:vi.fn(),removeListener:vi.fn(),addEventListener:vi.fn(),removeEventListener:vi.fn()}))})
globalThis.ResizeObserver=class { observe(){} unobserve(){} disconnect(){} }

vi.mock('../flowcards', async (original) => ({...await original<typeof import('../flowcards')>(),flowApi:{create:vi.fn(),generate:vi.fn(),preview:vi.fn(),cards:vi.fn(),group:vi.fn(),sample:vi.fn(),context:vi.fn(),pendingPrints:vi.fn()}}))
URL.revokeObjectURL=vi.fn()
configure({asyncUtilTimeout:5000})
const standard={id:1,version:1,status:'ACTIVE',customer_key:'翔邦',source_kind:'FACTORY_PROCESS',applicable_order_id:null,scope:'',whole_mold:'NOT_REQUIRED',source:'合成复核标准',snapshot:{criteria:[],appearance:[{id:1,name:'毛刺'},{id:2,name:'杂质'},{id:3,name:'包风'},{id:4,name:'缺料'}],route:{confirmed:true,inspection:true},packaging:{molds_per_card:100,patrol_every_molds:50}}}
const data:PreparationContext={write_enabled:true,preparation_enabled:true,can_maintain_standard:false,product:{id:1,specification:'SYNTH-201',material:'SYNTH'},run_id:1,mold_id:null,order_ids:[1],orders:[{id:1,order_no:'SYNTH-ORDER',item_no:'1'}],suggested_molds:201,standards:[standard],appearance:standard.snapshot.appearance,criteria:[],diagrams:[],existing_groups:[],runs:[{id:1,segment_no:1,status:'PLANNED',status_display:'待上机',station_id:null,mold_id:null,planned_molds:201,historical:false}],available_ranges:[[1,201]],occupied_ranges:[],preferred_group:null,missing:[],auto_standard_id:1,conflicts:[],suggestion_id:1}
const draft:PreparationGroup={id:'same-draft',revision:3,range_start:1,range_end:201,updated_at:'2026-11-19',updated_by:1,product_id:1,run_id:1,planned_molds:201,molds_per_card:100,whole_mold:'NOT_REQUIRED',plan_basis:'明确生产段',status:'DRAFT',request_key:'saved',snapshot:{product:data.product,orders:data.orders,standard,whole_mold:'NOT_REQUIRED',template:{version:'草稿'}}}
const print=vi.fn()
function mount(context:PreparationContext=data,readonly=false,openPrint?: (id:string,cardId?:string,groupId?:string)=>Promise<void>){return render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><MemoryRouter initialEntries={['/flow-cards?order_id=1']}><ReadOnlyContext.Provider value={readonly}><App><FlowPreparationWorkspace data={context} refresh={vi.fn()} lookup={null} settings={null} printResults={null} print={print} openPrint={openPrint} /></App></ReadOnlyContext.Provider></MemoryRouter></QueryClientProvider>)}
describe('server preparation workspace',{timeout:30000},()=>{
  beforeEach(()=>{vi.clearAllMocks();vi.mocked(flowApi.create).mockResolvedValue(draft);vi.mocked(flowApi.generate).mockResolvedValue({...draft,status:'GENERATED'});vi.mocked(flowApi.cards).mockResolvedValue({count:0,results:[]});vi.mocked(flowApi.preview).mockResolvedValue({count:3,tail_molds:1,planned_molds:201})})
  it('generates from auto inherited standard without entering parameters',async()=>{await act(async()=>{mount()});const button=await screen.findByRole('button',{name:'生成 3 张并准备打印'});expect(button).toBeEnabled();fireEvent.click(button);await waitFor(()=>expect(flowApi.generate).toHaveBeenCalledWith('same-draft',3));expect(vi.mocked(flowApi.create).mock.calls[0][0]).toMatchObject({run_id:1,order_ids:[1],standard_id:1,planned_molds:201,molds_per_card:100,card_molds:[100,100,1]});expect(await screen.findByText(/本组已发号/)).toBeInTheDocument()})
  it('resolves a lost generation response by the same group instead of creating again',async()=>{vi.mocked(flowApi.generate).mockRejectedValueOnce(new Error('network timeout'));vi.mocked(flowApi.group).mockResolvedValueOnce({...draft,status:'GENERATED'});await act(async()=>{mount()});fireEvent.click(await screen.findByRole('button',{name:'生成 3 张并准备打印'}));expect(await screen.findByText(/本组已发号/)).toBeInTheDocument();expect(flowApi.group).toHaveBeenCalledWith('same-draft');expect(flowApi.create).toHaveBeenCalledTimes(1)})
  it('normal save carries the same draft id and concurrency version',async()=>{mount({...data,preferred_group:draft,existing_groups:[draft]});await screen.findByText(/同一草稿ID/);fireEvent.click(screen.getByRole('button',{name:'保存本次准备'}));await waitFor(()=>expect(flowApi.create).toHaveBeenCalled());expect(vi.mocked(flowApi.create).mock.calls[0][0]).toMatchObject({group_id:'same-draft',expected_revision:3});expect(flowApi.generate).not.toHaveBeenCalled()})
  it('brings in actual material, retains a saved local override and uses it for preview',async()=>{
    const defaults={actual_material_length:'320',weight_g:'13/11',weight_source:'PRODUCT_ACTUAL'}
    const {unmount}=mount({...data,paper_material_defaults:defaults})
    await waitFor(()=>expect(screen.getByLabelText('本次料重（g）')).toHaveValue('13/11'))
    fireEvent.change(screen.getByLabelText('本次料重（g）'),{target:{value:'14/12'}})
    fireEvent.click(screen.getByRole('button',{name:'预览（不保存，只读可用）'}))
    await waitFor(()=>expect(flowApi.preview).toHaveBeenCalledWith(expect.objectContaining({paper_material:{actual_material_length:'320',weight_g:'14/12'}})))
    unmount()
    mount({...data,paper_material_defaults:defaults,preferred_group:{...draft,snapshot:{...draft.snapshot,paper_material:{actual_material_length:'325',weight_g:''}}}})
    await waitFor(()=>expect(screen.getByLabelText('本次实际料长（mm）')).toHaveValue('325'))
    expect(screen.getByLabelText('本次料重（g）')).toHaveValue('')
  })
  it('restores this-segment process adjustments without changing the master',async()=>{const process_overrides={primary_curing_time:{state:'CONFIRMED',lower:'55',upper:'60',unit:'min',source:'仅本段合成依据'}};mount({...data,preferred_group:{...draft,snapshot:{...draft.snapshot,process_overrides}}});await screen.findByText(/同一草稿ID/);fireEvent.click(screen.getByRole('button',{name:'保存本次准备'}));await waitFor(()=>expect(flowApi.create).toHaveBeenCalled());expect(vi.mocked(flowApi.create).mock.calls[0][0]).toMatchObject({process_overrides});expect(standard.snapshot).not.toHaveProperty('process_overrides')})
  it('readonly can inspect sources and preview, but cannot save or generate',async()=>{mount(data,true);await screen.findByLabelText('第3张预计模数');const preview=await screen.findByRole('button',{name:'预览（不保存，只读可用）'});expect(preview).toBeEnabled();expect(screen.getByRole('button',{name:'保存本次准备'})).toBeDisabled();fireEvent.click(screen.getByRole('button',{name:'查看产品标准'}));expect(await screen.findByText('当前只读，不能修改产品标准')).toBeInTheDocument();expect(screen.getByRole('button',{name:'保存并生效'})).toBeDisabled();fireEvent.click(screen.getByRole('button',{name:/^关\s*闭$/}));fireEvent.click(preview);await screen.findByText(/不落库预览：3张/);expect(flowApi.create).not.toHaveBeenCalled()})
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
  it('shows frozen original standard without the unrelated new-plan missing warning',async()=>{
    mount({...data,preferred_group:{...draft,status:'GENERATED'},auto_standard_id:null,missing:['唯一适用的生效标准']})
    expect(await screen.findByText(/已发 3 张/)).toBeInTheDocument()
    expect(screen.queryByText(/新计划还需处理/)).not.toBeInTheDocument()
    expect(screen.getByText(/原卡 v1/)).toBeInTheDocument()
  })
  it('reopens a pending job without reissuing cards, and can reprint just another batch',async()=>{
    const opener=vi.fn().mockResolvedValue(undefined)
    const results=['one','two','void'].map((id,i)=>({id,group_id:draft.id,card_no:`FC-SYNTH-${id}`,code:`ERPFC:${id}`,sequence:i+1,
      denominator:3,planned_molds:100,original_date:'2026-11-24',status:id==='void'?'VOID':'ACTIVE',
      printed_at:'2026-11-24',reprint_count:0,pending_print_job_id:id==='one'?'old-job':null}))
    vi.mocked(flowApi.cards).mockResolvedValue({count:3,results})
    vi.mocked(flowApi.pendingPrints).mockResolvedValue({count:1,results:[{id:'old-job'} as never]})
    mount({...data,preferred_group:{...draft,status:'GENERATED'}},false,opener)
    fireEvent.click(await screen.findByRole('button',{name:'处理原打印任务'}))
    expect(opener).toHaveBeenCalledWith('old-job',undefined,draft.id)
    fireEvent.click(await screen.findByRole('button',{name:'处理本批待确认打印'}))
    expect(opener).toHaveBeenCalledWith('old-job','one')
    fireEvent.click(await screen.findByRole('button',{name:'补打本批'}))
    expect(print).toHaveBeenCalledWith(['two'],false,'A4_TWO_UP')
    expect(flowApi.create).not.toHaveBeenCalled()
    expect(screen.getByText('已作废')).toBeInTheDocument()
  })
  it('uses the same computed 455 result and adopts it without persisting',async()=>{
    vi.mocked(flowApi.preview).mockResolvedValueOnce({count:5,tail_molds:55,planned_molds:455,card_molds:[100,100,100,100,55],snapshot:{plan_basis:'本次明确安排7268件 ÷ 已确认有效产出16件/模（向上取整）'}})
    mount({...data,run_id:null,suggested_molds:null,available_ranges:[]})
    await waitFor(()=>expect(screen.getByLabelText('默认每张卡模数（自动分卡）')).toHaveValue('100'))
    fireEvent.click(screen.getByRole('button',{name:'预览（不保存，只读可用）'}))
    const adopt=await screen.findByRole('button',{name:'采用此建议：455 模'})
    expect(screen.getAllByText('试算 455 模 → 5 张 · 尾卡 55 模（尚未采用）')).toHaveLength(2)
    fireEvent.click(adopt)
    expect(await screen.findByLabelText('第5张预计模数')).toHaveValue('55')
    expect(screen.getByLabelText('本次预计生产模数')).toHaveValue('455')
    expect(flowApi.create).not.toHaveBeenCalled()
  })
  it('sample includes second inspection choice but does not generate or reserve prints',async()=>{
    vi.mocked(flowApi.sample).mockResolvedValueOnce('blob:test-sample')
    await act(async()=>{mount()})
    await waitFor(()=>expect(screen.getByRole('button',{name:'生成 3 张并准备打印'})).toBeEnabled())
    fireEvent.click(screen.getByRole('checkbox',{name:/本次打印需要二检/}))
    fireEvent.click(screen.getByRole('button',{name:'看末页样张（不发号）'}))
    await waitFor(()=>expect(flowApi.sample).toHaveBeenCalled())
    expect(vi.mocked(flowApi.sample).mock.calls[0][0]).toMatchObject({sample_position:'LAST',second_inspection:true,paper_layout:'A4_TWO_UP'})
    expect(flowApi.create).not.toHaveBeenCalled();expect(flowApi.generate).not.toHaveBeenCalled();expect(print).not.toHaveBeenCalled()
    expect(await screen.findByRole('link',{name:'打开样张PDF（非正式卡）'})).toHaveAttribute('href','blob:test-sample')
  })
})
