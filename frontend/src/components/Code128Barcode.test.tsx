import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Code128Barcode } from './Code128Barcode'

describe('Code128Barcode', () => {
  it('renders a scannable vector barcode for a warehouse location', () => {
    const { container } = render(<Code128Barcode value="K01-L01-P01" />)
    expect(screen.getByRole('img', { name: '条形码 K01-L01-P01' })).toBeInTheDocument()
    expect(container.querySelectorAll('svg rect').length).toBeGreaterThan(20)
  })
})
