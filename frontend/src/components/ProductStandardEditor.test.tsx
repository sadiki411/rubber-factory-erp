import { App } from 'antd'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ProductStandardEditor } from './ProductStandardEditor'
import { ProductStandardsPanel } from './ProductStandardsPanel'
import { flowApi, flowLimitText, type PreparationContext } from '../flowcards'

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
    expect(screen.getAllByLabelText('下限')).toHaveLength(2)
    expect(screen.getAllByLabelText('上限')).toHaveLength(2)
    expect(screen.getByDisplayValue('线径')).toBeInTheDocument()
    expect(screen.getByDisplayValue('成型硬度')).toBeInTheDocument()
    const inputs=screen.getAllByLabelText('下限');fireEvent.change(inputs[1],{target:{value:'2.95'}})
    fireEvent.change(screen.getAllByLabelText('上限')[1],{target:{value:'3.05'}})
    fireEvent.click(screen.getByRole('tab',{name:'来源与适用范围'}))
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
})
