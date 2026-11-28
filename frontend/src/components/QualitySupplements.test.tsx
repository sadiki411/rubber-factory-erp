import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QualitySupplements, type SupplementStart } from './QualitySupplements'

const apiFetch = vi.hoisted(() => vi.fn())
vi.mock('../api/client', () => ({ apiFetch, productSpecificationApi: {}, toList: vi.fn() }))
vi.mock('../flowcards', () => ({ flowRequestKey: vi.fn(() => 'isolated-request'), finishFlowRequest: vi.fn() }))
vi.mock('./QualityQrScanner', () => ({ QualityQrScanner: () => null }))
Object.defineProperty(window, 'matchMedia', {
  writable: true, value: (media: string) => ({ media, matches: false, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() }),
})
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }

const origin = {
  id: 9, batch_id: 12, unit_no: 1, product_id: 7, shipment_no: 'QS-SYNTH-OLD', card_no: 'SYNTH-CARD', card_no_snapshot: 'SYNTH-CARD',
  batch__shipment_no: 'QS-SYNTH-OLD', date: '2026-11-25', specification: 'SYNTH-215', material: 'SYNTH-MAT',
  customer_code: 'SYNTH-CUSTOMER', project_no: '', is_supplement: false,
}
const issue = {
  id: 5, reference: 'BH-SYNTH', kind: 'REPLACEMENT', reason: '合成硬度异常', specification: origin.specification, material: origin.material,
  customer_code: origin.customer_code, project_no: '', product_id: 7, package_count: 0, requested_quantity: 100,
  remaining: 100, shipped_quantity: 0, unknown_packages: 0, retest_note: '', disputed_quantity: null, dispute_note: '',
  moved_out: false, origins: [origin], shipments: [], delivery_releases: [],
  release_options: [{ origin_id: 9, allocation_id: 20, order_no: 'SYNTH-ORDER', item_no: '02', max_quantity: 100, expected_released: 0,
    shipment_no: origin.shipment_no, unit_no: 1 }],
}

function open(start: SupplementStart, canReconcile: boolean) {
  const onSaved = vi.fn()
  const query = new QueryClient({defaultOptions:{queries:{retry:false}}})
  render(<QueryClientProvider client={query}><App><QualitySupplements start={start} employees={[]} canReconcile={canReconcile} readOnly={false}
    onClose={vi.fn()} onSaved={onSaved} onReturn={vi.fn()} onOriginal={vi.fn()} /></App></QueryClientProvider>)
  return onSaved
}

beforeEach(() => { apiFetch.mockReset() })

it('uses a scanned old card to select its original package without creating a return', async () => {
  apiFetch.mockResolvedValue({origins:[origin], replacements:[], related_issues:[], unverified_shipment_ids:[]})
  open({code:'SYNTH-CARD'}, false)
  fireEvent.click(await screen.findByRole('button', {name:'沿原卡补发新货'}))
  expect(await screen.findByText(/沿用原卡号 SYNTH-CARD/)).toBeInTheDocument()
  expect(screen.getByRole('button',{name:/尚未发出/})).toBeInTheDocument()
  expect(apiFetch.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
})

it('requires customer proof and the exact order share, then posts the delivery release once', async () => {
  const released = {...issue, release_options: [], delivery_releases:[{id:3, shipment_no: origin.shipment_no, unit_no:1,
    order_no:'SYNTH-ORDER', item_no:'02', quantity:100, note:'合成客户确认', active:true}]}
  let posted = false
  apiFetch.mockImplementation(async (url, init) => {
    if (url.endsWith('/delivery-release/') && init?.method === 'POST') { posted = true; return {issue_id:5, batch_id:null} }
    if (url.endsWith('/issues/5/')) return posted ? released : issue
    throw new Error('Unexpected endpoint')
  })
  const onSaved = open({issueId:5}, true)
  fireEvent.click(await screen.findByRole('button', {name:/核减 SYNTH-ORDER/}))
  fireEvent.change(screen.getByRole('spinbutton',{name:'本次确认不抵交付的件数'}), {target:{value:'100'}})
  fireEvent.change(screen.getByRole('textbox',{name:/翔邦确认依据/}), {target:{value:'合成客户确认'}})
  fireEvent.click(screen.getByRole('button',{name:'确认核减有效交付'}))
  expect(await screen.findByText('请先确认翔邦明确原货不抵交付')).toBeInTheDocument()
  expect(posted).toBe(false)
  fireEvent.click(screen.getByRole('checkbox',{name:/翔邦已确认/}))
  fireEvent.click(screen.getByRole('button',{name:'确认核减有效交付'}))
  await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1))
  const post = apiFetch.mock.calls.find(([, init]) => init?.method === 'POST')
  expect(post?.[0]).toBe('/api/quality/supplements/issues/5/delivery-release/')
  expect(JSON.parse(post?.[1].body)).toMatchObject({origin_id:9, allocation_id:20, expected_released:0, quantity:100,
    original_delivery_excluded:true, note:'合成客户确认'})
  expect(await screen.findByText(/有效交付已更新/)).toBeInTheDocument()
})
