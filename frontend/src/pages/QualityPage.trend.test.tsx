import { render, screen } from '@testing-library/react'
import type { QualityDailyTrend } from '../types'
import { DailyTrend } from './QualityPage'

describe('quality daily event display', () => {
  it('keeps missing inspection/rework distinct from zero on a shipment day', () => {
    const row = { date: '2026-10-13', shipped_quantity: 100, inspection_quantity: 0, reworked_quantity: 0, inspection_record_count: 0, rework_record_count: 0 } as QualityDailyTrend
    render(<DailyTrend rows={[row]} loading={false} />)
    expect(screen.getByText('质检 未登记')).toBeInTheDocument()
    expect(screen.getByText('返工 未登记')).toBeInTheDocument()
    expect(screen.getByText('出货 100')).toBeInTheDocument()
  })

  it('shows a real zero-valued event day rather than an empty business conclusion', () => {
    const row = { date: '2026-10-13', shipped_quantity: 0, inspection_quantity: 0, reworked_quantity: 0, inspection_record_count: 1, rework_record_count: 1 } as QualityDailyTrend
    render(<DailyTrend rows={[row]} loading={false} />)
    expect(screen.getByText('质检 0')).toBeInTheDocument()
    expect(screen.getByText('返工 0')).toBeInTheDocument()
    expect(screen.queryByText('所选日期暂无质检、出货或返工记录')).not.toBeInTheDocument()
  })
})
