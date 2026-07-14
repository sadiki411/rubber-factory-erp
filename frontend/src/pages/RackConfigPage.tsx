import { AppstoreAddOutlined, PlusOutlined, SaveOutlined } from '@ant-design/icons'
import { Alert, App, Button, Card, Checkbox, Col, Form, Input, InputNumber, Modal, Radio, Row, Select, Space, Switch, Typography } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { rackApi, toList } from '../api/client'
import { PageTitle } from '../components/PageTitle'
import { RackDiagram } from '../components/RackDiagram'
import type { RackConfigInput, RackLayout, RackSlot, RackSummary, RackZone } from '../types'

const defaults: RackConfigInput = {
  code: '', name: '', level_count: 6, zone_type: 'WHOLE', allowed_capacities: [2, 3, 4], default_capacity: 4, stack_levels: 1, default_stacking_enabled: false,
}

function previewLayout(values: RackConfigInput, rack?: RackSummary): RackLayout {
  const capacities = values.allowed_capacities?.length ? values.allowed_capacities : [values.default_capacity || 1]
  const capacity = capacities.includes(values.default_capacity) ? values.default_capacity : capacities[0]
  const stackingEnabled = values.default_stacking_enabled ?? values.stack_levels > 1
  const visibleStackLevels = stackingEnabled ? 2 : 1
  const zoneDefs = values.zone_type === 'SPLIT' ? [{ code: 'A', name: '左区' }, { code: 'B', name: '右区' }] : [{ code: 'A', name: '整层' }]
  return {
    rack: rack || { id: 0, code: values.code || 'J??', name: values.name || '新货架', configured: false },
    levels: Array.from({ length: Math.max(values.level_count || 1, 1) }, (_, levelIndex) => {
      const levelNo = levelIndex + 1
      return {
        id: levelNo,
        level_no: levelNo,
        zones: zoneDefs.map((zoneDef, zoneIndex): RackZone => ({
          id: levelNo * 10 + zoneIndex,
          ...zoneDef,
          current_capacity: capacity,
          allowed_capacities: capacities,
          supports_stacking: true,
          stacking_enabled: stackingEnabled,
          is_active: true,
          stack_levels: visibleStackLevels,
          slots: Array.from({ length: capacity * visibleStackLevels }, (_, slotIndex): RackSlot => {
            const position = Math.floor(slotIndex / visibleStackLevels) + 1
            const stack = slotIndex % visibleStackLevels + 1
            return {
              id: levelNo * 1000 + zoneIndex * 100 + slotIndex,
              display_code: `${values.code || 'J??'}-L${String(levelNo).padStart(2, '0')}-${zoneDef.code}-P${String(position).padStart(2, '0')}-S${stack}`,
              position_no: position,
              stack_level: stack,
              active: true,
              available: true,
            }
          }),
        })),
      }
    }),
  }
}

