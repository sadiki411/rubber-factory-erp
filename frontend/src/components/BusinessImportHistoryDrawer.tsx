import { ArrowLeftOutlined, DownloadOutlined, FileSearchOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Descriptions, Drawer, Empty, Grid, List, Pagination, Space, Table, Tag, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { businessImportApi } from '../api/client'
import type { BusinessImportHistoryRow, BusinessImportHistorySummary, ImportIssue } from '../types'

interface Props {
  open: boolean
  onClose: () => void
}

const ACTION_META: Record<string, { text: string; color: string }> = {
  CREATE: { text: '新增', color: 'success' },
  UPDATE: { text: '更新', color: 'processing' },
  SKIP: { text: '跳过', color: 'warning' },
}

const STATUS_COLORS: Record<string, string> = {
  PREVIEWED: 'processing',
  COMMITTING: 'processing',
  COMMITTED: 'success',
  FAILED: 'error',
}

function statusColor(batch: BusinessImportHistorySummary) {
  if (batch.status === 'PREVIEWED' && batch.error_count > 0) return 'error'
  return STATUS_COLORS[batch.status] || 'default'
}

function timestamp(value?: string | null) {
  if (!value) return '-'
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value)).replaceAll('/', '-')
}

function actionSummary(batch: BusinessImportHistorySummary) {
  return `新增 ${batch.actions.CREATE || 0} · 更新 ${batch.actions.UPDATE || 0} · 跳过 ${batch.actions.SKIP || 0}`
}

