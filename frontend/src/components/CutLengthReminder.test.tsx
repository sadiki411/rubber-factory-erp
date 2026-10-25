import { App } from 'antd'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { CutLengthReminder } from './CutLengthReminder'
import type { CutLengthCheck } from '../types'

Object.defineProperty(window, 'matchMedia', { writable: true, value: vi.fn().mockImplementation((query: string) => ({
  matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(),
  addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
})) })

const check: CutLengthCheck = {
  version: 1, tolerance_mm: '5', digest: 'snapshot', alert_count: 1,
  rows: [{ row_key: 'one', sheet: 'sheet1', row: 4, order_no: 'TEST-ORDER', item_no: '2', specification: '240', material: 'N7200', product_id: 1,
    customer_length: '275', actual_length: '300', customer_mm: '275', actual_mm: '300', difference_mm: '-25', status: 'SHORTER', message: '客户偏短25mm', candidates: [] }],
}

it('copies the exact order-level feedback including the manual baseline', async () => {
  const user = userEvent.setup()
  render(<App><CutLengthReminder check={check} token="test-token" /></App>)
  await user.click(screen.getByRole('button', { name: '复制反馈清单' }))
  expect(await navigator.clipboard.readText()).toContain('TEST-ORDER / 项次2 / 240 / N7200：客户275，实际300，差额-25')
})

it('renders committed confirmation as a read-only snapshot without refresh or acknowledgement', () => {
  render(<App><CutLengthReminder check={check} token="test-token" confirmation={{ digest: 'snapshot', user_id: 1, username: 'test-importer', confirmed_at: '2026-10-25T08:00:00Z', meaning: '已知悉并导入，不代表客户已更正' }} /></App>)
  expect(screen.getByText(/test-importer 已于/)).toBeInTheDocument()
  expect(screen.getByText(/快照，不随产品档案后续修改而重算/)).toBeInTheDocument()
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '重新核对实际料长' })).not.toBeInTheDocument()
})

it('does not request acknowledgement when all rows are within tolerance', () => {
  const onChange = vi.fn()
  render(<App><CutLengthReminder check={{ ...check, alert_count: 0, rows: [{ ...check.rows[0], status: 'IN_TOLERANCE', difference_mm: '-5' }] }} token="test-token" onAcknowledgedChange={onChange} /></App>)
  expect(screen.getByText('1 个订单项次的料长差值均在±5mm以内')).toBeInTheDocument()
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  expect(screen.queryByRole('link', { name: '下载料长提醒清单' })).not.toBeInTheDocument()
})
