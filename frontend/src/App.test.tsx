import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from './App'

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({ matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })),
})

const apiMocks = vi.hoisted(() => ({ session: vi.fn(), publicLocation: vi.fn(), inventoryHistory: vi.fn() }))
vi.mock('./api/client', () => ({
  ApiError: class extends Error { status = 401 },
  authApi: { session: apiMocks.session },
  inventoryApi: { publicLocation: apiMocks.publicLocation, publicLocationHistory: apiMocks.inventoryHistory },
}))
vi.mock('./components/AppShell', async () => {
  const { Outlet } = await import('react-router-dom')
  return { AppShell: () => <Outlet /> }
})
vi.mock('./pages/InventoryPage', () => ({ InventoryLocationPage: () => <div>库存管理页面</div> }))
vi.mock('./pages/MoldRackLocationPage', () => ({ MoldRackLocationPage: ({ readOnly }: { readOnly?: boolean }) => <div>{readOnly ? '模具库位只读页面' : '模具库位管理页面'}</div> }))
vi.mock('./pages/LoginPage', () => ({ LoginPage: () => <div>登录页面</div> }))

const originalUserAgent = navigator.userAgent
function renderRoute(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><App /></MemoryRouter></QueryClientProvider>)
}

describe('QR location routing boundaries', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    Object.defineProperty(navigator, 'userAgent', { configurable: true, value: 'MicroMessenger/8.0' })
    apiMocks.session.mockResolvedValue({ authenticated: true, user: { id: 1, username: 'admin' } })
    apiMocks.publicLocation.mockResolvedValue({ id: 1, code: 'K01-L01-P01', label: '一号架 第一层 第一位', is_active: true, container: null })
    apiMocks.inventoryHistory.mockResolvedValue({ count: 0, next: null, results: [] })
  })
  afterEach(() => Object.defineProperty(navigator, 'userAgent', { configurable: true, value: originalUserAgent }))

  it('opens inventory details and history in WeChat without fetching a session or showing edit controls', async () => {
    const { container } = renderRoute('/inventory/locations/K01-L01-P01')
    expect(await screen.findByText('库存库位 · K01-L01-P01')).toBeInTheDocument()
    expect(await screen.findByText('该库位暂无已登记的历史记录')).toBeInTheDocument()
    expect(screen.queryByText('登录页面')).not.toBeInTheDocument()
    expect(screen.queryByText('库存管理页面')).not.toBeInTheDocument()
    expect(apiMocks.session).not.toHaveBeenCalled()
    expect(apiMocks.publicLocation).toHaveBeenCalledWith('K01-L01-P01')
    expect(container.querySelector('form')).toBeNull()
  })

  it('does not let a WeChat manage parameter bypass the read-only page', async () => {
    renderRoute('/mold-rack/slots/42?mode=manage')
    expect(await screen.findByText('模具库位只读页面')).toBeInTheDocument()
    expect(apiMocks.session).not.toHaveBeenCalled()
  })

  it('preserves authenticated inventory management inside the Android app', async () => {
    Object.defineProperty(navigator, 'userAgent', { configurable: true, value: 'DongXiangERP/1.0' })
    renderRoute('/inventory/locations/K01-L01-P01')
    expect(await screen.findByText('库存管理页面')).toBeInTheDocument()
    expect(apiMocks.session).toHaveBeenCalledTimes(1)
    expect(apiMocks.publicLocation).not.toHaveBeenCalled()
  })

  it('preserves Android mold management when explicitly opened in manage mode', async () => {
    Object.defineProperty(navigator, 'userAgent', { configurable: true, value: 'DongXiangERP/1.0' })
    renderRoute('/mold-rack/slots/42?mode=manage')
    expect(await screen.findByText('模具库位管理页面')).toBeInTheDocument()
    expect(apiMocks.session).toHaveBeenCalledTimes(1)
  })
})
