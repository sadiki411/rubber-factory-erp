import { AppstoreOutlined, ArrowRightOutlined, ExportOutlined, HomeOutlined, SearchOutlined, ToolOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Col, Empty, Input, Row, Skeleton, Statistic, Typography } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { moldApi, toList } from '../api/client'
import { PageTitle } from '../components/PageTitle'
import { StatusTag } from '../components/StatusTag'
import type { MoldAsset, MoldStatus } from '../types'
import { moldCode, moldLocation, moldModelOf } from '../types'

function StatusSummary({ molds }: { molds: MoldAsset[] }) {
  const count = (status: MoldStatus) => molds.filter((mold) => mold.status === status).length
  const items = [
    { status: 'IN_STOCK' as const, label: '在库模具', value: count('IN_STOCK'), icon: <HomeOutlined />, className: 'green' },
    { status: 'ON_MACHINE' as const, label: '上机中', value: count('ON_MACHINE'), icon: <ToolOutlined />, className: 'blue' },
    { status: 'OUTSOURCED' as const, label: '客户收回', value: count('OUTSOURCED'), icon: <ExportOutlined />, className: 'amber' },
  ]
  return (
    <Row gutter={[16, 16]}>
      {items.map((item) => (
        <Col xs={24} sm={8} key={item.status}>
          <Card className={`summary-card ${item.className}`}>
            <Statistic title={item.label} value={item.value} prefix={item.icon} suffix="副" />
          </Card>
        </Col>
      ))}
    </Row>
  )
}

function ResultCard({ mold }: { mold: MoldAsset }) {
  const navigate = useNavigate()
  const model = moldModelOf(mold)
  return (
    <Card className="search-result-card" hoverable onClick={() => navigate(`/molds/${mold.id}`)}>
      <div className="result-card-top">
        <div>
          <Typography.Title level={4}>{moldCode(mold)}</Typography.Title>
          <Typography.Text>{model?.code || '-'} · {model?.product_name || model?.name || '未填写产品名称'}</Typography.Text>
        </div>
        <StatusTag status={mold.status} />
      </div>
      <div className="location-line">
        <span className="location-label">当前位置 / 状态</span>
        <strong>{moldLocation(mold)}</strong>
      </div>
      <Button type="link" className="card-link">查看详情 <ArrowRightOutlined /></Button>
    </Card>
  )
}

export function DashboardPage() {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [submitted, setSubmitted] = useState('')
  const summaryQuery = useQuery({
    queryKey: ['molds', 'summary'],
    queryFn: async () => toList(await moldApi.list({ page_size: 1000 })),
  })
  const searchQuery = useQuery({
    queryKey: ['molds', 'search', submitted],
    queryFn: async () => toList(await moldApi.list({ q: submitted, page_size: 50 })),
    enabled: !!submitted,
  })

  return (
    <div className="page-container dashboard-page">
      <PageTitle title="模具工作台" description="输入模具编号、型号或产品名称，立即查看当前所在位置。" />
      <Card className="hero-search-card" variant="borderless">
        <div className="search-copy">
          <span className="eyebrow">快速定位</span>
          <Typography.Title level={2}>今天要找哪副模具？</Typography.Title>
          <Typography.Paragraph>在库模具显示精确库位，上机模具显示具体机台，客户收回的模具显示当前状态。</Typography.Paragraph>
        </div>
        <Input.Search
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onSearch={(value) => setSubmitted(value.trim())}
          enterButton={<><SearchOutlined /> 查找模具</>}
          size="large"
          allowClear
          placeholder="例如：MJ-001、ABC-100、密封圈"
          aria-label="搜索模具"
        />
        <div className="quick-links">
          <Button icon={<AppstoreOutlined />} onClick={() => navigate('/racks')}>查看货架正面图</Button>
          <Button icon={<ToolOutlined />} onClick={() => navigate('/molds')}>打开完整台账</Button>
        </div>
      </Card>

      {submitted && (
        <section className="dashboard-section">
          <div className="section-heading">
            <Typography.Title level={3}>“{submitted}”的查找结果</Typography.Title>
            {!searchQuery.isLoading && <Typography.Text type="secondary">共 {searchQuery.data?.length || 0} 条</Typography.Text>}
          </div>
          {searchQuery.isLoading ? <Skeleton active /> : searchQuery.isError ? (
            <Alert type="error" showIcon title="查询失败" description={(searchQuery.error as Error).message} />
          ) : searchQuery.data?.length ? (
            <div className="result-grid">{searchQuery.data.map((mold) => <ResultCard key={mold.id} mold={mold} />)}</div>
          ) : <Empty description="没有找到匹配的模具，请尝试更短的关键词" />}
        </section>
      )}

      <section className="dashboard-section">
        <div className="section-heading">
          <Typography.Title level={3}>当前状态概览</Typography.Title>
          <Button type="link" onClick={() => navigate('/molds')}>查看全部</Button>
        </div>
        {summaryQuery.isLoading ? <Skeleton active /> : summaryQuery.isError ? (
          <Alert type="warning" showIcon title="暂时无法读取状态概览" />
        ) : <StatusSummary molds={summaryQuery.data || []} />}
      </section>
    </div>
  )
}
