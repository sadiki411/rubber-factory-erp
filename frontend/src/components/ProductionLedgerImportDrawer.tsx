import { CameraOutlined, CheckCircleOutlined, DownloadOutlined, InboxOutlined, UploadOutlined, WarningOutlined } from '@ant-design/icons'
import { Alert, App, Button, Card, Descriptions, Drawer, Empty, Progress, Table, Tabs, Tag, Typography, Upload } from 'antd'
import type { TableColumnsType, UploadFile } from 'antd'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { productionApi, productionImportApi } from '../api/client'
import type { ProductionRun } from '../types'
import { ProductionCounterDrawer } from './ProductionCounterDrawer'
import { ProductionLedgerTaskDrawer } from './ProductionLedgerTaskDrawer'

interface Props {
  open: boolean
  onClose: () => void
}

interface LedgerIssue {
  level: 'error' | 'warning'
  message: string
  sheet?: string
  row?: number
  field?: string
}

interface LedgerTaskPreview {
  key: string
  order_no: string
  item_no?: string
  specification?: string
  material?: string
  order_quantity?: number
  station_id?: number | null
  mold_id?: number | null
  cavities?: number
  planned_mold_count?: number
  logs?: Array<Record<string, any>>
}

interface LedgerPreview {
  token: string
  can_commit: boolean
  tasks: LedgerTaskPreview[]
  errors: LedgerIssue[]
  warnings: LedgerIssue[]
}

interface OcrBlockingItem {
  field: string
  message: string
  row?: number
}

interface OcrTaskPreview {
  file_name: string
  rotation_degrees?: number
  draft: Record<string, any>
  field_confidence?: Record<string, number>
  blocking_items?: OcrBlockingItem[]
  text_lines?: Array<{ text: string; confidence: number }>
}

interface OcrPreview {
  tasks?: OcrTaskPreview[]
  can_commit?: boolean
  requires_human_confirmation?: boolean
  detail?: string
}

function confidenceTag(value?: number) {
  if (value === undefined) return <Tag>未识别</Tag>
  return <Tag color={value >= 85 ? 'success' : value >= 60 ? 'warning' : 'error'}>{Math.round(value)}%</Tag>
}

