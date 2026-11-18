import { Alert, App, Button, Checkbox, DatePicker, Form, Input, InputNumber, Modal, Select, Space } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { qualityApi, toList } from '../api/client'
import { finishFlowRequest, flowApi, flowRequestKey, type FlowCardDetail } from '../flowcards'

function trays(value: string): number[] {
  const result = new Set<number>()
  for (const part of value.trim().split(/[,，、\s]+/).filter(Boolean)) {
    if (!/^\d+(-\d+)?$/.test(part)) throw new Error('盘号请填写1-3,5这样的明确范围；不连续盘号不会自动补齐')
    const [start, end = start] = part.split('-').map(Number)
    if (start < 1 || end < start || end-start > 499 || end > 100000) throw new Error('盘号范围无效或过大')
    for (let i = start; i <= end; i++) result.add(i)
    if (result.size > 500) throw new Error('一次最多500个实际盘号')
  }
  if (!result.size) throw new Error('请填写实际盘号')
  return [...result].sort((a, b) => a-b)
}

export function FlowBakePanel({ card, disabled, changed }: { card: FlowCardDetail; disabled: boolean; changed: () => void }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [form] = Form.useForm()
  const { message } = App.useApp()
  const employees = useQuery({ queryKey: ['flow-bake-employees'], queryFn: async () => toList(await qualityApi.listEmployees({ active: true })), enabled: open })
  const coupon = Form.useWatch('coupon', form)
  const coverageRows = Form.useWatch('coverage', form) || []
  const submit = async () => {
    setBusy(true); setError('')
    try {
      const v = await form.validateFields()
      const coverage = []
      for (const row of v.coverage) {
        const found = row.code.trim() === card.card_no ? card : await flowApi.scan(row.code.trim())
        coverage.push({ card_id: found.id, trays: trays(row.trays), scope: row.scope })
      }
      const ids = coverage.map((r) => r.card_id)
      const samples = v.coupon ? [{ sample_ref: v.sample_ref, material_ref: v.material_ref,
        card_ids: coverage.filter((_, i) => v.sample_rows?.includes(i)).map((r) => r.card_id), trays: trays(v.sample_trays), accompanied: v.accompanied === true }] : []
      const body = { card_ids: ids, kind: 'BAKE', employee_id: v.employee_id, business_at: v.business_at.toISOString(), scope: v.scope, evidence: v.evidence, replaces_id: v.replaces_id || null,
        values: { temperature_c: v.temperature_c, minutes: v.minutes, card_trays: coverage, coupon_samples: samples } }
      await flowApi.record({ ...body, request_key: flowRequestKey(`bake:${card.id}`, body) }); finishFlowRequest(`bake:${card.id}`)
      setOpen(false); changed(); message.success('已保存一次真实二烤及各卡盘号；不新增生产或库存数量')
    } catch (e) { setError(e instanceof Error ? e.message : '请核对必填项；未确认保存结果') }
    finally { setBusy(false) }
  }
  return <>
    <Button disabled={disabled} onClick={() => { form.resetFields(); form.setFieldsValue({ coverage: [{ code: card.card_no, scope: '本卡本次实际部分，需确认' }] }); setError(''); setOpen(true) }}>登记实际二烤 / 分盘续记</Button>
    <Modal title="实际二烤（一次记录覆盖多卡，不抄两套）" width={800} open={open} onCancel={() => setOpen(false)} onOk={() => void submit()} confirmLoading={busy} okButtonProps={{ disabled }}>
      <Alert type="info" title="实际温度时间不自动抄建议；一包多盘须逐项明确。分次、再烤另记一次，不覆盖原记录，不要求手填炉次。" />
      {error && <Alert type="error" title="二烤登记未确认" description={error} />}
      <Form name="flow-bake" form={form} layout="vertical">
        <div className="flow-form-grid"><Form.Item name="employee_id" label="实际二烤人员" rules={[{ required: true }]}><Select options={employees.data?.map((e) => ({ value: e.id, label: e.name }))} /></Form.Item><Form.Item name="business_at" label="实际作业日期时间" rules={[{ required: true }]}><DatePicker showTime format="YYYY-MM-DD HH:mm" classNames={{ popup: { root: 'flow-datetime-popup' } }} style={{ width: '100%' }} /></Form.Item><Form.Item name="temperature_c" label="实际温度 ℃" rules={[{ required: true }]}><InputNumber min={0.01} /></Form.Item><Form.Item name="minutes" label="实际保温 min" rules={[{ required: true }]}><InputNumber min={0.01} /></Form.Item></div>
        <Form.List name="coverage">{(fields, { add, remove }) => <>{fields.map((f) => <Space key={f.key} orientation="vertical" style={{ width: '100%' }}><Form.Item name={[f.name, 'code']} label={`本次第${f.name+1}张卡号`} rules={[{ required: true }]}><Input onChange={() => form.setFieldValue('sample_rows', [])} /></Form.Item><div className="flow-form-grid"><Form.Item name={[f.name, 'trays']} label="本卡实际盘号，例如1-3,5" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name={[f.name, 'scope']} label="本卡实际部分/整包范围" rules={[{ required: true }]}><Input /></Form.Item></div>{fields.length > 1 && <Button onClick={() => { remove(f.name); form.setFieldValue('sample_rows', []) }}>移除此卡（未保存）</Button>}</Space>)}<Button onClick={() => { add({}); form.setFieldValue('sample_rows', []) }}>同次二烤再关联一张卡</Button></>}</Form.List>
        <Form.Item name="coupon" valuePropName="checked"><Checkbox>本次有本厂当批胶料自制胶片随产品二烤</Checkbox></Form.Item>
        {coupon && <><Form.Item name="sample_ref" label="胶片原始标识" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name="material_ref" label="实际当批胶料原始标识（不虚构批号）" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name="sample_rows" label="该胶片明确代表哪些卡（按上面序号）" extra="增减卡或改卡号后需重新选择，防止序号变化误关联。" rules={[{ required: true }]}><Checkbox.Group options={coverageRows.map((_: unknown, i: number) => ({ value: i, label: `第${i+1}张卡` }))} /></Form.Item><Form.Item name="sample_trays" label="胶片对应取样盘号" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name="accompanied" valuePropName="checked"><Checkbox>确认此胶片实际随上述产品二烤；漏放部分不包含</Checkbox></Form.Item></>}
        <Form.Item name="scope" label="本次共同作业范围" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name="evidence" label="实际执行依据" rules={[{ required: true }]}><Input.TextArea /></Form.Item><Form.Item name="replaces_id" label="更正原二烤记录ID（可空；新一次分盘/再烤不填写）"><Input /></Form.Item>
      </Form>
    </Modal>
  </>
}
