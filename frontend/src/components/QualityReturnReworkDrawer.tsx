import {
  Alert,
  App,
  Button,
  Card,
  Col,
  DatePicker,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  List,
  Popconfirm,
  Row,
  Select,
  Space,
  Spin,
  Tag,
  Timeline,
  Typography,
} from 'antd'
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import type { Dayjs } from 'dayjs'
import dayjs from 'dayjs'
import { useEffect, useMemo, useState } from 'react'
import { qualityWorkflowApi } from '../api/client'
import { formatQualityDate, qualityNumber, reworkCaseSourceTitle } from '../quality'
import type {
  ApiList,
  QualityEmployee,
  QualityEmployeeSummary,
  QualityReturnableBatch,
  QualityReworkCase,
} from '../types'

function toList<T>(payload: ApiList<T> | T[]) {
  return Array.isArray(payload) ? payload : payload.results || []
}

function sourceInspectors(source?: { inspectors?: QualityEmployeeSummary[] } | null) {
  return (source?.inspectors || []).map((item) => item.name).filter(Boolean).join('、') || '待补录'
}

function returnableBatchTitle(item: QualityReturnableBatch) {
  return `${item.shipment_no} · ${item.order_no || '未关联订单'}${item.item_no ? ` / ${item.item_no}` : ''}`
}

function WholeBatchSummary({ item }: { item: QualityReturnableBatch }) {
  const available = item.available_batch_numbers || []
  return <div className="quality-return-source-summary">
    <div className="quality-return-source-heading">
      <div><strong>{returnableBatchTitle(item)}</strong><br /><Typography.Text type="secondary">{formatQualityDate(item.shipment_date)}</Typography.Text></div>
      <Space wrap size={[4, 4]}>
        <Tag color="blue">可退 {item.available_batches} / {item.total_batches} 批</Tag>
        {item.rework_count > 0 && <Tag color="orange">已有 {item.rework_count} 次返工</Tag>}
      </Space>
    </div>
    <div className="quality-return-source-product"><strong>{item.product_name || '-'}</strong><span>{[item.specification, item.material].filter(Boolean).join(' · ') || '规格材质未填写'}</span></div>
    <div className="quality-return-source-metrics">
      <span>整批件数 <strong>{qualityNumber(item.pieces_per_batch)}</strong> 件</span>
      <span>整批净重 <strong>{qualityNumber(item.single_batch_net_weight_kg, 3)}</strong> kg</span>
      <span>责任品检员 <strong>{sourceInspectors(item)}</strong></span>
    </div>
    {!!available.length && <Typography.Text type="secondary">可选物理批号：{available.slice(0, 8).join('、')}{available.length > 8 ? ` 等${available.length}批` : ''}</Typography.Text>}
  </div>
}

interface ReturnDrawerProps {
  open: boolean
  onClose: () => void
  onSaved: () => Promise<void>
  onBackfillShipment: () => void
}

