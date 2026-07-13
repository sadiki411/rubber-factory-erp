import { render, screen } from '@testing-library/react'
import { StatusTag } from './StatusTag'

describe('StatusTag', () => {
  it.each([
    ['IN_STOCK', '在库'],
    ['ON_MACHINE', '上机'],
    ['OUTSOURCED', '外出加工'],
  ] as const)('shows %s as Chinese label', (status, label) => {
    render(<StatusTag status={status} />)
    expect(screen.getByText(label)).toBeInTheDocument()
  })
})
