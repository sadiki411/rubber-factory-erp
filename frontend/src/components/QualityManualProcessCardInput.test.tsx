import { App } from 'antd'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { QualityManualProcessCardInput } from './QualityManualProcessCardInput'

describe('QualityManualProcessCardInput', () => {
  it('normalizes a manually entered card and sends it through the shared callback', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn().mockResolvedValue(true)
    render(<App><QualityManualProcessCardInput onSubmit={onSubmit} /></App>)

    await user.type(screen.getByLabelText('手动输入流程卡单号'), '04-m003-2608210028')
    await user.click(screen.getByRole('button', { name: /添加流程卡/ }))

    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith('04-M003-2608210028'))
    expect(screen.getByLabelText('手动输入流程卡单号')).toHaveValue('')
  })

  it('rejects invalid and duplicate cards before calling the business handler', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(<App><QualityManualProcessCardInput existingValues={['04-M003-2608210028']} onSubmit={onSubmit} /></App>)
    const input = screen.getByLabelText('手动输入流程卡单号')
    const add = screen.getByRole('button', { name: /添加流程卡/ })

    await user.type(input, '0028')
    await user.click(add)
    expect(onSubmit).not.toHaveBeenCalled()

    await user.clear(input)
    await user.type(input, '04-m003-2608210028')
    await user.click(add)
    expect(onSubmit).not.toHaveBeenCalled()
  })
})
