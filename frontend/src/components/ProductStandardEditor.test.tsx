import { App } from 'antd'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ProductStandardEditor } from './ProductStandardEditor'
import { ProductStandardsPanel } from './ProductStandardsPanel'
import { flowApi, flowLimitText, type PreparationContext } from '../flowcards'
import type { CustomerInspection } from '../customerInspection'

vi.mock('../flowcards',async original=>({...await original<typeof import('../flowcards')>(),flowApi:{saveStandard:vi.fn(),activateStandard:vi.fn(),context:vi.fn(),importStandards:vi.fn()}}))
Object.defineProperty(window,'matchMedia',{writable:true,value:vi.fn().mockImplementation((media:string)=>({matches:false,media,addListener:vi.fn(),removeListener:vi.fn(),addEventListener:vi.fn(),removeEventListener:vi.fn()}))})
globalThis.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}}
const appearances=[{id:1,name:'毛刺'},{id:2,name:'杂质'},{id:3,name:'包风'},{id:4,name:'缺料'}]
const context={preparation_enabled:true,write_enabled:true,can_maintain_standard:true,product:{id:7,specification:'SYNTH-100x3',material:'SYNTH-R'},orders:[],order_ids:[],standards:[],appearance:appearances,criteria:[],diagrams:[],existing_groups:[],runs:[],auto_standard_id:null,conflicts:[],missing:[]} as unknown as PreparationContext
function mount(node:React.ReactNode){return render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><App>{node}</App></QueryClientProvider>)}
describe('product-owned standard entry',()=>{
  beforeEach(()=>{vi.clearAllMocks();vi.mocked(flowApi.saveStandard).mockResolvedValue({id:8,version:1} as never);vi.mocked(flowApi.activateStandard).mockResolvedValue({id:8} as never)})
  it('shows hardness and both diameter bounds immediately, saves under the same product',async()=>{
    mount(<ProductStandardEditor data={context} open close={vi.fn()} changed={vi.fn()} />)
    expect(screen.getByText('这里填写标准，不是本批实测值')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button',{name:'增加成型硬度'}))
    fireEvent.click(screen.getByRole('button',{name:'增加线径 / 尺寸要求'}))
    expect(screen.getAllByLabelText('下限')).toHaveLength(2)
    expect(screen.getAllByLabelText('上限')).toHaveLength(2)
    expect(screen.getByDisplayValue('线径')).toBeInTheDocument()
    expect(screen.getByDisplayValue('成型硬度')).toBeInTheDocument()
    for(const field of ['测点','读数时间','客户认可测法','测量对象','测量方法']) expect(screen.queryByLabelText(field)).not.toBeInTheDocument()
    const inputs=screen.getAllByLabelText('下限');fireEvent.change(inputs[1],{target:{value:'2.95'}})
    fireEvent.change(screen.getAllByLabelText('上限')[1],{target:{value:'3.05'}})
    fireEvent.click(screen.getByRole('tab',{name:'来源与适用范围'}))
    expect(screen.queryByLabelText('客户图号')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('图纸版次')).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('来源文件、版号 / 人工确认依据'),{target:{value:'SYNTH依据A01'}})
    fireEvent.click(screen.getByRole('button',{name:'保存并生效'}))
    await waitFor(()=>expect(flowApi.activateStandard).toHaveBeenCalledWith(8))
    expect(flowApi.saveStandard).toHaveBeenCalledWith(expect.objectContaining({product_id:7,dimension_count:1,source:'SYNTH依据A01',criteria:expect.arrayContaining([expect.objectContaining({name:'线径',lower:'2.95',upper:'3.05',limit_mode:'RANGE'})])}))
  })
  it('read-only display distinguishes failed read from missing standard',async()=>{
    vi.mocked(flowApi.context).mockRejectedValue(new Error('synthetic read failure'))
    mount(<ProductStandardsPanel productId={7} />)
    expect(await screen.findByText('标准读取失败，不代表没有标准')).toBeInTheDocument()
    expect(screen.queryByText(/尚无已确认产品标准/)).not.toBeInTheDocument()
  })
  it('retains lower-only strictness and suppresses save for non-maintainer',()=>{
    expect(flowLimitText({limit_mode:'LOWER',lower:'2.95',unit:'mm',lower_inclusive:false})).toBe('> 2.95 mm')
    mount(<ProductStandardEditor data={{...context,can_maintain_standard:false}} open close={vi.fn()} changed={vi.fn()} />)
    expect(screen.getByRole('button',{name:'保存并生效'})).toBeDisabled()
    expect(within(screen.getByRole('dialog')).getByText('当前只读，不能修改产品标准')).toBeInTheDocument()
  })
  it('uses text for oil and hides irrelevant bake conditions, saving no-bake without bounds',async()=>{
    mount(<ProductStandardEditor data={context} open close={vi.fn()} changed={vi.fn()} />)
    expect(screen.queryByText('烤后硬度的对象与方法')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab',{name:'工艺与二烤'}))
    const text=within(screen.getByTestId('text-process-fields'))
    expect(text.queryByText('下限')).not.toBeInTheDocument()
    expect(text.queryByText('上限')).not.toBeInTheDocument()
    fireEvent.change(text.getByRole('combobox',{name:'毛边洒油'}),{target:{value:'少量'}})
    expect(screen.queryByLabelText('二烤温度（℃）')).not.toBeInTheDocument()
    fireEvent.mouseDown(screen.getByLabelText('是否需要二烤'))
    fireEvent.click(await screen.findByText('无需二烤'))
    fireEvent.click(screen.getByRole('tab',{name:'来源与适用范围'}))
    fireEvent.change(screen.getByLabelText('来源文件、版号 / 人工确认依据'),{target:{value:'SYNTH文字要求'}})
    fireEvent.click(screen.getByRole('button',{name:'保存草稿'}))
    await waitFor(()=>expect(flowApi.saveStandard).toHaveBeenCalled())
    const body=vi.mocked(flowApi.saveStandard).mock.calls[0][0]
    expect(body).toEqual(expect.objectContaining({bake:{state:'NOT_APPLICABLE'},route:expect.objectContaining({bake:false,baked_hardness:false}),process_parameters:{oil:{raw_text:'少量'}}}))
  })
  it('opens a specified bake value in one field, not a mandatory range',()=>{
    const data={...context,auto_standard_id:9,standards:[{id:9,status:'ACTIVE',source:'SYNTH条件',snapshot:{criteria:[],bake:{state:'APPLICABLE',temperature_lower:'145',temperature_upper:'145',minutes_lower:'90',minutes_upper:'90'},process_parameters:{oil:{raw_text:'禁止',state:'RAW_UNCONFIRMED'}}}}]} as unknown as PreparationContext
    mount(<ProductStandardEditor data={data} open close={vi.fn()} changed={vi.fn()} />)
    fireEvent.click(screen.getByRole('tab',{name:'工艺与二烤'}))
    expect(screen.getByLabelText('二烤温度（℃）')).toHaveValue('145')
    expect(screen.getByLabelText('二烤时间（min）')).toHaveValue('90')
    expect(screen.queryByLabelText('二烤温度下限（℃）')).not.toBeInTheDocument()
    expect(screen.getByRole('combobox',{name:'毛边洒油'})).toHaveValue('禁止')
  })
  it('shows the same 6001 hardness in both phases, keeping 2001 only in the source section',async()=>{
    const adopt={order_id:21,order_no:'SYN-21',item_no:'1',customer_code:'L',project_no:'SYN-163',adoption_key:'bundle',status:'CONFIRMED',groups:[],
      selection:{policy:'6001-primary-v1',main:[{key:'HD',item:'HD',stage:'MOLDING_HARDNESS',category:'6001',lower:'66',upper:'74',unit:'度',version:'D00',row:8},
        {key:'C/S-A',item:'C/S-A',stage:'DIMENSION',category:'6001',lower:'2.9',upper:'3.1',unit:'mm',version:'D00',row:9}],
        supplements:[{key:'FLASH',item:'FLASH',stage:null,category:'2001',lower:'0',upper:'0.2',unit:'mm',version:'F01',row:6}],problems:[],warnings:[]},
      reference:{primary:{state:'REFERENCE',raw:'时间60-65min 温度178-183℃',time_lower:'60',time_upper:'65',temperature_lower:'178',temperature_upper:'183'},
        secondary:{state:'REFERENCE',raw:'时间120min温度150°C',time_lower:'120',time_upper:'120',temperature_lower:'150',temperature_upper:'150'}}} as unknown as CustomerInspection
    const onActivated=vi.fn()
    mount(<ProductStandardEditor data={context} open adopt={adopt} initialTab="process" close={vi.fn()} changed={vi.fn()} onActivated={onActivated} />)
    expect(screen.getByLabelText('模温下限（℃）')).toHaveValue('178')
    expect(screen.getByLabelText('模温上限（℃）')).toHaveValue('183')
    expect(screen.getByLabelText('二烤温度（℃）')).toHaveValue('150')
    expect(screen.getByLabelText('二烤时间（min）')).toHaveValue('120')
    expect(screen.queryByLabelText('上模设定温度')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab',{name:'硬度与尺寸'}))
    expect(screen.getAllByDisplayValue('74')).toHaveLength(2)
    expect(screen.getAllByDisplayValue('66').every(e=>e.hasAttribute('readonly'))).toBe(true)
    expect(screen.queryByDisplayValue('0.2')).not.toBeInTheDocument()
    const dimension=screen.getAllByRole('checkbox',{name:/印到流转卡/})[2]
    expect(dimension).not.toBeChecked()
    fireEvent.click(dimension)
    fireEvent.click(screen.getByRole('tab',{name:'来源与适用范围'}))
    fireEvent.click(screen.getByText(/2001 补缺参考/))
    expect(screen.getByText(/FLASH（毛边，量法待确认）：0 ～ 0.2 mm/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab',{name:'打印项目'}))
    fireEvent.click(screen.getByRole('radio',{name:'不需整模'}))
    fireEvent.click(screen.getByRole('button',{name:'保存并用于本单制卡'}))
    await waitFor(()=>expect(onActivated).toHaveBeenCalledWith(expect.objectContaining({id:8})))
    expect(flowApi.saveStandard).toHaveBeenCalledWith(expect.objectContaining({customer_reference:{key:'bundle'},applicable_order_id:21,dimension_count:1,
      criteria:[
        expect.objectContaining({customer_item:'HD',stage:'MOLDING_HARDNESS',lower:'66',upper:'74',follow_package:true}),
        expect.objectContaining({customer_item:'HD',stage:'BAKED_HARDNESS',lower:'66',upper:'74',follow_package:true}),
        expect.objectContaining({customer_item:'C/S-A',stage:'DIMENSION',follow_package:true}),
      ],
      process_parameters:expect.objectContaining({mold_temperature_range:expect.objectContaining({lower:'178',upper:'183',unit:'℃'}),primary_curing_time:expect.objectContaining({lower:'60',upper:'65',unit:'s'})})}))
  })
})
