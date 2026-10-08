import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ThermalLocationLabel } from './ThermalLocationLabel'

vi.mock('antd', () => ({ QRCode: ({ value }: { value: string }) => <svg className="ant-qrcode" data-value={value} /> }))

describe('70x50 thermal location label', () => {
  it.each(['inventory', 'mold-rack'] as const)('uses the same page artwork for %s and retains the original barcode/QR identity', (kind) => {
    const code = 'J01-06-L01-A-P01'
    const url = 'https://erp.qfylagent.org/mold-rack/slots/42'
    const { container } = render(<ThermalLocationLabel kind={kind} code={code} detailUrl={url} />)
    expect(container.querySelector('.thermal-label-row')).toBeTruthy()
    expect(container.querySelector(`.${kind}-label-codes .inventory-code128`)).toHaveAttribute('aria-label', `条形码 ${code}`)
    expect(container.querySelector('.ant-qrcode')).toHaveAttribute('data-value', url)
    const text = container.querySelector('.thermal-location-code text')!
    expect(text.textContent).toBe(code)
    expect(Number(text.getAttribute('font-size')) * 0.6 * code.length).toBeLessThanOrEqual(630.01)
    expect(text).toHaveAttribute('text-anchor', 'middle')
    expect(container.querySelector('b')).toBeNull()
  })

  it('fits longer legacy location codes without truncation', () => {
    const code = 'J01-06-L01-A-P01-02'
    const { container } = render(<ThermalLocationLabel kind="mold-rack" code={code} detailUrl="https://erp.qfylagent.org/mold-rack/slots/7" />)
    const text = container.querySelector('.thermal-location-code text')!
    expect(text.textContent).toBe(code)
    expect(Number(text.getAttribute('font-size')) * 0.6 * code.length).toBeLessThanOrEqual(630.01)
  })
})
