import { App } from 'antd'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QualityQrScanner } from './QualityQrScanner'

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

describe('QualityQrScanner', () => {
  it('accepts the customer QR value and prevents a duplicate in one continuous session', async () => {
    const onScan = vi.fn().mockResolvedValue(true)
    render(<App><QualityQrScanner open onClose={vi.fn()} onScan={onScan} /></App>)

    const input = screen.getByPlaceholderText(/04-M003-2608210028/)
    fireEvent.change(input, { target: { value: '04-M003-2608210028' } })
    fireEvent.click(screen.getByRole('button', { name: /加入/ }))
    await waitFor(() => expect(onScan).toHaveBeenCalledWith('04-M003-2608210028'))
    expect(screen.getByText('本次已扫 1 张')).toBeInTheDocument()

    fireEvent.change(input, { target: { value: '04-M003-2608210028' } })
    fireEvent.click(screen.getByRole('button', { name: /加入/ }))
    await waitFor(() => expect(onScan).toHaveBeenCalledTimes(1))
  })
})