export function BusinessImportHistoryDrawer({ open, onClose }: Props) {
  const screens = Grid.useBreakpoint()
  const mobile = screens.md === false
  const [selectedToken, setSelectedToken] = useState<string>()
  const [page, setPage] = useState(1)

  const historyQuery = useQuery({
    queryKey: ['business-import-history', { page }],
    queryFn: () => businessImportApi.history({ page, page_size: 20 }),
    enabled: open,
  })
  const detailQuery = useQuery({
    queryKey: ['business-import-history', selectedToken],
    queryFn: () => businessImportApi.historyDetail(selectedToken!),
    enabled: open && Boolean(selectedToken),
  })

  const close = () => {
    setSelectedToken(undefined)
    setPage(1)
    onClose()
  }

  const historyRows = Array.isArray(historyQuery.data)
    ? historyQuery.data
    : historyQuery.data?.results || []
  const historyTotal = Array.isArray(historyQuery.data)
    ? historyQuery.data.length
    : historyQuery.data?.count || 0

  const batchColumns: TableColumnsType<BusinessImportHistorySummary> = [
    { title: '文件', dataIndex: 'original_name', width: 250, ellipsis: true },
    { title: '识别类型', dataIndex: 'source_type_display', width: 140 },
    { title: '状态', key: 'status', width: 120, render: (_, row) => <Tag color={statusColor(row)}>{row.status_display}</Tag> },
    { title: '结果', key: 'actions', width: 210, render: (_, row) => actionSummary(row) },
    { title: '问题', key: 'issues', width: 125, render: (_, row) => <span><Typography.Text type={row.error_count ? 'danger' : undefined}>错误 {row.error_count}</Typography.Text> / 警告 {row.warning_count}</span> },
    { title: '上传时间', dataIndex: 'created_at', width: 165, render: timestamp },
    { title: '操作', key: 'action', fixed: 'right', width: 90, render: (_, row) => <Button type="link" icon={<FileSearchOutlined />} onClick={() => setSelectedToken(row.token)}>查看</Button> },
  ]
  const rowColumns: TableColumnsType<BusinessImportHistoryRow> = [
    { title: '工作表 / 行', key: 'source', width: 165, render: (_, row) => `${row.sheet || '-'}${row.row === null || row.row === undefined ? '' : ` / ${row.row}`}` },
    { title: '动作', dataIndex: 'action', width: 90, render: (value) => { const meta = ACTION_META[value] || { text: value || '-', color: 'default' }; return <Tag color={meta.color}>{meta.text}</Tag> } },
    { title: '订单号 / 项次', key: 'order', width: 210, render: (_, row) => `${row.order_no || '-'}${row.item_no ? ` / ${row.item_no}` : ''}` },
    { title: '识别摘要', dataIndex: 'summary', width: 280, ellipsis: true, render: (value) => value || '-' },
    { title: '失败或略过原因', key: 'reasons', width: 420, render: (_, row) => row.reasons?.length ? <ul className="import-history-reasons">{row.reasons.map((reason, index) => <li key={`${index}-${reason}`}>{reason}</li>)}</ul> : '-' },
  ]
  const issueColumns: TableColumnsType<ImportIssue> = [
    { title: '级别', dataIndex: 'level', width: 85, render: (value) => <Tag color={value === 'error' ? 'error' : 'warning'}>{value === 'error' ? '错误' : '警告'}</Tag> },
    { title: '阶段', dataIndex: 'stage', width: 90, render: (value) => value === 'commit' ? '提交' : value === 'preview' ? '预检' : '-' },
    { title: '工作表 / 行', key: 'source', width: 165, render: (_, row) => `${row.sheet || '-'}${row.row === null || row.row === undefined ? '' : ` / ${row.row}`}` },
    { title: '业务标识', dataIndex: 'field', width: 140, render: (value) => value || '-' },
    { title: '原因', dataIndex: 'message' },
  ]

  const detail = detailQuery.data
  return (
    <Drawer
      open={open}
      onClose={close}
      size={mobile ? 390 : 1000}
      title={selectedToken ? '导入记录详情' : '订单 / 发料导入记录'}
      extra={selectedToken ? <Button icon={<ArrowLeftOutlined />} onClick={() => setSelectedToken(undefined)}>返回记录</Button> : undefined}
    >
      {!selectedToken ? (
        <>
          <Alert type="info" showIcon title="每次预检、导入失败和逐行跳过原因都会长期保留。" description="可按文件回看上传时间、识别结果和错误原因；错误文件不会写入订单或发料数据。" />
          {mobile ? (
            <>
              <List
                className="import-history-mobile-list"
                loading={historyQuery.isLoading}
                dataSource={historyRows}
                locale={{ emptyText: <Empty description="暂无导入记录" /> }}
                renderItem={(batch) => (
                  <List.Item>
                    <Card className="mobile-record-card" onClick={() => setSelectedToken(batch.token)}>
                      <Space direction="vertical" size={6} style={{ width: '100%' }}>
                        <Space wrap><Tag color={statusColor(batch)}>{batch.status_display}</Tag><Typography.Text type="secondary">{batch.source_type_display}</Typography.Text></Space>
                        <Typography.Text strong ellipsis>{batch.original_name}</Typography.Text>
                        <Typography.Text>{actionSummary(batch)}</Typography.Text>
                        <Typography.Text type={batch.error_count ? 'danger' : 'secondary'}>错误 {batch.error_count} / 警告 {batch.warning_count} · {timestamp(batch.created_at)}</Typography.Text>
                        <Button block icon={<FileSearchOutlined />}>查看原因</Button>
                      </Space>
                    </Card>
                  </List.Item>
                )}
              />
              {historyTotal > 20 && <Pagination simple current={page} pageSize={20} total={historyTotal} onChange={setPage} />}
            </>
          ) : (
            <Table
              className="import-history-table"
              rowKey="token"
              loading={historyQuery.isLoading}
              dataSource={historyRows}
              columns={batchColumns}
              pagination={{ current: page, pageSize: 20, total: historyTotal, showSizeChanger: false, showTotal: (total) => `共 ${total} 次`, onChange: setPage }}
              scroll={{ x: 1100 }}
              locale={{ emptyText: <Empty description="暂无导入记录" /> }}
            />
          )}
          {historyQuery.isError && <Alert type="error" showIcon title="导入记录读取失败" description={(historyQuery.error as Error).message} />}
        </>
      ) : detailQuery.isError ? (
        <Alert type="error" showIcon title="导入详情读取失败" description={(detailQuery.error as Error).message} />
      ) : detail ? (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Descriptions bordered size="small" column={{ xs: 1, sm: 2, md: 3 }}>
            <Descriptions.Item label="文件名">{detail.original_name}</Descriptions.Item>
            <Descriptions.Item label="状态"><Tag color={statusColor(detail)}>{detail.status_display}</Tag></Descriptions.Item>
            <Descriptions.Item label="识别类型">{detail.source_type_display}</Descriptions.Item>
            <Descriptions.Item label="上传时间">{timestamp(detail.created_at)}</Descriptions.Item>
            <Descriptions.Item label="完成时间">{timestamp(detail.committed_at)}</Descriptions.Item>
            <Descriptions.Item label="结果">{actionSummary(detail)}</Descriptions.Item>
          </Descriptions>
          <Space wrap>
            <Tag color="error">错误 {detail.error_count}</Tag>
            <Tag color="warning">警告 {detail.warning_count}</Tag>
            {(detail.error_count > 0 || detail.warning_count > 0) && <Button icon={<DownloadOutlined />} href={businessImportApi.errorReportUrl(detail.token)}>下载问题报告</Button>}
          </Space>
          <Typography.Title level={5}>逐行处理结果</Typography.Title>
          {mobile ? (
            <List
              dataSource={detail.rows}
              locale={{ emptyText: <Empty description="此文件未识别出业务行" /> }}
              renderItem={(row) => (
                <List.Item>
                  <Card size="small" style={{ width: '100%' }}>
                    <Space direction="vertical" size={4} style={{ width: '100%' }}>
                      <Space wrap><Tag>{row.sheet || '-'}/{row.row ?? '-'}</Tag>{row.action && <Tag color={ACTION_META[row.action]?.color}>{ACTION_META[row.action]?.text || row.action}</Tag>}</Space>
                      <Typography.Text strong>{row.summary || `${row.order_no || '-'} / ${row.item_no || '-'}`}</Typography.Text>
                      {row.reasons?.map((reason, index) => <Typography.Text key={`${index}-${reason}`} type="danger">{reason}</Typography.Text>)}
                    </Space>
                  </Card>
                </List.Item>
              )}
            />
          ) : <Table rowKey="row_key" size="small" dataSource={detail.rows} columns={rowColumns} pagination={{ pageSize: 10 }} scroll={{ x: 1165 }} locale={{ emptyText: <Empty description="此文件未识别出业务行" /> }} />}
          <Typography.Title level={5}>错误与警告</Typography.Title>
          <Table rowKey={(issue) => `${issue.stage}-${issue.sheet}-${issue.row}-${issue.field}-${issue.message}`} size="small" dataSource={detail.issues} columns={issueColumns} pagination={{ pageSize: 8 }} scroll={{ x: 900 }} locale={{ emptyText: <Empty description="无错误或警告" /> }} />
        </Space>
      ) : <Empty description="正在读取导入详情" />}
    </Drawer>
  )
}