export function RackConfigPage() {
  const [form] = Form.useForm<RackConfigInput>()
  const [newForm] = Form.useForm<Pick<RackSummary, 'code' | 'name'>>()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<number>()
  const [newOpen, setNewOpen] = useState(false)
  const racksQuery = useQuery({ queryKey: ['racks'], queryFn: async () => toList(await rackApi.list()) })
  const selectedRack = racksQuery.data?.find((rack) => rack.id === selectedId)
  const watched = Form.useWatch([], form) as RackConfigInput | undefined
  const values = { ...defaults, ...(watched || {}) }
  const preview = previewLayout(values, selectedRack)

  const configureMutation = useMutation({
    mutationFn: (body: RackConfigInput) => rackApi.configure(selectedId!, body),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['racks'] }),
        queryClient.invalidateQueries({ queryKey: ['slots'] }),
      ])
      message.success('货架结构已保存')
    },
    onError: (error: Error) => message.error(error.message),
  })
  const createMutation = useMutation({
    mutationFn: (body: Pick<RackSummary, 'code' | 'name'>) => rackApi.create(body),
    onSuccess: async (rack) => {
      await queryClient.invalidateQueries({ queryKey: ['racks'] })
      setSelectedId(rack.id)
      form.setFieldsValue({ ...defaults, code: rack.code, name: rack.name })
      setNewOpen(false)
      message.success('货架已创建，请继续配置结构')
    },
    onError: (error: Error) => message.error(error.message),
  })

  const selectRack = (id: number) => {
    const rack = racksQuery.data?.find((item) => item.id === id)
    setSelectedId(id)
    form.setFieldsValue({ ...defaults, code: rack?.code || '', name: rack?.name || '' })
  }

  const save = async () => {
    if (!selectedId) return message.warning('请先选择或新建一个货架')
    if (selectedRack?.locked) return message.warning('此货架已投入使用，结构不能修改')
    try {
      configureMutation.mutate(await form.validateFields())
    } catch {
      // Field errors are displayed by the form.
    }
  }

  return (
    <div className="page-container">
      <PageTitle title="货架配置" description="为待配置货架设定层数、分区和每区容量；新货架的每个区域都支持按需开启叠放。" extra={<Button icon={<PlusOutlined />} onClick={() => { newForm.resetFields(); setNewOpen(true) }}>新建货架</Button>} />
      <Row gutter={[20, 20]}>
        <Col xs={24} xl={8}>
          <Card title="结构参数" className="config-card">
            <Form form={form} layout="vertical" initialValues={defaults} requiredMark="optional">
              <Form.Item label="选择货架">
                <Select
                  value={selectedId}
                  onChange={selectRack}
                  loading={racksQuery.isLoading}
                  placeholder="例如 J06 · 6号模具架"
                  options={(racksQuery.data || []).map((rack) => ({ value: rack.id, label: `${rack.code} · ${rack.name}${rack.locked ? '（已锁定）' : ''}` }))}
                />
              </Form.Item>
              {selectedRack?.locked && <Alert type="warning" showIcon title="该货架已有使用记录，结构已锁定" description="仍可在货架总览中切换空区域的容量模式。" />}
              <Form.Item name="code" label="货架编号"><Input disabled /></Form.Item>
              <Form.Item name="name" label="货架名称"><Input disabled /></Form.Item>
              <Form.Item name="level_count" label="层数" rules={[{ required: true }]}><InputNumber min={1} max={20} addonAfter="层" style={{ width: '100%' }} /></Form.Item>
              <Form.Item name="zone_type" label="每层分区"><Radio.Group optionType="button" buttonStyle="solid" options={[{ value: 'WHOLE', label: '整层一个区域' }, { value: 'SPLIT', label: '左右两个区域' }]} /></Form.Item>
              <Form.Item name="allowed_capacities" label="每区允许容量" rules={[{ required: true, message: '至少选择一种容量' }]}>
                <Checkbox.Group options={[1, 2, 3, 4, 5, 6].map((value) => ({ value, label: `${value} 位` }))} />
              </Form.Item>
              <Form.Item
                name="default_capacity"
                label="默认容量"
                dependencies={['allowed_capacities']}
                rules={[
                  { required: true, message: '请选择默认容量' },
                  ({ getFieldValue }) => ({
                    validator(_, value) {
                      return (getFieldValue('allowed_capacities') || []).includes(value)
                        ? Promise.resolve()
                        : Promise.reject(new Error('默认容量必须属于允许容量'))
                    },
                  }),
                ]}
              >
                <Select options={(values.allowed_capacities || []).map((value) => ({ value, label: `${value} 位` }))} />
              </Form.Item>
              <Form.Item name="default_stacking_enabled" label="默认显示叠放层" valuePropName="checked" extra="关闭时只显示S1下层；保存后仍可在货架总览中按区域开启S2上叠层。">
                <Switch checkedChildren="显示S2" unCheckedChildren="仅S1" />
              </Form.Item>
              <Button type="primary" block icon={<SaveOutlined />} loading={configureMutation.isPending} disabled={selectedRack?.locked} onClick={save}>保存货架结构</Button>
            </Form>
          </Card>
        </Col>
        <Col xs={24} xl={16}>
          <Card title="正面图实时预览" extra={<Typography.Text type="secondary">仅预览，保存后才会生效</Typography.Text>} className="config-preview-card">
            <RackDiagram layout={preview} />
          </Card>
        </Col>
      </Row>

      <Modal open={newOpen} title={<Space><AppstoreAddOutlined />新建货架</Space>} okText="创建并配置" cancelText="取消" confirmLoading={createMutation.isPending} onCancel={() => setNewOpen(false)} onOk={() => newForm.validateFields().then((values) => createMutation.mutate(values))}>
        <Form form={newForm} layout="vertical" requiredMark="optional">
          <Form.Item name="code" label="货架编号" rules={[{ required: true, message: '请输入货架编号' }, { pattern: /^[A-Za-z0-9_-]+$/, message: '只能使用字母、数字、横线和下划线' }]}><Input placeholder="例如 J08" /></Form.Item>
          <Form.Item name="name" label="货架名称" rules={[{ required: true, message: '请输入货架名称' }]}><Input placeholder="例如 8号模具架" /></Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
