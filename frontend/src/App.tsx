import { Alert, Button, Result, Skeleton } from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Navigate, Route, Routes } from 'react-router-dom'
import { lazy, Suspense } from 'react'
import { authApi, ApiError } from './api/client'
import { AppShell } from './components/AppShell'
import { LoginPage } from './pages/LoginPage'

const DashboardPage = lazy(() => import('./pages/DashboardPage').then((module) => ({ default: module.DashboardPage })))
const AnalyticsPage = lazy(() => import('./pages/AnalyticsPage').then((module) => ({ default: module.AnalyticsPage })))
const ImportPage = lazy(() => import('./pages/ImportPage').then((module) => ({ default: module.ImportPage })))
const MoldDetailPage = lazy(() => import('./pages/MoldDetailPage').then((module) => ({ default: module.MoldDetailPage })))
const MoldsPage = lazy(() => import('./pages/MoldsPage').then((module) => ({ default: module.MoldsPage })))
const OrdersPage = lazy(() => import('./pages/OrdersPage').then((module) => ({ default: module.OrdersPage })))
const ProductionPage = lazy(() => import('./pages/ProductionPage').then((module) => ({ default: module.ProductionPage })))
const ProductSpecificationsPage = lazy(() => import('./pages/ProductSpecificationsPage').then((module) => ({ default: module.ProductSpecificationsPage })))
const QualityPage = lazy(() => import('./pages/QualityPage').then((module) => ({ default: module.QualityPage })))
const RackConfigPage = lazy(() => import('./pages/RackConfigPage').then((module) => ({ default: module.RackConfigPage })))
const RacksPage = lazy(() => import('./pages/RacksPage').then((module) => ({ default: module.RacksPage })))

export function App() {
  const queryClient = useQueryClient()
  const sessionQuery = useQuery({
    queryKey: ['session'],
    queryFn: authApi.session,
    retry: (count, error) => !(error instanceof ApiError && error.status === 401) && count < 2,
    staleTime: 5 * 60 * 1000,
  })

  if (sessionQuery.isLoading) {
    return <div className="boot-screen"><div className="boot-logo">橡</div><Skeleton active paragraph={{ rows: 2 }} /></div>
  }

  const unauthenticated = sessionQuery.isError && sessionQuery.error instanceof ApiError && sessionQuery.error.status === 401
  if (sessionQuery.isError && !unauthenticated) {
    return (
      <div className="fatal-screen">
        <Alert type="error" showIcon title="无法连接到系统服务" description={(sessionQuery.error as Error).message} />
        <Button onClick={() => sessionQuery.refetch()}>重新连接</Button>
      </div>
    )
  }

  if (unauthenticated || !sessionQuery.data?.authenticated) {
    return <LoginPage onLogin={async (username, password) => {
      const session = await authApi.login(username, password)
      queryClient.setQueryData(['session'], session)
    }} />
  }

  const logout = async () => {
    await authApi.logout()
    queryClient.clear()
    window.location.assign('/')
  }

  return (
    <Suspense fallback={<div className="route-loading"><Skeleton active /></div>}>
      <Routes>
        <Route element={<AppShell user={sessionQuery.data.user} onLogout={logout} />}>
          <Route index element={<DashboardPage />} />
          <Route path="product-specifications" element={<ProductSpecificationsPage />} />
          <Route path="orders" element={<OrdersPage />} />
          <Route path="production" element={<ProductionPage />} />
          <Route path="quality" element={<QualityPage />} />
          <Route path="analytics" element={<AnalyticsPage />} />
          <Route path="molds" element={<MoldsPage />} />
          <Route path="molds/:id" element={<MoldDetailPage />} />
          <Route path="racks" element={<RacksPage />} />
          <Route path="rack-config" element={<RackConfigPage />} />
          <Route path="imports" element={<ImportPage />} />
          <Route path="404" element={<Result status="404" title="页面不存在" extra={<Button type="primary" href="/">返回工作台</Button>} />} />
          <Route path="*" element={<Navigate to="/404" replace />} />
        </Route>
      </Routes>
    </Suspense>
  )
}