export function QualityReturnReworkDrawer({ open, onClose, onSaved, onBackfillShipment }: ReturnDrawerProps) {
  const [form] = Form.useForm<Record<string, unknown>>()
  const { message } = App.useApp()
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<QualityReturnableBatch>()
  const [saving, setSaving] = useState(false)
  const openedOn = Form.useWatch('opened_on', form) as Dayjs | undefined

  const resetDrawer = () => {
    form.resetFields()
    form.setFieldsValue({ opened_on: dayjs(), reason_category: 'OTHER' })
    setSearch('')
    setQuery('')
    setSelected(undefined)
  }

  useEffect(() => {
    if (!open) return
    const timer = window.setTimeout(() => setQuery(search.trim()), 300)
    return () => window.clearTimeout(timer)
  }, [open, search])

  const candidatesQuery = useQuery({
    queryKey: ['quality', 'returnable-batches', query],
    queryFn: async () => toList(await qualityWorkflowApi.listReturnableBatches({ q: query || undefined, page_size: 50 })),
    enabled: open,
    retry: false,
  })
  const candidates = useMemo(() => candidatesQuery.data || [], [candidatesQuery.data])
  const activeSelected = useMemo(() => selected ? candidates.find((item) => item.key === selected.key) || selected : undefined, [candidates, selected])
  const selectedBatchNumbers = useMemo(() => activeSelected?.available_batch_numbers || [], [activeSelected?.available_batch_numbers])
  const selectCandidate = (item: QualityReturnableBatch) => {
    setSelected(item)
    form.setFieldValue('shipment_unit_no', item.available_batch_numbers?.[0])
  }

  const historical = Boolean(openedOn?.isValid() && openedOn.startOf('day').isBefore(dayjs().startOf('day')))
  const submit = async () => {
    if (!activeSelected) {
      message.warning('请先选择一条已确认出货记录')
      return
    }
    const values = await form.validateFields()
    const date = values.opened_on as Dayjs
    setSaving(true)
    try {
      await qualityWorkflowApi.createReworkCase({
        origin: 'CUSTOMER_RETURN',
        shipment_batch_id: activeSelected.shipment_batch_id,
        shipment_unit_no: values.shipment_unit_no,
        opened_on: date.format('YYYY-MM-DD'),
        backfill_reason: values.backfill_reason || '',
        reason_category: values.reason_category,
        reason: values.reason || '',
        notes: values.notes || '',
      })
      await onSaved()
      message.success(`已登记 ${activeSelected.shipment_no} 第${values.shipment_unit_no}批整批退货`)
      onClose()
    } catch (error) {
      message.error((error as Error).message || '登记退货返工失败')
    } finally {
      setSaving(false)
    }
  }

  const empty = !candidatesQuery.isLoading && !candidatesQuery.error && candidates.length === 0
  return <Drawer
    open={open}
    onClose={onClose}
    width={720}
    className="quality-return-rework-drawer"
    afterOpenChange={(visible) => { if (visible) resetDrawer() }}
    title="登记客户整批退货返工"
    footer={<Space className="drawer-footer-actions"><Button onClick={onClose}>取消</Button><Button type="primary" loading={saving} disabled={!activeSelected} onClick={() => void submit()}>确认登记整批退货</Button></Space>}
  >
    <Alert type="info" showIcon message="从已确认出货中选一整批" description="件数和重量按原出货自动带入且不可修改；同一次称重出货多批时，只需选择本次退回的物理批号。" />
    <div className="quality-return-source-search">
      <Input
        allowClear
        prefix={<SearchOutlined />}
        value={search}
        onChange={(event) => setSearch(event.target.value)}
        onPressEnter={() => setQuery(search.trim())}
        placeholder="搜索出货单、订单、项次、产品、规格或材质"
      />
      <Button icon={<ReloadOutlined />} onClick={() => void candidatesQuery.refetch()}>刷新</Button>
    </div>
    {candidatesQuery.error && <Alert type="error" showIcon message="已确认出货记录读取失败" description={(candidatesQuery.error as Error).message} action={<Button size="small" onClick={() => void candidatesQuery.refetch()}>重试</Button>} />}
    {candidatesQuery.isLoading && <div className="quality-return-source-loading"><Spin tip="正在读取已确认出货…" /></div>}
    {empty && <Empty description={query ? '没有找到可退整批的已确认出货记录' : '暂无可退整批的已确认出货记录'}>
      <Space direction="vertical"><Typography.Text type="secondary">若原出货尚未登记，请先补录并确认出货，再返回选择。</Typography.Text><Button type="primary" onClick={onBackfillShipment}>补录原出货</Button></Space>
    </Empty>}
    {!!candidates.length && <List
      className="quality-return-source-list"
      dataSource={candidates}
      renderItem={(item) => <List.Item>
        <Card className={activeSelected?.key === item.key ? 'quality-return-source-card is-selected' : 'quality-return-source-card'} size="small">
          <WholeBatchSummary item={item} />
          <Button type={activeSelected?.key === item.key ? 'primary' : 'default'} disabled={!item.available_batches || !item.available_batch_numbers?.length} onClick={() => selectCandidate(item)}>
            {activeSelected?.key === item.key ? '已选择此出货' : '选择一整批退货'}
          </Button>
        </Card>
      </List.Item>}
    />}
    {activeSelected && <Card className="quality-return-selected-card" title="本次退货（自动取原出货数据）" size="small">
      <WholeBatchSummary item={activeSelected} />
      <Form form={form} layout="vertical" requiredMark="optional">
        <Row gutter={12}>
          <Col xs={24} sm={12}><Form.Item name="shipment_unit_no" label="本次退回的物理批号" rules={[{ required: true, message: '请选择物理批号' }]}><Select options={selectedBatchNumbers.map((value) => ({ value, label: `物理批号 ${value}（本组共${activeSelected.total_batches}批）` }))} /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="opened_on" label="退货日期" rules={[{ required: true, message: '请选择退货日期' }]}><DatePicker style={{ width: '100%' }} /></Form.Item></Col>
        </Row>
        <Row gutter={12}>
          <Col xs={12}><Form.Item label="整批件数"><Input value={`${qualityNumber(activeSelected.pieces_per_batch)} 件`} disabled /></Form.Item></Col>
          <Col xs={12}><Form.Item label="整批净重"><Input value={`${qualityNumber(activeSelected.single_batch_net_weight_kg, 3)} kg`} disabled /></Form.Item></Col>
        </Row>
        {historical && <Form.Item name="backfill_reason" label="补录原因" rules={[{ required: true, whitespace: true, message: '补录历史日期时请填写原因' }]}><Input placeholder="例如：当天漏记，现按退货单补录" /></Form.Item>}
        <Form.Item name="reason_category" label="原因分类" rules={[{ required: true }]}><Select options={[{ value: 'APPEARANCE', label: '外观' }, { value: 'DIMENSION', label: '尺寸' }, { value: 'MATERIAL', label: '材质' }, { value: 'MIXED', label: '混料/混装' }, { value: 'PACKAGING', label: '包装' }, { value: 'OTHER', label: '其他' }]} /></Form.Item>
        <Form.Item name="reason" label="问题描述（选填）"><Input.TextArea rows={2} maxLength={500} showCount /></Form.Item>
        <Form.Item name="notes" label="备注（选填）"><Input.TextArea rows={2} maxLength={500} showCount /></Form.Item>
      </Form>
    </Card>}
  </Drawer>
}

