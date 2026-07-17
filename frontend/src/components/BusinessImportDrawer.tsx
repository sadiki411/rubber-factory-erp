import { CheckCircleOutlined, DownloadOutlined, InboxOutlined, UploadOutlined } from '@ant-design/icons'
import { Alert, App, Button, Descriptions, Drawer, Progress, Space, Table, Tag, Upload } from 'antd'
import type { TableColumnsType, UploadFile } from 'antd'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { businessImportApi } from '../api/client'
import type { BusinessImportCounts, BusinessImportPreview, BusinessImportPreviewRow, ImportIssue } from '../types'

interface Props {
  open: boolean
  context?: 'product-specifications' | 'orders'
  onClose: () => void
}

const RECORD_META: Record<string, { text: string; color: string }> = {
  PRODUCT_SPECIFICATION: { text: '产品规格', color: 'blue' },
  ORDER: { text: '订单', color: 'processing' },
  MATERIAL_RECEIPT: { text: '发料记录', color: 'orange' },
  INSPECTION_CRITERION: { text: '检验标准', color: 'purple' },
}

const SOURCE_META: Record<string, string> = {
  PRODUCT_SPECIFICATIONS: '产品规格记录表',
  INTERNAL_ORDERS: '内部订单表',
  FACTORY_WORK_CONTACT: '客户工作联络单',
  MATERIAL_ISSUE: '客户发料清单',
  MIXED: '混合业务工作簿',
}

function countOf(counts: Partial<BusinessImportCounts> | undefined, key: keyof BusinessImportCounts) {
  return Number(counts?.[key] || 0)
}

