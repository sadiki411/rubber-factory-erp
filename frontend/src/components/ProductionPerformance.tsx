import { CalendarOutlined, ClockCircleOutlined, TeamOutlined } from '@ant-design/icons'
import { Alert, Card, Col, DatePicker, Empty, Row, Skeleton, Statistic, Table, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import { useQuery } from '@tanstack/react-query'
import dayjs, { type Dayjs } from 'dayjs'
import { useState } from 'react'
import { productionApi } from '../api/client'
import { productionOperatorDayCount } from '../production'
import type { ProductionMonthlyPerformanceOperator } from '../types'

interface Props {
  mobile: boolean
}

function numberText(value: number | string | null | undefined, digits = 2) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed.toLocaleString('zh-CN', { maximumFractionDigits: digits }) : '-'
}

export function ProductionPerformance({ mobile }: Props) {
  const [month, setMonth] = useState<Dayjs>(dayjs().startOf('month'))
  const monthText = month.format('YYYY-MM')
  const query = useQuery({
    queryKey: ['production', 'performance', monthText],
    queryFn: () => productionApi.monthlyPerformance(monthText),
  })
  const rows = query.data?.operators || []
  const totals = query.data?.totals
  const columns: TableColumnsType<ProductionMonthlyPerformanceOperator> = [
    { title: '作业员', dataIndex: 'operator', fixed: 'left', width: 130, render: (value) => <strong>{value || '-'}</strong> },
    { title: '总模数', dataIndex: 'total_mold_count', width: 110, render: (value) => numberText(value, 0) },
    { title: '生产天数', dataIndex: 'production_days', width: 105, render: (value) => `${numberText(value, 0)} 天` },
    { title: '参与订单数', dataIndex: 'participated_run_count', width: 115, render: (value) => numberText(value, 0) },
    { title: '日均模数', dataIndex: 'average_daily_mold_count', width: 110, render: (value) => numberText(value) },
    { title: '折算生产小时', dataIndex: 'production_hours', width: 130, render: (value) => `${numberText(value)} h` },
  ]

  return (
    <section className="production-performance-section">
      <div className="section-heading production-performance-heading">
        <div>
          <Typography.Title level={3}>月度绩效</Typography.Title>
          <Typography.Text type="secondary">按作业员汇总每日模数、生产天数与折算工时，仅提供绩效依据，不计算奖金金额。</Typography.Text>
        </div>
        <DatePicker picker="month" allowClear={false} value={month} format="YYYY年M月" onChange={(value) => value && setMonth(value)} />
      </div>

      {query.isError && <Alert type="error" showIcon title="月度绩效读取失败" description={(query.error as Error).message} />}
      <Row gutter={[12, 12]} className="production-performance-totals">
        <Col xs={12} md={6}><Card><Statistic title="作业员人数" value={totals?.operator_count || 0} suffix="人" prefix={<TeamOutlined />} /></Card></Col>
        <Col xs={12} md={6}><Card><Statistic title="总生产模数" value={totals?.total_mold_count || 0} suffix="模" prefix={<CalendarOutlined />} /></Card></Col>
        <Col xs={12} md={6}><Card><Statistic title="生产人日" value={productionOperatorDayCount(totals)} suffix="人日" /></Card></Col>
        <Col xs={12} md={6}><Card><Statistic title="折算生产小时" value={Number(totals?.production_hours || 0)} precision={2} suffix="h" prefix={<ClockCircleOutlined />} /></Card></Col>
      </Row>

      {query.isLoading ? <Card><Skeleton active /></Card> : mobile ? (
        <div className="production-performance-mobile-list">
          {rows.length ? rows.map((row) => (
            <Card key={row.operator} className="production-performance-mobile-card">
              <div className="record-card-heading"><Typography.Title level={4}>{row.operator}</Typography.Title><strong>{numberText(row.total_mold_count, 0)} 模</strong></div>
              <div className="production-performance-mobile-grid">
                <span>生产天数<b>{numberText(row.production_days, 0)} 天</b></span>
                <span>参与订单<b>{numberText(row.participated_run_count, 0)} 个</b></span>
                <span>日均模数<b>{numberText(row.average_daily_mold_count)}</b></span>
                <span>折算工时<b>{numberText(row.production_hours)} h</b></span>
              </div>
            </Card>
          )) : <Empty description={`${month.format('YYYY年M月')}暂无绩效记录`} />}
        </div>
      ) : (
        <Card className="data-card" styles={{ body: { padding: 0 } }}>
          <Table rowKey="operator" dataSource={rows} columns={columns} pagination={false} scroll={{ x: 700 }} locale={{ emptyText: `${month.format('YYYY年M月')}暂无绩效记录` }} />
        </Card>
      )}
    </section>
  )
}
