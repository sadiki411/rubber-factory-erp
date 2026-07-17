import {
  AppstoreOutlined,
  BarChartOutlined,
  FileExcelOutlined,
  HomeOutlined,
  LogoutOutlined,
  MenuOutlined,
  OrderedListOutlined,
  ProfileOutlined,
  SafetyCertificateOutlined,
  ScheduleOutlined,
  SettingOutlined,
  ToolOutlined,
} from '@ant-design/icons'
import { Avatar, Button, Drawer, Dropdown, Grid, Layout, Menu, Space, Typography } from 'antd'
import { useMemo, useState } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import type { User } from '../types'

const { Header, Sider, Content } = Layout

const menuItems = [
  { key: '/', icon: <HomeOutlined />, label: '工作台' },
  { key: '/product-specifications', icon: <ProfileOutlined />, label: '产品规格资料' },
  { key: '/orders', icon: <OrderedListOutlined />, label: '订单管理' },
  { key: '/production', icon: <ScheduleOutlined />, label: '前端生产' },
  { key: '/quality', icon: <SafetyCertificateOutlined />, label: '品检出货' },
  { key: '/analytics', icon: <BarChartOutlined />, label: '数据分析' },
  { key: '/molds', icon: <ToolOutlined />, label: '模具台账' },
  { key: '/racks', icon: <AppstoreOutlined />, label: '货架总览' },
  { key: '/rack-config', icon: <SettingOutlined />, label: '货架配置' },
  { key: '/imports', icon: <FileExcelOutlined />, label: '模具 Excel 导入' },
]

interface Props {
  user?: User
  onLogout: () => Promise<void>
}

export function AppShell({ user, onLogout }: Props) {
  const navigate = useNavigate()
  const location = useLocation()
  const screens = Grid.useBreakpoint()
  const desktop = !!screens.lg
  const [drawerOpen, setDrawerOpen] = useState(false)
  const selectedKey = useMemo(() => {
    const item = [...menuItems].reverse().find((entry) => entry.key === '/' ? location.pathname === '/' : location.pathname.startsWith(entry.key))
    return item?.key || '/'
  }, [location.pathname])

  const menu = (
    <Menu
      mode="inline"
      selectedKeys={[selectedKey]}
      items={menuItems}
      onClick={({ key }) => {
        navigate(key)
        setDrawerOpen(false)
      }}
    />
  )

  return (
    <Layout className="app-layout">
      {desktop ? (
        <Sider width={236} theme="light" className="app-sider">
          <div className="brand-block">
            <div className="brand-mark">橡</div>
            <div>
              <Typography.Title level={4}>橡胶工厂 ERP</Typography.Title>
              <Typography.Text type="secondary">生产 · 品检 · 模具</Typography.Text>
            </div>
          </div>
          <div className="sider-menu-scroll">{menu}</div>
          <div className="sider-footer">生产 · 品检 · 模具</div>
        </Sider>
      ) : (
        <Drawer
          open={drawerOpen}
          onClose={() => setDrawerOpen(false)}
          placement="left"
          size={286}
          styles={{ body: { padding: 0 } }}
          title={<span className="mobile-drawer-title"><span className="brand-mark small">橡</span> 橡胶工厂 ERP</span>}
        >
          {menu}
        </Drawer>
      )}

      <Layout>
        <Header className="app-header">
          <Space>
            {!desktop && <Button type="text" className="mobile-nav-button" icon={<MenuOutlined />} onClick={() => setDrawerOpen(true)} aria-label="打开导航" />}
            {!desktop && <Typography.Text strong>橡胶工厂 ERP</Typography.Text>}
          </Space>
          <Dropdown
            trigger={['click']}
            menu={{
              items: [{ key: 'logout', label: '退出登录', icon: <LogoutOutlined /> }],
              onClick: ({ key }) => key === 'logout' && void onLogout(),
            }}
          >
            <Button type="text" className="user-button">
              <Avatar size="small">{(user?.display_name || user?.username || '管').slice(0, 1)}</Avatar>
              <span className="user-name">{user?.display_name || user?.username || '管理员'}</span>
            </Button>
          </Dropdown>
        </Header>
        <Content className="app-content">
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
