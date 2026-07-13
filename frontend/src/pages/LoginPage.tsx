import { LockOutlined, SafetyCertificateOutlined, UserOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Form, Input, Space, Typography } from 'antd'
import { useState } from 'react'

interface Props {
  onLogin: (username: string, password: string) => Promise<void>
}

export function LoginPage({ onLogin }: Props) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const submit = async (values: { username: string; password: string }) => {
    setLoading(true)
    setError('')
    try {
      await onLogin(values.username, values.password)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '登录失败，请检查账号和密码')
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="login-page">
      <section className="login-intro">
        <div className="login-logo">橡</div>
        <Typography.Title>生产、品检、模具，一套系统</Typography.Title>
        <Typography.Paragraph>
          统一管理生产进度、品检出货、退货返工与模具流向，让橡胶工厂的关键记录清晰可追溯。
        </Typography.Paragraph>
        <Space orientation="vertical" size="middle" className="login-benefits">
          <span><SafetyCertificateOutlined /> 质检与返工绩效清晰</span>
          <span><SafetyCertificateOutlined /> 生产和模具状态全程留痕</span>
          <span><SafetyCertificateOutlined /> 手机与电脑均可使用</span>
        </Space>
      </section>
      <Card className="login-card" variant="borderless">
        <div className="login-card-heading">
          <Typography.Title level={2}>欢迎回来</Typography.Title>
          <Typography.Text type="secondary">请使用工厂共用账号登录</Typography.Text>
        </div>
        {error && <Alert type="error" showIcon title={error} />}
        <Form layout="vertical" size="large" onFinish={submit} requiredMark={false}>
          <Form.Item name="username" label="账号" rules={[{ required: true, message: '请输入账号' }]}>
            <Input prefix={<UserOutlined />} autoComplete="username" placeholder="请输入账号" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password prefix={<LockOutlined />} autoComplete="current-password" placeholder="请输入密码" />
          </Form.Item>
          <Button block type="primary" htmlType="submit" loading={loading}>登录系统</Button>
        </Form>
        <Typography.Paragraph type="secondary" className="login-tip">账号由管理员在部署时设置，不开放自行注册。</Typography.Paragraph>
      </Card>
    </main>
  )
}
