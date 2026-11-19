import { Alert, App, Button, Card, Checkbox, Input, InputNumber, Modal, Space, Typography } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useReadOnly } from '../access'
import { flowApi } from '../flowcards'

export function FlowTemplatePanel({ standardId }: { standardId?: number }) {
  const { message } = App.useApp()
  const readOnly = useReadOnly()
  const query = useQuery({ queryKey: ['flow-template-status'], queryFn: flowApi.templates })
  const [open, setOpen] = useState(false)
  const [confirmation, setConfirmation] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [width, setWidth] = useState(190)
  const [height, setHeight] = useState(130)
  const data = query.data
  const release = async () => {
    if (!data) return
    setBusy(true)
    try {
      await flowApi.publish(data.renderer, confirmation)
      await query.refetch(); setOpen(false); message.success('已核对发布结果；同版重复确认不会升版')
    } catch (e) { message.error(`发布结果未确认：${(e as Error).message}。请刷新核对原版本，不要另建版本。`) }
    finally { setBusy(false) }
  }
  return <Card size="small" title="模板预览与发布确认">
    {query.isError ? <Alert type="error" title="模板状态读取失败，不能当作草稿或已发布" action={<Button onClick={() => void query.refetch()}>重新核对</Button>} /> : !data ? <Typography.Text>正在读取模板状态…</Typography.Text> : <>
      <Typography.Paragraph>当前：{data.published ? `正式版 ${data.version}` : '草稿（尚未发布）'} · {data.renderer}。预览和下载不会升版，普通制卡/补打无需重复审批。</Typography.Paragraph>
      <Space wrap><span>预览宽mm</span><InputNumber aria-label="预览纸宽mm" min={180} max={220} value={width} onChange={(v) => setWidth(v || 190)} /><span>高mm</span><InputNumber aria-label="预览纸高mm" min={125} max={165} value={height} onChange={(v) => setHeight(v || 130)} />
        {['REQUIRED', 'NOT_REQUIRED'].map((whole) => <Button key={whole} href={`/api/flow-cards/templates/preview/?whole_mold=${whole}&width_mm=${width}&height_mm=${height}${standardId ? `&standard_id=${standardId}` : ''}`} target="_blank">{whole === 'REQUIRED' ? '预览需整模草稿' : '预览不需整模草稿'}</Button>)}
        <Button disabled={readOnly || !data.write_enabled || data.published} onClick={() => setOpen(true)}>确认此版已审核打印并发布</Button><Button onClick={() => void query.refetch()}>刷新发布结果</Button>
      </Space>
      <details><summary>历次正式模板（旧卡不套新模板）</summary>{data.history.map((v) => <div key={v.renderer}>版{v.version} · {v.renderer} · {v.created_at} · {v.confirmation}</div>)}</details>
    </>}
    <Modal title="正式发布打印确认" open={open} onCancel={() => setOpen(false)} onOk={() => void release()} confirmLoading={busy} okButtonProps={{ disabled: !confirmed || !confirmation.trim() || readOnly || !data?.write_enabled }}>
      <Alert type="info" title="仅发布当前模板；不会改旧卡、旧PDF或数量账。未实印审核请继续保留草稿。" />
      <Input.TextArea aria-label="发布审核依据" placeholder="填写本版实印审核依据、纸张尺寸及审核结果" value={confirmation} onChange={(e) => setConfirmation(e.target.value)} />
      <Checkbox checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)}>确认已核对本版并完成打印审核（不是仅下载预览）</Checkbox>
    </Modal>
  </Card>
}