export function BusinessImportDrawer({ open, context = 'orders', onClose }: Props) {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [files, setFiles] = useState<UploadFile[]>([])
  const [preview, setPreview] = useState<BusinessImportPreview>()

  const previewMutation = useMutation({
    mutationFn: (file: File) => businessImportApi.preview(file),
    onSuccess: (result) => {
      setPreview({
        ...result,
        counts: result.counts || { product_specifications: 0, orders: 0, material_receipts: 0, inspection_criteria: 0 },
        rows: result.rows || [],
        issues: result.issues || [],
      })
      message.success('业务工作簿预检完成')
    },
    onError: (error: Error) => message.error(error.message),
  })
  const commitMutation = useMutation({
    mutationFn: (token: string) => businessImportApi.commit(token),
    onSuccess: async (result) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['product-specifications'] }),
        queryClient.invalidateQueries({ queryKey: ['orders'] }),
        queryClient.invalidateQueries({ queryKey: ['material-receipts'] }),
        queryClient.invalidateQueries({ queryKey: ['quality'] }),
        queryClient.invalidateQueries({ queryKey: ['production'] }),
        queryClient.invalidateQueries({ queryKey: ['analytics'] }),
      ])
      const imported = result.imported || result.counts
      const importedTotal = result.imported_count ?? (
        countOf(imported, 'product_specifications')
        + countOf(imported, 'orders')
        + countOf(imported, 'material_receipts')
        + countOf(imported, 'inspection_criteria')
      )
      message.success(`业务数据导入完成，共写入 ${importedTotal} 条记录`)
      setPreview(undefined)
      setFiles([])
      onClose()
    },
    onError: (error: Error) => message.error(error.message),
  })

  const busy = previewMutation.isPending || commitMutation.isPending
  const closeDrawer = () => {
    if (busy) return
    setPreview(undefined)
    setFiles([])
    onClose()
  }
  const startPreview = () => {
    const file = files[0]?.originFileObj
    if (!file) return message.warning('请先选择 Excel 文件')
    setPreview(undefined)
    previewMutation.mutate(file)
  }

  const issueColumns: TableColumnsType<ImportIssue> = [
    { title: '级别', dataIndex: 'level', width: 80, render: (value) => <Tag color={value === 'error' ? 'error' : 'warning'}>{value === 'error' ? '错误' : '警告'}</Tag> },
    { title: '工作表', dataIndex: 'sheet', width: 140, render: (value) => value || '-' },
    { title: '行号', dataIndex: 'row', width: 75, render: (value) => value ?? '-' },
    { title: '字段', dataIndex: 'field', width: 120, render: (value) => value || '-' },
    { title: '说明', dataIndex: 'message' },
  ]
  const rowColumns: TableColumnsType<BusinessImportPreviewRow> = [
    { title: '类型', dataIndex: 'record_type', fixed: 'left', width: 110, render: (value) => { const meta = RECORD_META[value] || { text: value || '未知', color: 'default' }; return <Tag color={meta.color}>{meta.text}</Tag> } },
    { title: '工作表 / 行', key: 'source', width: 160, render: (_, row) => `${row.sheet || '-'}${row.row === null || row.row === undefined ? '' : ` / ${row.row}`}` },
    { title: '动作', dataIndex: 'action', width: 90, render: (value) => ({ CREATE: '新增', UPDATE: '更新', SKIP: '跳过' })[value as string] || value || '-' },
    { title: '订单号', dataIndex: 'order_no', width: 180, render: (value) => value || '-' },
    { title: '项次', dataIndex: 'item_no', width: 85, render: (value) => value || '-' },
    { title: '规格', dataIndex: 'specification', width: 180, render: (value) => value || '-' },
    { title: '材质', dataIndex: 'material', width: 130, render: (value) => value || '-' },
    { title: '识别摘要', dataIndex: 'summary', width: 280, ellipsis: true, render: (value) => value || '-' },
    { title: '校验', dataIndex: 'valid', fixed: 'right', width: 80, render: (value) => <Tag color={value === false ? 'error' : 'success'}>{value === false ? '失败' : '通过'}</Tag> },
  ]

  const validRows = preview?.rows.filter((row) => row.valid !== false).length || 0
  const percent = preview?.total_rows ? Math.round((validRows / preview.total_rows) * 100) : 0

  return (
    <Drawer
      className="business-import-drawer"
      open={open}
      onClose={closeDrawer}
      closable={!busy}
      maskClosable={!busy}
      keyboard={!busy}
      size={960}
      title={context === 'product-specifications' ? '导入产品规格及业务数据' : '导入订单及客户业务数据'}
      extra={<Button icon={<DownloadOutlined />} href={businessImportApi.templateUrl(context === 'product-specifications' ? 'product_specifications' : 'orders')}>下载标准模板</Button>}
    >
      <Alert
        type="info"
        showIcon
        title="系统会自动识别产品规格表、内部订单表、大厂工作联络单和客户发料清单。"
        description="导入前会显示识别类型、拟新增或跳过的记录以及全部问题；存在错误时整批不会写入数据库。"
      />
      <div className="business-import-upload">
        <Upload.Dragger
          accept=".xlsx"
          maxCount={1}
          fileList={files}
          disabled={busy}
          beforeUpload={() => false}
          onChange={({ fileList }) => {
            if (busy) return
            setFiles(fileList)
            setPreview(undefined)
          }}
        >
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="ant-upload-text">点击或拖入客服 / 厂家发来的 Excel 文件</p>
          <p className="ant-upload-hint">系统只提取 ERP 所需字段，原始数据仍会保留用于核对。</p>
        </Upload.Dragger>
        <Button type="primary" block icon={<UploadOutlined />} disabled={!files.length || busy} loading={previewMutation.isPending} onClick={startPreview}>上传并自动识别</Button>
      </div>

      {preview && (
        <div className="business-import-preview">
          <Descriptions column={{ xs: 2, sm: 4 }} size="small">
            <Descriptions.Item label="识别来源">{SOURCE_META[preview.source_type || ''] || preview.source_type || '自动识别'}</Descriptions.Item>
            <Descriptions.Item label="总记录">{preview.total_rows}</Descriptions.Item>
            <Descriptions.Item label="产品规格">{countOf(preview.counts, 'product_specifications')}</Descriptions.Item>
            <Descriptions.Item label="订单">{countOf(preview.counts, 'orders')}</Descriptions.Item>
            <Descriptions.Item label="发料记录">{countOf(preview.counts, 'material_receipts')}</Descriptions.Item>
            <Descriptions.Item label="检验标准">{countOf(preview.counts, 'inspection_criteria')}</Descriptions.Item>
            <Descriptions.Item label="错误 / 警告"><span className="error-text">{preview.error_count}</span> / <span className="warning-text">{preview.warning_count}</span></Descriptions.Item>
          </Descriptions>
          <Progress percent={percent} status={preview.error_count ? 'exception' : 'success'} />
          {preview.issues.length ? (
            <Table rowKey={(row) => `${row.sheet}-${row.row}-${row.field}-${row.message}`} size="small" dataSource={preview.issues} columns={issueColumns} pagination={{ pageSize: 6 }} scroll={{ x: 700 }} />
          ) : <Alert type="success" showIcon icon={<CheckCircleOutlined />} title="预检通过，可以导入" />}
          <Table className="business-import-table" rowKey="row_key" size="small" dataSource={preview.rows} columns={rowColumns} pagination={{ pageSize: 10 }} scroll={{ x: 1295 }} />
          <div className="import-commit-bar">
            <span>确认后将一次性处理 {validRows} 条业务记录。</span>
            <Space wrap>
              {preview.issues.length > 0 && <Button href={businessImportApi.errorReportUrl(preview.token)}>下载问题报告</Button>}
              <Button type="primary" disabled={busy || preview.error_count > 0 || validRows === 0} loading={commitMutation.isPending} onClick={() => commitMutation.mutate(preview.token)}>确认整批导入</Button>
            </Space>
          </div>
        </div>
      )}
    </Drawer>
  )
}
