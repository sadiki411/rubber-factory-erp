import React from 'react'
import ReactDOM from 'react-dom/client'
import { App as AntApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import { App } from './App'
import './styles.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: '#12624f',
          colorInfo: '#2f6f9f',
          colorSuccess: '#26845f',
          colorWarning: '#c98019',
          borderRadius: 10,
          fontFamily: "Inter, 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif",
          controlHeight: 40,
        },
        components: {
          Button: { primaryShadow: 'none', fontWeight: 600 },
          Card: { headerFontSize: 16 },
          Menu: { itemBorderRadius: 8, itemHeight: 44 },
          Table: { headerBg: '#f4f7f6', headerColor: '#36534d' },
        },
      }}
    >
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>
  </React.StrictMode>,
)