interface AttemptDrawerProps {
  open: boolean
  item?: QualityReworkCase
  employees: QualityEmployee[]
  onClose: () => void
  onSaved: () => Promise<void>
}

export function QualityReturnReworkAttemptDrawer({ open, item, employees, onClose, onSaved }: AttemptDrawerProps) {
  const [form] = Form.useForm<Record<string, unknown>>()
  const [saving, setSaving] = useState(false)
  const { message } = App.useApp()
  const attemptNo = Number(item?.attempt_count || item?.attempts?.length || 0) + 1
  const date = Form.useWatch('attempt_date', form) as Dayjs | undefined
  const historical = Boolean(date?.isValid() && date.startOf('day').isBefore(dayjs().startOf('day')))
  const reworkers = useMemo(() => employees.filter((person) => person.is_active && ['REWORKER', 'BOTH'].includes(person.role)), [employees])

  useEffect(() => {
    if (!open) return
    form.resetFields()
    form.setFieldsValue({ attempt_date: dayjs(), status: 'PROCESSING' })
  }, [form, open, item?.id])

  const submit = async () => {
    if (!item) return
    const values = await form.validateFields()
    const attemptDate = values.attempt_date as Dayjs
    setSaving(true)
    try {
      await qualityWorkflowApi.createReworkAttempt({
        case_id: item.id,
        attempt_date: attemptDate.format('YYYY-MM-DD'),
        backfill_reason: values.backfill_reason || '',
        rework_employee_id: values.rework_employee_id || null,
        status: values.status || 'PROCESSING',
        notes: values.notes || '',
      })
      await onSaved()
      message.success(`已记录 ${item.case_no} · R${attemptNo}`)
      onClose()
    } catch (error) {
      message.error((error as Error).message || '保存返工轮次失败')
    } finally {
      setSaving(false)
    }
  }

  return <Drawer open={open} onClose={onClose} width={560} className="quality-return-rework-drawer" title={item ? `${item.case_no} · 登记 R${attemptNo}` : '登记返工轮次'} footer={<Space className="drawer-footer-actions"><Button onClick={onClose}>取消</Button><Button type="primary" loading={saving} onClick={() => void submit()}>保存本轮返工</Button></Space>}>
    <Alert type="info" showIcon message="每一轮都按原退货整批处理" description="整批件数和重量由系统自动带入，不再要求填写返工合格数量；R1、R2、R3会自动连续编号。" />
    {item?.source && <Descriptions className="quality-return-attempt-source" column={1} size="small" bordered>
      <Descriptions.Item label="原出货">{reworkCaseSourceTitle(item)}</Descriptions.Item>
      <Descriptions.Item label="产品 / 规格 / 材质">{item.source.product_name || '-'} · {item.source.specification || '-'} · {item.source.material || '-'}</Descriptions.Item>
      <Descriptions.Item label="退回整批">第 {item.source.shipment_unit_no || item.shipment_unit_no || '-'} 批 · {qualityNumber(item.source.pieces_per_batch)}件 · {qualityNumber(item.source.single_batch_net_weight_kg, 3)}kg</Descriptions.Item>
    </Descriptions>}
    <Form form={form} layout="vertical" style={{ marginTop: 16 }} requiredMark="optional">
      <Form.Item name="attempt_date" label="本轮日期" rules={[{ required: true, message: '请选择日期' }]}><DatePicker style={{ width: '100%' }} /></Form.Item>
      {historical && <Form.Item name="backfill_reason" label="补录原因" rules={[{ required: true, whitespace: true, message: '补录历史日期时请填写原因' }]}><Input /></Form.Item>}
      <Form.Item name="rework_employee_id" label="返工处理人（选填，可后补）"><Select allowClear showSearch optionFilterProp="label" placeholder={reworkers.length ? '选择返工处理人' : '尚未维护返工人员'} options={reworkers.map((person) => ({ value: person.id, label: `${person.employee_no} · ${person.name}` }))} /></Form.Item>
      <Form.Item name="status" label="本轮状态"><Select options={[{ value: 'PROCESSING', label: '返工中' }, { value: 'WAITING_REINSPECTION', label: '待复检' }, { value: 'COMPLETED', label: '本轮完成' }, { value: 'SCRAPPED', label: '报废' }]} /></Form.Item>
      <Form.Item name="notes" label="本轮说明（选填）"><Input.TextArea rows={3} maxLength={500} showCount /></Form.Item>
    </Form>
  </Drawer>
}

