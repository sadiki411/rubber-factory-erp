import { CheckCircleOutlined, DownloadOutlined, FileExcelOutlined, InboxOutlined, UploadOutlined, WarningOutlined } from '@ant-design/icons'
import { Alert, App, Button, Card, Col, Descriptions, Empty, Input, Progress, Row, Space, Table, Tag, Typography, Upload } from 'antd'
import type { TableColumnsType, UploadFile } from 'antd'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { importApi } from '../api/client'
import { PageTitle } from '../components/PageTitle'
import type { ImportIssue, ImportPreview, ImportPreviewRow } from '../types'
import { STATUS_META } from '../types'

export function ImportPage() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [files, setFiles] = useState<UploadFile[]>([])
  const [preview, setPreview] = useState<ImportPreview>()
  const previewMutation = useMutation({
    mutationFn: (file: File) => importApi.preview(file),
    onSuccess: (result) => { setPreview(result); message.success('文件预检完成') },
    onError: (error: Error) => message.error(error.message),
  })
  const commitMutation = useMutation({
    mutationFn: ({ token, rows }: { token: string; rows: ImportPreviewRow[] }) => importApi.commit(
      token,
      rows.map(({ row_key, asset_code }) => ({ row_key, asset_code })),
    ),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries()
      message.success(`成功导入 ${result.imported_count} 副模具`)
      setPreview(undefined)
      setFiles([])
    },
    onError: (error: Error) => message.error(error.message),
  })

  const startPreview = () => {
    const file = files[0]?.originFileObj
    if (!file) return message.warning('请先选择 Excel 文件')
    previewMutation.mutate(file)
  }

  const issueColumns: TableColumnsType<ImportIssue> = [
    { title: '级别', dataIndex: 'level', width: 90, render: (value) => <Tag color={value === 'error' ? 'error' : 'warning'}>{value === 'error' ? '错误' : '警告'}</Tag> },
    { title: '工作表', dataIndex: 'sheet', width: 130, render: (value) => value || '-' },
    { title: '行号', dataIndex: 'row', width: 80, render: (value) => value || '-' },
    { title: '说明', dataIndex: 'message' },
  ]
  const rowColumns: TableColumnsType<ImportPreviewRow> = [
    { title: '行号', dataIndex: 'row_no', width: 75 },
    {
      title: '建议模具编号',
      dataIndex: 'asset_code',
      width: 190,
      render: (value, row) => preview?.source_type === 'legacy' ? (
        <Input
          value={value}
          maxLength={100}
          aria-label={`修改 ${row.model_code} 的建议模具编号`}
          onChange={(event) => setPreview((current) => current ? {
            ...current,
            rows: current.rows.map((item) => item.row_key === row.row_key ? { ...item, asset_code: event.target.value } : item),
          } : current)}
        />
      ) : value,
    },
    { title: '型号', dataIndex: 'model_code', width: 150 },
    { title: '产品名称', dataIndex: 'product_name', width: 160 },
    { title: '状态', dataIndex: 'status', width: 110, render: (value) => STATUS_META[value as keyof typeof STATUS_META]?.text || value },
    { title: '位置 / 去向', dataIndex: 'location', width: 180 },
    { title: '校验', dataIndex: 'valid', width: 90, render: (value) => value ? <Tag color="success">通过</Tag> : <Tag color="error">失败</Tag> },
  ]

  return (
    <div className="page-container">
      <PageTitle title="模具 Excel 导入" description="先预检再整批导入模具资料；存在错误时不会写入任何数据。" extra={<Button icon={<DownloadOutlined />} href={importApi.templateUrl}>下载标准模板</Button>} />

      <Row gutter={[20, 20]}>
        <Col xs={24} lg={15}>
          <Card title="1. 选择文件" className="import-card">
            <Upload.Dragger
              accept=".xlsx"
              maxCount={1}
              fileList={files}
              beforeUpload={() => false}
              onChange={({ fileList }) => { setFiles(fileList); setPreview(undefined) }}
            >
              <p className="ant-upload-drag-icon"><InboxOutlined /></p>
              <p className="ant-upload-text">点击或拖动 Excel 文件到这里</p>
              <p className="ant-upload-hint">支持系统标准模板，以及原《模具架位置管理表_实际布局版.xlsx》</p>
            </Upload.Dragger>
            <Button type="primary" icon={<UploadOutlined />} block className="preview-button" loading={previewMutation.isPending} onClick={startPreview}>上传并预检</Button>
          </Card>
        </Col>
        <Col xs={24} lg={9}>
          <Card title="导入规则" className="import-card">
            <Space orientation="vertical" size="middle">
              <span><CheckCircleOutlined className="success-icon" /> 标准模板支持在库、上机、客户收回三种状态</span>
              <span><CheckCircleOutlined className="success-icon" /> 旧台账仅读取当前启用格位，统一作为在库导入</span>
              <span><WarningOutlined className="warning-icon" /> 重复编号、库位冲突等错误必须全部处理</span>
              <span><FileExcelOutlined /> 导入采用事务处理，不会出现只成功一部分</span>
            </Space>
          </Card>
        </Col>
      </Row>

      {preview && (
        <>
          <Card
            title="2. 预检结果"
            className="import-preview-card"
            extra={<Space><Tag color={preview.error_count ? 'error' : 'success'}>{preview.error_count ? '需要修正' : '可以导入'}</Tag>{preview.issues?.length > 0 && <Button size="small" href={importApi.errorReportUrl(preview.token)}>下载错误报告</Button>}</Space>}
          >
            <Descriptions column={{ xs: 2, sm: 4 }}>
              <Descriptions.Item label="来源">{preview.source_type === 'legacy' ? '旧版货架台账' : '标准模板'}</Descriptions.Item>
              <Descriptions.Item label="总行数">{preview.total_rows}</Descriptions.Item>
              <Descriptions.Item label="可导入">{preview.valid_rows}</Descriptions.Item>
              <Descriptions.Item label="错误 / 警告"><span className="error-text">{preview.error_count}</span> / <span className="warning-text">{preview.warning_count}</span></Descriptions.Item>
            </Descriptions>
            <Progress percent={preview.total_rows ? Math.round((preview.valid_rows / preview.total_rows) * 100) : 0} status={preview.error_count ? 'exception' : 'success'} />
            {preview.issues?.length ? (
              <Table rowKey={(item) => `${item.sheet}-${item.row}-${item.field}-${item.message}`} size="small" dataSource={preview.issues} columns={issueColumns} pagination={{ pageSize: 10 }} scroll={{ x: 600 }} />
            ) : <Alert type="success" showIcon title="预检通过，没有发现问题" />}
          </Card>

          <Card title="3. 数据预览" className="import-preview-card">
            {preview.source_type === 'legacy' && preview.rows?.length > 0 && <Alert type="info" showIcon title="系统已生成建议模具编号，可在下表中直接修改后再确认导入。" />}
            {preview.rows?.length ? <Table rowKey="row_key" size="small" dataSource={preview.rows} columns={rowColumns} scroll={{ x: 930 }} pagination={{ pageSize: 20 }} /> : <Empty description="没有可预览的数据" />}
            <div className="import-commit-bar">
              <div><Typography.Text strong>确认后将一次性写入 {preview.valid_rows} 条记录</Typography.Text><br /><Typography.Text type="secondary">有错误时按钮不可用，请修正原文件后重新预检。</Typography.Text></div>
              <Button type="primary" size="large" disabled={preview.error_count > 0 || preview.valid_rows === 0 || preview.rows.some((row) => !row.asset_code.trim())} loading={commitMutation.isPending} onClick={() => commitMutation.mutate({ token: preview.token, rows: preview.source_type === 'legacy' ? preview.rows : [] })}>确认导入</Button>
            </div>
          </Card>
        </>
      )}
    </div>
  )
}
