import { Alert, App, Button, Card, Checkbox, Input, Modal, Space, Tag, Typography } from 'antd'
import { useState } from 'react'
import { flowApi, flowRequestKey, finishFlowRequest, PAPER_LAYOUT_LABEL, type FlowCard, type PrintJob } from '../flowcards'

export function FlowVoidDialog({cards, close, changed}: {cards: FlowCard[]; close:()=>void; changed:()=>void}) {
  const { message } = App.useApp()
  const [reason,setReason] = useState('')
  const [checked,setChecked] = useState(false)
  const [busy,setBusy] = useState(false)
  const [error,setError] = useState('')
  const save = async () => {
    const card_ids = cards.map(c=>c.id)
    const body = {card_ids,kind:'VOID',evidence:reason.trim(),scope:'误发未使用',
      values:{reason:reason.trim(),unused_confirmed:true}}
    const slot = `void-cards:${card_ids.join(',')}`
    setBusy(true)
    try {
      await flowApi.record({...body,request_key:flowRequestKey(slot,body)})
      finishFlowRequest(slot)
      changed()
      message.success('误发卡已作废，原卡号保留供追溯')
      close()
    } catch (e) {setError((e as Error).message)}
    finally {setBusy(false)}
  }
  return <Modal open title={`作废误发工序卡（${cards.length} 张）`} onCancel={close} onOk={()=>void save()}
    okText="确认作废" cancelText="返回" confirmLoading={busy} okButtonProps={{danger:true,disabled:busy||!checked||!reason.trim()}}>
    <Typography.Paragraph>作废后原卡不能补打或继续使用。原记录保留，不影响订单、生产台账或出货。</Typography.Paragraph>
    <Typography.Paragraph>本次范围：{cards.map(c=>`第 ${c.sequence}/${c.denominator} 批`).join('、')}</Typography.Paragraph>
    {error&&<Alert type="error" title={error} />}
    <Input.TextArea aria-label="作废原因" value={reason} onChange={e=>setReason(e.target.value)} maxLength={500} placeholder="填写作废原因" />
    <Checkbox style={{marginTop:12}} checked={checked} onChange={e=>setChecked(e.target.checked)}>确认误发、从未使用，已收回或销毁原纸卡</Checkbox>
  </Modal>
}

export function FlowPrintResults({ job, changed, disabled, cardId, groupId }: {
  job: PrintJob; changed: (job: PrintJob) => void; disabled: boolean; cardId?: string; groupId?:string
}) {
  const { message, modal } = App.useApp()
  const [selected, setSelected] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const items = job.items.filter(item => (!cardId || item.card_id === cardId) && (!groupId || item.group_id === groupId))
  const active = items.filter(item => item.active)
  const checked = active.filter(item => selected.includes(item.card_id))
  const update = async (ids: string[], status: 'SUCCESS' | 'CANCELLED') => {
    setBusy(true)
    try {
      changed(await flowApi.printResult(job.id, Object.fromEntries(ids.map(id => [id, status]))))
      setSelected([])
      message.success(status === 'SUCCESS' ? '已确认实际打印成功' : '已取消未完成打印，原卡号保留，可重新打印')
    } catch (e) {
      message.error((e as Error).message)
      // Recover the original task state after a lost response or concurrent confirmation.
      await flowApi.printJob(job.id).then(changed).catch(() => {})
    } finally { setBusy(false) }
  }
  const cancel = () => {
    const targets = checked.length ? checked : active
    modal.confirm({
      title: `取消 ${targets.length} 张未确认的打印？`,
      content: '仅在没打印出来或废纸已处理时取消。已经打出的请先确认成功。取消保留原卡号和记录，不撤销订单、也不会停止打印机里的队列。',
      okText: '确认取消打印', cancelText: '返回', okButtonProps: { danger: true },
      onOk: () => update(targets.map(item => item.card_id), 'CANCELLED'),
    })
  }
  const layout = job.paper_layout ?? 'SINGLE'
  return <Card size="small" title="打印任务" extra={<Tag color={active.length ? 'orange' : 'default'}>{active.length ? '待确认打印结果' : '此处无待确认项'}</Tag>}>
    <Typography.Paragraph>纸张：{PAPER_LAYOUT_LABEL[layout]}。{layout === 'A4_TWO_UP' ? 'A4 纵向，实际大小 100%，每张纸 1 页。' : '原卡尺寸，实际大小 100%。'}{job.second_inspection ? '含一检、二检签名。' : ''}</Typography.Paragraph>
    {items.length < job.items.length && <Alert type="info" title={cardId?'原 PDF 还有其他批次；下方确认或取消仅作用于本批。':'原 PDF 还有其他卡组；下方确认或取消仅作用于当前卡组。'} />}
    {job.file_prepared ? <Button type={active.length ? 'primary' : 'default'} href={job.pdf_url} target="_blank">{active.length ? '打开原 PDF 打印' : '查看历史 PDF'}</Button> :
      <Button disabled={disabled || !active.length} loading={busy} onClick={() => { setBusy(true); void flowApi.preparePrint(job.id).then(changed).catch((e: Error) => message.error(e.message)).finally(() => setBusy(false)) }}>继续生成原 PDF</Button>}
    {active.length > 0 && <Typography.Paragraph style={{marginTop:8}}>已打出的卡请勾选「确认打印成功」。未打或打坏的卡可取消，再从原卡补打。</Typography.Paragraph>}
    <Checkbox.Group value={selected} onChange={setSelected} style={{display:'flex',flexDirection:'column',gap:6}}
      options={items.map(i => ({label: `第 ${i.sequence}/${i.denominator} 批 · ${i.proposed_reprint ? `补打 ${i.proposed_reprint} 次` : '初印'} · ${i.active?'待确认':i.status==='SUCCESS'?'已打印':'已取消'}`,
        value:i.card_id, disabled:disabled||busy||!i.active}))} />
    {active.length > 0 && <Space wrap style={{marginTop:12}}>
      {active.length > 1 && <Button disabled={disabled||busy} onClick={()=>setSelected(active.map(i=>i.card_id))}>选中全部待确认</Button>}
      <Button type="primary" disabled={disabled||!checked.length} loading={busy} onClick={()=>void update(checked.map(i=>i.card_id),'SUCCESS')}>确认所选打印成功</Button>
      <Button danger disabled={disabled} loading={busy} onClick={cancel}>{checked.length ? '取消所选打印' : '取消未确认打印'}</Button>
    </Space>}
  </Card>
}
