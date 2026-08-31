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

  it('clears the manual value and duplicate guard after closing, then accepts the same card in a new session', async () => {
    const onScan = vi.fn().mockResolvedValue(true)
    const onClose = vi.fn()
    const { rerender } = render(<App><QualityQrScanner open onClose={onClose} onScan={onScan} /></App>)

    const input = screen.getByPlaceholderText(/04-M003-2608210028/)
    fireEvent.change(input, { target: { value: '04-M003-2608210028' } })
    fireEvent.click(screen.getByRole('button', { name: /加入/ }))
    await waitFor(() => expect(onScan).toHaveBeenCalledTimes(1))

    rerender(<App><QualityQrScanner open={false} onClose={onClose} onScan={onScan} /></App>)
    await waitFor(() => {
      expect(input).toHaveValue('')
      expect(screen.getByText('本次已扫 0 张')).toBeInTheDocument()
    })

    rerender(<App><QualityQrScanner open onClose={onClose} onScan={onScan} /></App>)
    fireEvent.change(input, { target: { value: '04-M003-2608210028' } })
    fireEvent.click(screen.getByRole('button', { name: /加入/ }))
    await waitFor(() => expect(onScan).toHaveBeenCalledTimes(2))
  })

  it('does not restart the phone camera when the parent supplies fresh callbacks or seed arrays', async () => {
    const originalSecureContext = Object.getOwnPropertyDescriptor(window, 'isSecureContext')
    const originalMediaDevices = Object.getOwnPropertyDescriptor(navigator, 'mediaDevices')
    const originalBarcodeDetector = Object.getOwnPropertyDescriptor(window, 'BarcodeDetector')
    const stop = vi.fn()
    const track = {
      stop,
      getCapabilities: vi.fn().mockReturnValue({}),
      applyConstraints: vi.fn().mockResolvedValue(undefined),
    }
    const getUserMedia = vi.fn().mockResolvedValue({
      getTracks: () => [track],
      getVideoTracks: () => [track],
    })
    class MockBarcodeDetector {
      static getSupportedFormats = vi.fn().mockResolvedValue(['qr_code'])
      detect = vi.fn().mockResolvedValue([])
    }

    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true })
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia } })
    Object.defineProperty(window, 'BarcodeDetector', { configurable: true, value: MockBarcodeDetector })
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined)

    try {
      const firstScan = vi.fn().mockResolvedValue(true)
      const secondScan = vi.fn().mockResolvedValue(true)
      const { rerender, unmount } = render(<App><QualityQrScanner open initialValues={[]} onClose={vi.fn()} onScan={firstScan} /></App>)
      await waitFor(() => expect(getUserMedia).toHaveBeenCalledTimes(1))

      rerender(<App><QualityQrScanner open initialValues={[...[]]} onClose={vi.fn()} onScan={secondScan} /></App>)
      await new Promise((resolve) => window.setTimeout(resolve, 180))
      expect(getUserMedia).toHaveBeenCalledTimes(1)

      const input = screen.getByPlaceholderText(/04-M003-2608210028/)
      fireEvent.change(input, { target: { value: '04-M003-2608210029' } })
      fireEvent.click(screen.getByRole('button', { name: /加入/ }))
      await waitFor(() => expect(secondScan).toHaveBeenCalledWith('04-M003-2608210029'))
      expect(firstScan).not.toHaveBeenCalled()

      unmount()
      expect(stop).toHaveBeenCalled()
    } finally {
      play.mockRestore()
      if (originalSecureContext) Object.defineProperty(window, 'isSecureContext', originalSecureContext)
      else Reflect.deleteProperty(window, 'isSecureContext')
      if (originalMediaDevices) Object.defineProperty(navigator, 'mediaDevices', originalMediaDevices)
      else Reflect.deleteProperty(navigator, 'mediaDevices')
      if (originalBarcodeDetector) Object.defineProperty(window, 'BarcodeDetector', originalBarcodeDetector)
      else Reflect.deleteProperty(window, 'BarcodeDetector')
    }
  })
})
