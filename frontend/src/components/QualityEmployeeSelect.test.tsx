import { App } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { QualityEmployeeSelect } from './QualityEmployeeSelect'

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation(() => ({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })),
})

const apiMocks = vi.hoisted(() => ({
  quickResolveEmployee: vi.fn(),
}))

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
    qualityApi: { ...actual.qualityApi, quickResolveEmployee: apiMocks.quickResolveEmployee },
  }
})

function renderSelect(onChange = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <App>
        <QualityEmployeeSelect employees={[]} multiple value={[]} onChange={onChange} />
      </App>
    </QueryClientProvider>,
  )
  return onChange
}

describe('QualityEmployeeSelect', () => {
  beforeEach(() => apiMocks.quickResolveEmployee.mockReset())

  it('shows a direct employee entry path when the archive is empty', () => {
    renderSelect()

    expect(screen.getByText('尚无品检员，请在下方新增')).toBeInTheDocument()
    expect(screen.getByLabelText('快速新增品检员姓名')).toBeInTheDocument()
    expect(screen.getByText(/员工库为空也可直接继续录入/)).toBeInTheDocument()
  })

  it('creates an inspector and immediately selects it', async () => {
    const employee = {
      id: 18,
      employee_no: 'EMP-260916-ABC12345',
      name: '林品检',
      role: 'INSPECTOR' as const,
      is_active: true,
    }
    apiMocks.quickResolveEmployee.mockResolvedValue(employee)
    const onChange = renderSelect()
    const user = userEvent.setup()

    await user.type(screen.getByLabelText('快速新增品检员姓名'), '林品检')
    await user.click(screen.getByRole('button', { name: /新增并选中/ }))

    await waitFor(() => expect(apiMocks.quickResolveEmployee).toHaveBeenCalledWith({
      name: '林品检',
      purpose: 'INSPECTOR',
    }))
    await waitFor(() => expect(onChange).toHaveBeenCalledWith([18]))
    expect(screen.getByText('选择品检员')).toBeInTheDocument()
  })

})
