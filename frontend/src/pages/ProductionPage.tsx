import { AlertOutlined, ClockCircleOutlined, DownloadOutlined, FileExcelOutlined, MoreOutlined, PlusOutlined, SearchOutlined, ToolOutlined } from '@ant-design/icons'
import { Alert, App, Button, Card, Col, DatePicker, Dropdown, Empty, Grid, Input, Pagination, Progress, Row, Select, Skeleton, Space, Statistic, Table, Tag, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'
import { useEffect, useMemo, useRef, useState } from 'react'
import { productionApi, productionImportApi, toList } from '../api/client'
import { PageTitle } from '../components/PageTitle'
import { ProductionBoard } from '../components/ProductionBoard'
import { ProductionImportDrawer } from '../components/ProductionImportDrawer'
import { ProductionLogDrawer } from '../components/ProductionLogDrawer'
import { ProductionPerformance } from '../components/ProductionPerformance'
import { ProductionRunDrawer } from '../components/ProductionRunDrawer'
import { formatProductionDate, isKeyboardActivationKey, productionReminderKey, productionStationGroupLabel, productionStationNumber } from '../production'
import type { ApiList, ProductionBoardStation, ProductionMold, ProductionRun, ProductionRunStatus, ProductionStation } from '../types'

const STATUS_META: Record<ProductionRunStatus, { text: string; color: string }> = {
  PLANNED: { text: '待上机', color: 'blue' },
  RUNNING: { text: '生产中', color: 'processing' },
  COMPLETED: { text: '已完成', color: 'success' },
  CANCELLED: { text: '已取消', color: 'default' },
}

function numberText(value: number | string | null | undefined, digits = 2) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed.toLocaleString('zh-CN', { maximumFractionDigits: digits }) : '0'
}

function statusTag(status: ProductionRunStatus) {
  const meta = STATUS_META[status]
  return <Tag color={meta.color}>{meta.text}</Tag>
}

function settlementProfit(record: ProductionRun) {
  if (record.is_settled) {
    return <strong className={Number(record.profit) < 0 ? 'negative-value' : 'profit-value'}>¥{numberText(record.profit)}</strong>
  }
  if (record.status === 'PLANNED' || (record.status === 'CANCELLED' && !record.loaded_at)) return <Typography.Text type="secondary">-</Typography.Text>
  return <Tag color="warning">待结算</Tag>
}

