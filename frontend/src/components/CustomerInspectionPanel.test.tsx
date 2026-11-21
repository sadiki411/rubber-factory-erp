import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { customerInspectionApi, type CustomerInspection } from '../customerInspection'
import { OrderCustomerInspectionPanel } from './CustomerInspectionPanel'

Object.defineProperty(window, 'matchMedia', { writable: true, value: (query: string) => ({
  matches: false, media: query, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(),
}) })

const sample: CustomerInspection = {
  order_id: 5, order_no: 'SYN-1', item_no: '1', customer_code: 'J', project_no: 'SYN-P', source: '翔邦', supplier: 'SYN-S',
  status: 'PENDING', digest: 'synthetic', can_review: true, problems: [],
  reference: {
    primary: {state:'REFERENCE', raw:'时间160-180min 温度175-180℃', time_lower:'160', time_upper:'180', temperature_lower:'175', temperature_upper:'180', unit:'s', corrected_unit:true},
    secondary: {state:'NOT_REQUIRED', raw:'时间0min温度0°C'},
    mold_no:'SYN', mold_size:'500x500', material_length:'350', cut_weight:'9',
  },
  groups: ['2001','6001'].map((category,i)=>({customer_code:'J',category,project_no:'SYN-P',version:'F02',rows:[
    {item:'HD',lower:'61',upper:i?'69':'65',unit:'度',file:'合成.xlsx',batch:'synthetic-batch',sheet:'Sheet1',row:i+3},
  ]})),
}

function mount() {
  return render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}>
    <App><OrderCustomerInspectionPanel orderId={5} /></App>
  </QueryClientProvider>)
}

afterEach(()=>vi.restoreAllMocks())

it('shows separate unclassified categories and prints a separate PDF without approval', async () => {
  vi.spyOn(customerInspectionApi,'get').mockResolvedValue({inspection:sample})
  const update=vi.spyOn(customerInspectionApi,'update')
  mount()
  const print=await screen.findByRole('link',{name:'打印标准 PDF'})
  expect(print).toHaveAttribute('href','/api/orders/orders/5/customer-inspection/print/')
  fireEvent.click(screen.getByText(/类别 2001/))
  fireEvent.click(screen.getByText(/类别 6001/))
  expect(await screen.findByText('下限 61 · 上限 65 · 度')).toBeVisible()
  expect(screen.getByText('下限 61 · 上限 69 · 度')).toBeVisible()
  expect(screen.getByText(/160–180 s/)).toBeInTheDocument()
  expect(screen.getByText(/不需要二烤/)).toBeInTheDocument()
  expect(update).not.toHaveBeenCalled()
})

it('confirms only an explicitly reviewed fingerprint', async () => {
  vi.spyOn(customerInspectionApi,'get').mockResolvedValue({inspection:sample})
  const update=vi.spyOn(customerInspectionApi,'update').mockResolvedValue({inspection:{...sample,status:'CONFIRMED'}})
  mount()
  fireEvent.click(await screen.findByRole('button',{name:'核对并确认本单标准'}))
  expect(update).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button',{name:'确认原表要求'}))
  await waitFor(()=>expect(update).toHaveBeenCalledWith(5,{action:'confirm',digest:'synthetic'}))
})
