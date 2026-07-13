import { CheckCircleOutlined, DownloadOutlined, InboxOutlined, UploadOutlined } from '@ant-design/icons'
import { Alert, App, Button, Descriptions, Drawer, Progress, Space, Table, Tag, Upload } from 'antd'
import type { TableColumnsType, UploadFile } from 'antd'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { productionImportApi } from '../api/client'
import { formatProductionDate } from '../production'
import type { ProductionImportIssue, ProductionImportPreview, ProductionImportPreviewRow } from '../types'

interface Props {
  open: boolean
  onClose: () => void
}

export function ProductionImportDrawer({ open, onClose }: Props) {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [files, setFiles] = useState<UploadFile[]>([])
  const [preview, setPreview] = useState<ProductionImportPreview>()
  const previewMutation = useMutation({
    mutationFn: (file: File) => productionImportApi.preview(file),
    onSuccess: (result) => { setPreview(result); message.success('生产统计表预检完成') },
    onError: (error: Error) => message.error(error.message),
  })
  const commitMutation = useMutation({
    mutationFn: (token: string) => productionImportApi.commit(token),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ['production'] })
      message.success(`已导入 ${result.imported_count} 张生产订单、${result.log_count} 条人员模数记录${result.settled_count ? `、${result.settled_count} 张完工结算` : ''}`)
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

  const issueColumns: TableColumnsType<ProductionImportIssue> = [
    { title: '级别', dataIndex: 'level', width: 80, render: (value) => <Tag color={value === 'error' ? 'error' : 'warning'}>{value === 'error' ? '错误' : '警告'}</Tag> },
    { title: '工作表', dataIndex: 'sheet', width: 130 },
    { title: '位置', dataIndex: 'row', width: 70, render: (value) => value || '-' },
    { title: '字段', dataIndex: 'field', width: 120, render: (value) => value || '-' },
    { title: '说明', dataIndex: 'message' },
  ]
  const rowColumns: TableColumnsType<ProductionImportPreviewRow> = [
    { title: '工作表', dataIndex: 'sheet', width: 130 },
    { title: '订单编号', dataIndex: 'order_no', width: 180 },
    { title: '机台', dataIndex: 'station_code', width: 100, render: (value) => value || '-' },
    { title: '模具', dataIndex: 'mold_code', width: 130, render: (value) => value || '-' },
    { title: '孔数', dataIndex: 'cavities', width: 75, render: (value) => value ?? '-' },
    { title: '预计工时', dataIndex: 'estimated_hours', width: 100, render: (value) => value === null || value === undefined || value === '' ? '-' : `${value} 小时` },
    { title: '上模时间', dataIndex: 'loaded_at', width: 145, render: (value) => formatProductionDate(value, 'MM-DD HH:mm') },
    { title: '预计换模', dataIndex: 'expected_change_at', width: 145, render: (value) => formatProductionDate(value, 'MM-DD HH:mm') },
    { title: '下机时间', dataIndex: 'unloaded_at', width: 145, render: (value) => formatProductionDate(value, 'MM-DD HH:mm') },
    { title: '人员模数记录', dataIndex: 'daily_log_count', width: 115, render: (value) => value ?? 0 },
    { title: '完工结算', dataIndex: 'is_settled', width: 95, render: (value) => <Tag color={value ? 'success' : 'default'}>{value ? '已填写' : '待结算'}</Tag> },
    { title: '实际良品', dataIndex: 'actual_good_quantity', width: 95, render: (value) => value ?? '-' },
    { title: '实际不良', dataIndex: 'actual_defective_quantity', width: 95, render: (value) => value ?? '-' },
    { title: '总材料kg', dataIndex: 'total_material_kg', width: 95, render: (value) => value ?? '-' },
    { title: '人工成本', dataIndex: 'labor_cost', width: 95, render: (value) => value === null || value === undefined ? '-' : `¥${value}` },
    { title: '能耗成本', dataIndex: 'energy_cost', width: 95, render: (value) => value === null || value === undefined ? '-' : `¥${value}` },
    { title: '其他成本', dataIndex: 'other_cost', width: 95, render: (value) => value === null || value === undefined ? '-' : `¥${value}` },
    { title: '结算备注', dataIndex: 'settlement_notes', width: 180, ellipsis: true, render: (value) => value || '-' },
    {
      title: '状态', dataIndex: 'status', width: 90,
      render: (value) => ({ PLANNED: '待上模', RUNNING: '生产中', COMPLETED: '已完成', CANCELLED: '已取消' })[value as string] || value || '-',
    },
    {
      title: '校验', key: 'valid', width: 80,
      render: (_, row) => {
        const failed = row.valid === false || preview?.issues.some((issue) => issue.level === 'error' && issue.sheet === row.sheet)
        return <Tag color={failed ? 'error' : 'success'}>{failed ? '失败' : '通过'}</Tag>
      },
    },
  ]
  const failedSheets = new Set(preview?.issues.filter((issue) => issue.level === 'error').map((issue) => issue.sheet).filter(Boolean))
  const validRows = preview?.rows.filter((row) => row.valid !== false && !failedSheets.has(row.sheet)).length || 0
  const settledRows = preview?.rows.filter((row) => row.is_settled).length || 0
  const percent = preview?.total_rows ? Math.round((validRows / preview.total_rows) * 100) : 0

  return (
    <Drawer
      open={open}
      onClose={closeDrawer}
      closable={!busy}
      maskClosable={!busy}
      keyboard={!busy}
      size={820}
      title="导入生产订单统计表"
      extra={<Button icon={<DownloadOutlined />} href={productionImportApi.templateUrl}>下载新版模板</Button>}
    >
      <Alert
        type="info"
        showIcon
        title="新版模板每个工作表对应一张生产订单，可复制工作表批量填写。"
        description="日报区只填写日期、作业员、模数和备注；已完工订单可在结算区一次填写实际产量、材料及成本。系统预检通过后才会写入数据库。"
      />
      <div className="production-import-upload">
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
          <p className="ant-upload-text">点击或拖入填写完成的生产统计表</p>
          <p className="ant-upload-hint">请使用“新版模板”，原扫描表缺少必要字段，无法可靠识别。</p>
        </Upload.Dragger>
        <Button type="primary" block icon={<UploadOutlined />} disabled={!files.length || busy} loading={previewMutation.isPending} onClick={startPreview}>上传并预检</Button>
      </div>

      {preview && (
        <div className="production-import-preview">
          <Descriptions column={{ xs: 2, sm: 4 }} size="small">
            <Descriptions.Item label="工作表">{preview.sheet_count}</Descriptions.Item>
            <Descriptions.Item label="可导入">{validRows}</Descriptions.Item>
            <Descriptions.Item label="人员模数记录">{preview.daily_log_count || 0}</Descriptions.Item>
            <Descriptions.Item label="完工结算">{settledRows}</Descriptions.Item>
            <Descriptions.Item label="错误 / 警告"><span className="error-text">{preview.error_count}</span> / <span className="warning-text">{preview.warning_count}</span></Descriptions.Item>
          </Descriptions>
          <Progress percent={percent} status={preview.error_count ? 'exception' : 'success'} />
          {preview.issues?.length ? (
            <Table rowKey={(row) => `${row.sheet}-${row.row}-${row.field}-${row.message}`} size="small" dataSource={preview.issues} columns={issueColumns} pagination={{ pageSize: 6 }} scroll={{ x: 650 }} />
          ) : <Alert type="success" showIcon icon={<CheckCircleOutlined />} title="预检通过，可以导入" />}
          <Table className="production-import-table" rowKey="row_key" size="small" dataSource={preview.rows} columns={rowColumns} pagination={{ pageSize: 8 }} scroll={{ x: 2330 }} />
          <div className="import-commit-bar">
            <span>确认后将一次性写入 {validRows} 张生产订单。</span>
            <Space>
              {preview.issues?.length > 0 && <Button href={productionImportApi.errorReportUrl(preview.token)}>下载问题报告</Button>}
              <Button type="primary" disabled={busy || preview.error_count > 0 || validRows === 0} loading={commitMutation.isPending} onClick={() => commitMutation.mutate(preview.token)}>确认导入</Button>
            </Space>
          </div>
        </div>
      )}
    </Drawer>
  )
}