export function ProductionPage() {
  const { message, notification } = App.useApp()
  const screens = Grid.useBreakpoint()
  const mobile = screens.md === false
  const [date, setDate] = useState(dayjs())
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<ProductionRunStatus | ''>('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [formTarget, setFormTarget] = useState<{ run?: ProductionRun; station?: ProductionStation; mold?: ProductionMold; initialStatus?: ProductionRunStatus }>()
  const [selectedRun, setSelectedRun] = useState<ProductionRun>()
  const [importOpen, setImportOpen] = useState(false)
  const notified = useRef(new Set<string>())
  const dateText = date.format('YYYY-MM-DD')

  const boardQuery = useQuery({
    queryKey: ['production', 'board'],
    queryFn: () => productionApi.board(60),
    refetchInterval: 30_000,
    refetchIntervalInBackground: true,
  })
  const summaryQuery = useQuery({
    queryKey: ['production', 'summary', dateText],
    queryFn: () => productionApi.summary({ date_from: dateText, date_to: dateText }),
  })
  const runsQuery = useQuery({
    queryKey: ['production', 'runs', { dateText, query, status, page, pageSize }],
    queryFn: () => productionApi.listRuns({
      q: query || undefined,
      status: status || undefined,
      date_from: dateText,
      date_to: dateText,
      page,
      page_size: pageSize,
    }),
  })

  useEffect(() => {
    const stations = boardQuery.data?.groups.flatMap((group) => group.stations) || []
    stations.filter((station) => station.run && ['DUE_SOON', 'OVERDUE'].includes(station.reminder_status)).forEach((station) => {
      const key = productionReminderKey(station.run!, station.reminder_status)
      if (notified.current.has(key)) return
      notified.current.add(key)
      notification[station.reminder_status === 'OVERDUE' ? 'error' : 'warning']({
        title: station.reminder_status === 'OVERDUE' ? '换模时间已超时' : '即将到换模时间',
        description: `${productionStationGroupLabel(station.group)} ${productionStationNumber(station)}号机台 · ${station.run!.order_no}`,
        placement: 'topRight',
      })
    })
  }, [boardQuery.data, notification])

  const payload = runsQuery.data
  const rows = payload ? toList(payload) : []
  const total = Array.isArray(payload) ? payload.length : (payload as ApiList<ProductionRun> | undefined)?.count || 0
  const reminderStations = useMemo(() => (
    boardQuery.data?.groups.flatMap((group) => group.stations).filter((station) => ['DUE_SOON', 'OVERDUE'].includes(station.reminder_status)) || []
  ), [boardQuery.data])

  const showRun = async (run: Pick<ProductionRun, 'id'>) => {
    try {
      setSelectedRun(await productionApi.detailRun(run.id))
    } catch (error) {
      message.error((error as Error).message)
    }
  }

  const openStation = (station: ProductionBoardStation) => {
    if (station.run) void showRun(station.run)
    else setFormTarget({
      station,
      mold: station.mounted_molds?.length === 1 ? station.mounted_molds[0] : undefined,
    })
  }

  const columns: TableColumnsType<ProductionRun> = [
    { title: '机台', key: 'station', fixed: 'left', width: 105, render: (_, record) => <strong>{productionStationGroupLabel(record.station.group)}-{productionStationNumber(record.station)}号</strong> },
    { title: '订单编号', dataIndex: 'order_no', fixed: 'left', width: 185, render: (value, record) => <Button type="link" className="table-primary-link" onClick={() => void showRun(record)}>{value}</Button> },
    { title: '规格 / 材质', key: 'product', width: 210, render: (_, record) => <span>{record.specification}<br /><Typography.Text type="secondary">{record.material}</Typography.Text>{record.mold && <><br /><Typography.Text type="secondary">模具 {record.mold.model_code} · {record.mold.asset_code}</Typography.Text></>}</span> },
    { title: '状态', dataIndex: 'status', width: 95, render: statusTag },
    { title: '上模时间', dataIndex: 'loaded_at', width: 145, render: (value) => formatProductionDate(value, 'MM-DD HH:mm') },
    { title: '预计换模', dataIndex: 'expected_change_at', width: 145, render: (value) => formatProductionDate(value, 'MM-DD HH:mm') },
    { title: '订单累计模数', key: 'molds', width: 135, render: (_, record) => `${numberText(record.produced_mold_count, 0)} / ${numberText(record.planned_mold_count, 0)}` },
    { title: '订单累计进度', dataIndex: 'progress_percent', width: 145, render: (value) => <Progress percent={Math.round(Number(value || 0))} size="small" /> },
    { title: '订单累计工时', dataIndex: 'actual_hours', width: 115, render: (value) => `${numberText(value)}h` },
    { title: '结算利润', key: 'settlement_profit', width: 125, render: (_, record) => settlementProfit(record) },
    {
      title: '操作', key: 'actions', fixed: 'right', width: 70,
      render: (_, record) => (
        <Dropdown trigger={['click']} menu={{ items: [
          { key: 'detail', label: '日报 / 完工结算' },
          { key: 'edit', label: '编辑生产资料' },
        ], onClick: ({ key }) => key === 'edit' ? setFormTarget({ run: record }) : void showRun(record) }}>
          <Button type="text" icon={<MoreOutlined />} aria-label="生产记录操作" />
        </Dropdown>
      ),
    },
  ]

  const boardCounts = boardQuery.data?.counts
  const summary = summaryQuery.data
  return (
    <div className="page-container production-page">
      <PageTitle
        title="前端生产管理"
        description="三组联体机台，每组2台，共6台（孔=台）；模具台账登记上机后会同步显示型号，生产订单结束后仍需人工归位。"
        extra={<Space wrap><Button icon={<FileExcelOutlined />} onClick={() => setImportOpen(true)}>导入统计表</Button><Button icon={<PlusOutlined />} onClick={() => setFormTarget({ initialStatus: 'PLANNED' })}>新增待上机计划</Button><Button type="primary" icon={<PlusOutlined />} onClick={() => setFormTarget({ initialStatus: 'RUNNING' })}>登记已上机生产</Button></Space>}
      />

      {boardQuery.isError && <Alert type="error" showIcon title="实时机台看板读取失败" description={(boardQuery.error as Error).message} />}
      {reminderStations.length > 0 && (
        <Alert
          className="changeover-alert"
          type={reminderStations.some((item) => item.reminder_status === 'OVERDUE') ? 'error' : 'warning'}
          showIcon
          icon={<AlertOutlined />}
          title={`当前有 ${reminderStations.length} 台机台需要关注换模`}
          description={<Space wrap>{reminderStations.map((station) => <Button size="small" key={station.id} onClick={() => openStation(station)}>{productionStationGroupLabel(station.group)}-{productionStationNumber(station)}号机台 · {station.run?.order_no}</Button>)}</Space>}
        />
      )}

      <Row gutter={[14, 14]} className="production-kpis">
        <Col xs={12} md={6}><Card className="production-kpi running"><Statistic title="机台占用" value={boardCounts?.occupied || 0} suffix="台" prefix={<ToolOutlined />} /></Card></Col>
        <Col xs={12} md={6}><Card className="production-kpi warning"><Statistic title="1小时内换模" value={boardCounts?.due_soon || 0} suffix="台" prefix={<ClockCircleOutlined />} /></Card></Col>
        <Col xs={12} md={6}><Card className="production-kpi overdue"><Statistic title="已超时" value={boardCounts?.overdue || 0} suffix="台" prefix={<AlertOutlined />} /></Card></Col>
        <Col xs={12} md={6}><Card className="production-kpi profit"><Statistic title={`${date.format('M月D日')}生产模数`} value={summary?.produced_mold_count || 0} suffix="模" /></Card></Col>
      </Row>

      <ProductionBoard board={boardQuery.data} loading={boardQuery.isLoading} onStationClick={openStation} />

      <section className="production-record-section">
        <div className="section-heading">
          <div><Typography.Title level={3}>每日生产台账</Typography.Title><Typography.Text type="secondary">按日期在线查看和编辑订单、人员日报；模数、进度和工时为订单累计，利润仅在完工结算后显示。</Typography.Text></div>
          <Button icon={<DownloadOutlined />} href={productionImportApi.templateUrl}>下载填写模板</Button>
        </div>
        <Card className="filter-card production-filter-card">
          <div className="production-filter-row">
            <DatePicker value={date} allowClear={false} onChange={(value) => { if (value) { setDate(value); setPage(1) } }} />
            <Input allowClear prefix={<SearchOutlined />} placeholder="搜索订单、规格、材质或模具" value={query} onChange={(event) => { setQuery(event.target.value); setPage(1) }} />
            <Select value={status} onChange={(value) => { setStatus(value); setPage(1) }} options={[
              { value: '', label: '全部状态' },
              { value: 'PLANNED', label: '待上机' },
              { value: 'RUNNING', label: '生产中' },
              { value: 'COMPLETED', label: '已完成' },
              { value: 'CANCELLED', label: '已取消' },
            ]} />
          </div>
        </Card>

        {mobile ? (
          <div className="production-mobile-list">
            {runsQuery.isLoading ? <Card><Skeleton active /></Card> : rows.length ? rows.map((record) => (
              <Card
                key={record.id}
                className="mobile-record-card production-mobile-card"
                role="button"
                tabIndex={0}
                aria-label={`查看生产订单 ${record.order_no}`}
                onClick={() => void showRun(record)}
                onKeyDown={(event) => {
                  if (!isKeyboardActivationKey(event.key)) return
                  event.preventDefault()
                  void showRun(record)
                }}
              >
                <div className="record-card-heading"><Typography.Title level={4}>{record.order_no}</Typography.Title>{statusTag(record.status)}</div>
                <Typography.Text>{productionStationGroupLabel(record.station.group)}-{productionStationNumber(record.station)}号机台 · {record.specification} · {record.material}</Typography.Text>
                {record.mold && <Typography.Text type="secondary">模具 {record.mold.model_code} · {record.mold.asset_code}</Typography.Text>}
                <div className="mobile-production-times"><span>上模 {formatProductionDate(record.loaded_at, 'MM-DD HH:mm')}</span><span>换模 {formatProductionDate(record.expected_change_at, 'MM-DD HH:mm')}</span></div>
                <Progress percent={Math.round(Number(record.progress_percent || 0))} size="small" />
                <div className="mobile-production-footer"><span>累计模数 {numberText(record.produced_mold_count, 0)}/{numberText(record.planned_mold_count, 0)}</span><span>{settlementProfit(record)}</span></div>
              </Card>
            )) : <Empty description="当日暂无生产记录" />}
            {total > pageSize && <Pagination current={page} pageSize={pageSize} total={total} onChange={setPage} showSizeChanger={false} />}
          </div>
        ) : (
          <Card className="data-card" styles={{ body: { padding: 0 } }}>
            <Table
              rowKey="id"
              loading={runsQuery.isLoading}
              dataSource={rows}
              columns={columns}
              scroll={{ x: 1420 }}
              pagination={{ current: page, pageSize, total, showSizeChanger: true, showTotal: (value) => `共 ${value} 条`, onChange: (next, size) => { setPage(next); setPageSize(size) } }}
            />
          </Card>
        )}
      </section>

      <ProductionPerformance mobile={mobile} />

      <ProductionRunDrawer open={!!formTarget} run={formTarget?.run} station={formTarget?.station} mountedMold={formTarget?.mold} initialStatus={formTarget?.initialStatus} onClose={() => setFormTarget(undefined)} onSuccess={(result) => setSelectedRun(result)} />
      <ProductionLogDrawer
        open={!!selectedRun}
        run={selectedRun}
        onClose={() => setSelectedRun(undefined)}
        onRunChange={setSelectedRun}
        onEdit={(run) => { setSelectedRun(undefined); setFormTarget({ run }) }}
      />
      <ProductionImportDrawer open={importOpen} onClose={() => setImportOpen(false)} />
    </div>
  )
}
