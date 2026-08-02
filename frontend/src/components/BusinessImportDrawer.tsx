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

const CHANGE_FIELD_LABELS: Record<string, string> = {
  product_name: '产品名称',
  customer_product_no: '项目号',
  specification: '规格',
  material: '材质',
  material_length: '料长',
  cut_weight: '切料重',
  strip_count: '条数',
  primary_curing: '一次加硫条件',
  secondary_curing: '二次加硫条件',
  total_cavities: '总孔数',
  effective_cavities: '有效孔数',
  mold_in_stock: '模具在库',
  mold_no: '模具号',
  mold_size: '模具尺寸',
  notes: '备注',
  order_no: '订单号',
  item_no: '项次',
  order_quantity: '订单数量',
  order_date: '下单日期',
  due_date: '交期',
  forming_hours: '成型工时',
  production_required: '是否生产',
  legacy_shipment_text: '原出货信息',
  required_material_kg: '所需胶料',
  manual_received_material_kg: '手工已发胶料',
  process_card_text: '流程卡',
  production_quantity: '生产数量',
  shipment_date: '出货日期',
  shipped_quantity: '出货数量',
  status: '订单状态',
  finished_product_name: '成品品名',
  batch_no: '批号',
  sheet_size: '出片尺寸',
  weight_kg: '发料重量',
  issued_on: '发料日期',
  manufactured_on: '制造日期',
  project_no: '项目号',
  customer: '客户',
  category: '类别',
  version: '版本',
  inspection_item: '检验项目',
  lower_limit: '下限',
  upper_limit: '上限',
  unit: '单位',
  product_specification_id: '关联产品规格',
  source_system: '数据来源',
  source_document_at: '来源文件时间',
  external_key: '外部业务标识',
}

function formatChangeValue(value: unknown) {
  if (value === null || value === undefined || value === '') return '（空）'
  if (value === true) return '是'
  if (value === false) return '否'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function renderChanges(changes: BusinessImportPreviewRow['changes']) {
  const entries = Object.entries(changes || {})
  if (!entries.length) return '-'
  return (
    <div className="business-import-changes">
      {entries.map(([field, change]) => (
        <div key={field}>
          <strong>{CHANGE_FIELD_LABELS[field] || field}：</strong>
          <span>{formatChangeValue(change?.from)} → {formatChangeValue(change?.to)}</span>
        </div>
      ))}
    </div>
  )
}

function countOf(counts: Partial<BusinessImportCounts> | undefined, key: keyof BusinessImportCounts) {
  return Number(counts?.[key] || 0)
}

function totalOf(counts: Partial<BusinessImportCounts> | undefined) {
  return countOf(counts, 'product_specifications')
    + countOf(counts, 'orders')
    + countOf(counts, 'material_receipts')
    + countOf(counts, 'inspection_criteria')
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
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['business-import-history'] }),
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
        queryClient.invalidateQueries({ queryKey: ['business-import-history'] }),
      ])
      const created = result.created || result.imported || result.counts
      const createdTotal = result.created_count ?? result.imported_count ?? totalOf(created)
      const updatedTotal = result.updated_count ?? totalOf(result.updated)
      const skippedTotal = result.skipped_count ?? totalOf(result.skipped)
      message.success(`业务数据导入完成：新增 ${createdTotal} 条，更新 ${updatedTotal} 条，跳过 ${skippedTotal} 条`)
      setPreview(undefined)
      setFiles([])
      onClose()
    },
    onError: (error: Error) => {
      void queryClient.invalidateQueries({ queryKey: ['business-import-history'] })
      message.error(error.message)
    },
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
    { title: '拟变更字段（原值 → 新值）', dataIndex: 'changes', width: 360, render: renderChanges },
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
        title="系统会自动识别产品规格表、内部订单表、客户工作联络单和客户发料清单。"
        description="同一订单号与项次会增量更新现有订单；同一发料批号不会重复累计。导入前会显示新增、更新或跳过动作及全部问题，存在错误时整批不会写入数据库。"
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
          <Table className="business-import-table" rowKey="row_key" size="small" dataSource={preview.rows} columns={rowColumns} pagination={{ pageSize: 10 }} scroll={{ x: 1655 }} />
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