export function ProductionLedgerImportDrawer({ open, onClose }: Props) {
  const { message, modal } = App.useApp()
  const queryClient = useQueryClient()
  const [tab, setTab] = useState('excel')
  const [excelFiles, setExcelFiles] = useState<UploadFile[]>([])
  const [photoFiles, setPhotoFiles] = useState<UploadFile[]>([])
  const [ledgerPreview, setLedgerPreview] = useState<LedgerPreview>()
  const [ocrPreview, setOcrPreview] = useState<OcrPreview>()
  const [draftToCreate, setDraftToCreate] = useState<Record<string, any>>()
  const [ocrRun, setOcrRun] = useState<ProductionRun>()
  const [pendingOcrLogs, setPendingOcrLogs] = useState<Array<Record<string, any>>>([])
  const [confirmingOcrLog, setConfirmingOcrLog] = useState(false)

  const ledgerPreviewMutation = useMutation({
    mutationFn: (file: File) => productionImportApi.ledgerPreview(file),
    onSuccess: (result) => {
      setLedgerPreview(result as unknown as LedgerPreview)
      message.success('简化生产手工账预检完成')
    },
    onError: (error: Error) => message.error(error.message),
  })
  const ledgerCommitMutation = useMutation({
    mutationFn: ({ token, confirmWarnings }: { token: string; confirmWarnings: boolean }) => productionImportApi.ledgerCommit(token, confirmWarnings),
    onSuccess: async (result) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['production'] }),
        queryClient.invalidateQueries({ queryKey: ['orders'] }),
        queryClient.invalidateQueries({ queryKey: ['analytics'] }),
      ])
      const created = Array.isArray(result.created_run_ids) ? result.created_run_ids.length : 0
      message.success(`已整批导入 ${created} 个生产任务，交接读数已同步计算`)
      setLedgerPreview(undefined)
      setExcelFiles([])
      onClose()
    },
    onError: (error: Error) => message.error(error.message),
  })
  const ocrMutation = useMutation({
    mutationFn: (files: File[]) => productionImportApi.ocrPreview(files),
    onSuccess: (result) => {
      setOcrPreview(result as unknown as OcrPreview)
      message.success('照片逐张识别完成，请对照原图核对红色项目')
    },
    onError: (error: Error) => message.error(error.message),
  })

  const busy = ledgerPreviewMutation.isPending || ledgerCommitMutation.isPending || ocrMutation.isPending
  const issues = useMemo(() => [...(ledgerPreview?.errors || []), ...(ledgerPreview?.warnings || [])], [ledgerPreview])
  const close = () => {
    if (busy) return
    setLedgerPreview(undefined)
    setOcrPreview(undefined)
    setExcelFiles([])
    setPhotoFiles([])
    setDraftToCreate(undefined)
    setOcrRun(undefined)
    setPendingOcrLogs([])
    setConfirmingOcrLog(false)
    onClose()
  }

  const handleOcrTaskSaved = (run: ProductionRun) => {
    const logs = Array.isArray(draftToCreate?.logs) ? draftToCreate.logs : []
    setDraftToCreate(undefined)
    if (!logs.length) {
      message.success('识别任务已建立；没有可用交接行，请在任务卡手工补录累计模数。')
      return
    }
    setOcrRun(run)
    setPendingOcrLogs(logs)
    setConfirmingOcrLog(true)
    message.info(`任务已建立，接下来请逐行核对并保存 ${logs.length} 条累计模数。`)
  }

  const finishOneOcrLog = async () => {
    if (ocrRun) {
      try {
        setOcrRun(await productionApi.detailRun(ocrRun.id))
      } catch {
        // The row is already saved. Keep the confirmation queue moving; the
        // server still validates the next cumulative reading against its
        // authoritative previous value.
      }
    }
    setPendingOcrLogs((rows) => {
      const remaining = rows.slice(1)
      if (!remaining.length) {
        setConfirmingOcrLog(false)
        setOcrRun(undefined)
        message.success('照片中的交接累计模数已逐行人工确认完成。')
      }
      return remaining
    })
  }

  const issueColumns: TableColumnsType<LedgerIssue> = [
    { title: '级别', dataIndex: 'level', width: 80, render: (value) => <Tag color={value === 'error' ? 'error' : 'warning'}>{value === 'error' ? '阻断' : '核对'}</Tag> },
    { title: '位置', key: 'position', width: 150, render: (_, row) => [row.sheet, row.row ? `第${row.row}行` : '', row.field].filter(Boolean).join(' · ') || '-' },
    { title: '原因', dataIndex: 'message' },
  ]

  const previewExcel = () => {
    const file = excelFiles[0]?.originFileObj
    if (!file) return message.warning('请先选择简化Excel文件')
    setLedgerPreview(undefined)
    ledgerPreviewMutation.mutate(file)
  }
  const previewPhotos = () => {
    const files = photoFiles.map((item) => item.originFileObj).filter(Boolean) as File[]
    if (!files.length) return message.warning('请至少选择一张纸质生产表照片')
    setOcrPreview(undefined)
    ocrMutation.mutate(files)
  }
  const commitLedger = () => {
    if (!ledgerPreview) return
    if (ledgerPreview.warnings.length) {
      modal.confirm({
        title: '确认这些警告不是重复补录？',
        content: `本次有 ${ledgerPreview.warnings.length} 条需要人工核对的警告。确认后整批写入；取消则不会写入任何数据。`,
        okText: '已逐条核对，确认导入',
        cancelText: '返回检查',
        onOk: () => ledgerCommitMutation.mutateAsync({ token: ledgerPreview.token, confirmWarnings: true }),
      })
      return
    }
    ledgerCommitMutation.mutate({ token: ledgerPreview.token, confirmWarnings: false })
  }

  return (
    <>
      <Drawer open={open} onClose={close} closable={!busy} maskClosable={!busy} keyboard={!busy} size={860} title="补录生产手工账">
        <Alert type="info" showIcon title="只保留现场真正会填写的内容" description="一项生产任务只需确认唯一订单和孔数；每次交接只填机台累计模数。机台、人员、日期可暂时留空以后补录，所有导入都先预检且不会部分成功。" />
        <Tabs activeKey={tab} onChange={setTab} items={[
          {
            key: 'excel', label: '简化Excel', children: <>
              <div className="production-import-toolbar"><Button icon={<DownloadOutlined />} href={productionImportApi.ledgerTemplateUrl}>下载简化模板</Button><Typography.Text type="secondary">“生产任务”一行一个任务，“交接读数”一行一次累计读数。</Typography.Text></div>
              <Upload.Dragger accept=".xlsx" maxCount={1} fileList={excelFiles} disabled={busy} beforeUpload={() => false} onChange={({ fileList }) => { setExcelFiles(fileList); setLedgerPreview(undefined) }}>
                <p className="ant-upload-drag-icon"><InboxOutlined /></p><p className="ant-upload-text">点击或拖入简化生产手工账</p><p className="ant-upload-hint">不用填写上模、停机、成本等不适合纸质账的字段</p>
              </Upload.Dragger>
              <Button className="production-import-primary" type="primary" block icon={<UploadOutlined />} disabled={!excelFiles.length || busy} loading={ledgerPreviewMutation.isPending} onClick={previewExcel}>上传并预检</Button>
              {ledgerPreview && <div className="production-ledger-import-preview">
                <Descriptions size="small" column={{ xs: 2, sm: 4 }}>
                  <Descriptions.Item label="生产任务">{ledgerPreview.tasks.length}</Descriptions.Item>
                  <Descriptions.Item label="交接读数">{ledgerPreview.tasks.reduce((sum, item) => sum + (item.logs?.length || 0), 0)}</Descriptions.Item>
                  <Descriptions.Item label="阻断错误"><span className="error-text">{ledgerPreview.errors.length}</span></Descriptions.Item>
                  <Descriptions.Item label="需核对"><span className="warning-text">{ledgerPreview.warnings.length}</span></Descriptions.Item>
                </Descriptions>
                <Progress percent={ledgerPreview.can_commit ? 100 : 0} status={ledgerPreview.can_commit ? 'success' : 'exception'} />
                {issues.length ? <Table rowKey={(row) => `${row.level}-${row.sheet}-${row.row}-${row.field}-${row.message}`} size="small" dataSource={issues} columns={issueColumns} pagination={false} scroll={{ x: 580 }} /> : <Alert type="success" showIcon icon={<CheckCircleOutlined />} title="预检通过，可以整批导入" />}
                <div className="production-ledger-import-tasks">{ledgerPreview.tasks.map((task) => <Card size="small" key={task.key} title={`${task.order_no}${task.item_no ? ` / ${task.item_no}` : ''}`} extra={<Tag color="processing">{task.logs?.length || 0}次交接</Tag>}><b>{task.specification || '规格待补'} · {task.material || '材质待补'}</b><div>{task.cavities || '-'}孔 · 目标{task.planned_mold_count || '-'}模 · 机台{task.station_id || '待补'} · 模具{task.mold_id || '待补'}</div></Card>)}</div>
                <div className="import-commit-bar"><span>{ledgerPreview.errors.length ? '存在阻断错误，本次不会写入任何数据。' : `确认后一次写入 ${ledgerPreview.tasks.length} 个任务。`}</span><Button type="primary" disabled={!ledgerPreview.can_commit || !ledgerPreview.tasks.length || busy} loading={ledgerCommitMutation.isPending} onClick={commitLedger}>确认整批导入</Button></div>
              </div>}
            </>,
          },
          {
            key: 'photo', label: '拍照识别（先核对）', children: <>
              <Alert type="warning" showIcon title="照片识别只做草稿，不会直接写入" description="手写数字、纸张阴影和角度都可能误识别。订单号、孔数、目标模数、累计模数低于85%置信度时会标红，必须对照原纸人工改正。" />
              {ocrRun && pendingOcrLogs.length > 0 && <Alert
                type="warning"
                showIcon
                title={`还有 ${pendingOcrLogs.length} 条识别交接行尚未人工确认`}
                description={`${ocrRun.order_no}${ocrRun.order_item_no ? ` / ${ocrRun.order_item_no}` : ''}；关闭确认表单不会写入该行，也不会丢弃待确认队列。`}
                action={<Button type="primary" onClick={() => setConfirmingOcrLog(true)}>继续逐行核对</Button>}
              />}
              <Upload.Dragger accept="image/jpeg,image/png" multiple maxCount={20} listType="picture" fileList={photoFiles} disabled={busy} beforeUpload={() => false} onChange={({ fileList }) => { setPhotoFiles(fileList); setOcrPreview(undefined) }}>
                <p className="ant-upload-drag-icon"><CameraOutlined /></p><p className="ant-upload-text">拍照或选择纸质生产统计表</p><p className="ant-upload-hint">一张照片识别为一个任务，可一次选择多张并逐张预览</p>
              </Upload.Dragger>
              <Button className="production-import-primary" type="primary" block icon={<CameraOutlined />} disabled={!photoFiles.length || busy} loading={ocrMutation.isPending} onClick={previewPhotos}>逐张识别并预览</Button>
              {ocrPreview?.detail && <Alert type="error" showIcon title="服务器照片识别暂不可用" description={ocrPreview.detail} />}
              {ocrPreview?.tasks?.length ? <div className="production-ocr-results">{ocrPreview.tasks.map((task, index) => {
                const draft = task.draft || {}
                const blocking = task.blocking_items || []
                return <Card key={`${task.file_name}-${index}`} title={`${index + 1}. ${task.file_name}`} extra={<Tag color={blocking.length ? 'error' : 'success'}>{blocking.length ? `${blocking.length}项需核对` : '关键项已识别'}</Tag>}>
                  {task.rotation_degrees ? <Typography.Paragraph type="secondary">已自动旋转 {task.rotation_degrees}° 后识别</Typography.Paragraph> : null}
                  <div className="production-ocr-field-grid">
                    {[
                      ['order_no', '订单号'], ['specification', '规格'], ['material', '材质'], ['order_quantity', '订单数量'], ['cavities', '孔数'], ['planned_mold_count', '目标模数'],
                    ].map(([field, label]) => <div key={field}><small>{label}</small><b>{draft[field] ?? '未识别'}</b>{confidenceTag(task.field_confidence?.[field])}</div>)}
                  </div>
                  {Array.isArray(draft.logs) && draft.logs.length > 0 && <div className="production-ocr-log-list"><Typography.Text strong>识别到的交接行</Typography.Text>{draft.logs.map((log: Record<string, any>, rowIndex: number) => <div key={rowIndex}><span>第{rowIndex + 1}行 · {log.production_date || '日期待补'} · {log.operator || '人员待补'}</span><b>累计 {log.cumulative_mold_count ?? '待核对'} 模</b></div>)}</div>}
                  {blocking.length ? <Alert type="error" showIcon icon={<WarningOutlined />} title="以下内容禁止直接采用" description={<ul>{blocking.map((item, itemIndex) => <li key={`${item.field}-${itemIndex}`}>{item.row ? `第${item.row}行：` : ''}{item.message}</li>)}</ul>} /> : <Alert type="success" showIcon title="仍需对照原纸确认后再保存" />}
                  <Button className="production-ocr-create" type="primary" onClick={() => setDraftToCreate(draft)}>核对并带入新增任务</Button>
                </Card>
              })}</div> : ocrPreview ? <Empty description="没有识别到可预览的任务" /> : null}
            </>,
          },
        ]} />
      </Drawer>
      <ProductionLedgerTaskDrawer open={Boolean(draftToCreate)} initialDraft={draftToCreate} onClose={() => setDraftToCreate(undefined)} onSaved={handleOcrTaskSaved} />
      <ProductionCounterDrawer
        open={Boolean(ocrRun && pendingOcrLogs.length && confirmingOcrLog)}
        run={ocrRun}
        initialValues={pendingOcrLogs[0]}
        keepOpenAfterSave
        onClose={() => setConfirmingOcrLog(false)}
        onSaved={finishOneOcrLog}
      />
    </>
  )
}