export function QualityReworkCaseDetailDrawer({ open, item, onClose, onAddAttempt, onSaved }: { open: boolean; item?: QualityReworkCase; onClose: () => void; onAddAttempt: (item: QualityReworkCase) => void; onSaved: () => Promise<void> }) {
  const source = item?.source
  const attempts = item?.attempts || []
  const [cancelling, setCancelling] = useState(false)
  const { message } = App.useApp()
  const cancelCase = async () => {
    if (!item) return
    setCancelling(true)
    try {
      await qualityWorkflowApi.updateReworkCase(item.id, { status: 'CANCELLED' })
      await onSaved()
      message.success('误登记已取消，原物理批号已释放，可重新选择。')
      onClose()
    } catch (error) {
      message.error((error as Error).message || '取消误登记失败')
    } finally {
      setCancelling(false)
    }
  }
  return <Drawer open={open} onClose={onClose} width={620} className="quality-return-rework-drawer" title={item ? `退货返工详情 · ${item.case_no}` : '退货返工详情'} footer={<Space className="drawer-footer-actions">{item?.origin === 'CUSTOMER_RETURN' && !['CANCELLED', 'SCRAPPED'].includes(item.status) && <Popconfirm title="确认取消这条误登记吗？" description="记录会保留审计历史，但原物理批号将释放并可重新登记。" okText="确认取消" cancelText="返回" onConfirm={() => void cancelCase()}><Button danger loading={cancelling}>取消误登记</Button></Popconfirm>}<Button onClick={onClose}>关闭</Button>{item?.origin === 'CUSTOMER_RETURN' && !['CANCELLED', 'SCRAPPED'].includes(item.status) && <Button type="primary" onClick={() => { onClose(); onAddAttempt(item) }}>登记下一轮返工</Button>}</Space>}>
    {item && <>
      <Descriptions column={1} size="small" bordered>
        <Descriptions.Item label="来源">{item.origin === 'CUSTOMER_RETURN' ? '客户整批退货' : '内部返工'}</Descriptions.Item>
        <Descriptions.Item label="原出货">{reworkCaseSourceTitle(item)}</Descriptions.Item>
        {source && <>
          <Descriptions.Item label="出货日期">{formatQualityDate(source.shipment_date)}</Descriptions.Item>
          <Descriptions.Item label="产品">{source.product_name || '-'}</Descriptions.Item>
          <Descriptions.Item label="规格 / 材质">{source.specification || '-'} · {source.material || '-'}</Descriptions.Item>
          <Descriptions.Item label="退回整批">物理批号 {source.shipment_unit_no || item.shipment_unit_no || '-'} · 本组共{source.total_batches || '-'}批 · {qualityNumber(source.pieces_per_batch)}件 · {qualityNumber(source.single_batch_net_weight_kg, 3)}kg</Descriptions.Item>
          <Descriptions.Item label="原责任品检员">{sourceInspectors(source)}</Descriptions.Item>
        </>}
        <Descriptions.Item label="登记日期">{formatQualityDate(item.opened_on)}</Descriptions.Item>
        <Descriptions.Item label="原因">{item.reason || '未填写'}</Descriptions.Item>
        <Descriptions.Item label="备注">{item.notes || '-'}</Descriptions.Item>
      </Descriptions>
      <Typography.Title level={5} style={{ marginTop: 20 }}>返工轮次</Typography.Title>
      {attempts.length ? <Timeline items={attempts.map((attempt, index) => ({
        color: attempt.status === 'COMPLETED' ? 'green' : attempt.status === 'SCRAPPED' ? 'red' : 'blue',
        label: `R${attempt.attempt_no || index + 1}`,
        children: <Card size="small"><Space wrap><strong>{formatQualityDate(attempt.attempt_date)}</strong><Tag>{attempt.status}</Tag></Space><div>整批投入：{qualityNumber(attempt.input_quantity)}件 / {qualityNumber(attempt.input_weight_kg, 3)}kg</div><Typography.Text type="secondary">{attempt.notes || '未填写说明'}</Typography.Text></Card>,
      }))} /> : <Empty description="尚未登记返工轮次，可直接登记 R1" />}
    </>}
  </Drawer>
}
