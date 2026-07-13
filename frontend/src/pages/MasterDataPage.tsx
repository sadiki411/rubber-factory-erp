import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons'
import { App, Button, Card, Drawer, Form, Grid, Input, List, Popconfirm, Space, Switch, Table, Tabs, Tag } from 'antd'
import type { TableColumnsType } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { masterApi, toList } from '../api/client'
import { PageTitle } from '../components/PageTitle'
import type { Machine, MoldModel, Processor } from '../types'

type Resource = 'mold-models' | 'machines' | 'processors'
type RecordType = MoldModel | Machine | Processor

const config = {
  'mold-models': { title: '模具型号', createLabel: '新增型号' },
  machines: { title: '机台', createLabel: '新增机台' },
  processors: { title: '外加工方', createLabel: '新增加工方' },
} as const

function labelOf(record: RecordType, resource: Resource) {
  if (resource === 'mold-models') return (record as MoldModel).product_name
  return (record as Machine | Processor).name
}

function DataPanel({ resource }: { resource: Resource }) {
  const { message } = App.useApp()
  const screens = Grid.useBreakpoint()
  const mobile = !screens.md
  const queryClient = useQueryClient()
  const [form] = Form.useForm()
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<RecordType>()
  const api = masterApi<any>(resource)
  const query = useQuery({ queryKey: [resource], queryFn: async () => toList(await api.list()) })
  const saveMutation = useMutation({
    mutationFn: (values: Record<string, unknown>) => editing ? api.update(editing.id, values) : api.create(values),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: [resource] }); message.success('资料已保存'); setOpen(false) },
    onError: (error: Error) => message.error(error.message),
  })
  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.remove(id),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: [resource] }); message.success('资料已删除') },
    onError: (error: Error) => message.error(error.message),
  })

  const showForm = (record?: RecordType) => {
    setEditing(record)
    if (record) form.setFieldsValue(record)
    else { form.resetFields(); form.setFieldValue('active', true) }
    setOpen(true)
  }
  const columns: TableColumnsType<RecordType> = [
    { title: '编码', dataIndex: 'code', width: 150, render: (value) => <strong>{value}</strong> },
    { title: resource === 'mold-models' ? '产品名称' : '名称', key: 'name', render: (_, record) => labelOf(record, resource) },
    { title: '状态', dataIndex: 'active', width: 100, render: (value) => <Tag color={value === false ? 'default' : 'success'}>{value === false ? '停用' : '启用'}</Tag> },
    {
      title: '操作', width: 150, render: (_, record) => <Space>
        <Button type="link" icon={<EditOutlined />} onClick={() => showForm(record)}>编辑</Button>
        <Popconfirm title="确认删除此资料？" description="已被模具或历史记录使用的资料可能无法删除。" onConfirm={() => deleteMutation.mutate(record.id)}><Button type="link" danger icon={<DeleteOutlined />}>删除</Button></Popconfirm>
      </Space>,
    },
  ]

  return (
    <>
      <div className="panel-actions"><Button type="primary" icon={<PlusOutlined />} onClick={() => showForm()}>{config[resource].createLabel}</Button></div>
      {mobile ? (
        <List
          loading={query.isLoading}
          dataSource={query.data || []}
          renderItem={(record: RecordType) => (
            <List.Item actions={[
              <Button key="edit" type="link" onClick={() => showForm(record)}>编辑</Button>,
              <Popconfirm key="delete" title="确认删除此资料？" onConfirm={() => deleteMutation.mutate(record.id)}><Button type="link" danger>删除</Button></Popconfirm>,
            ]}>
              <List.Item.Meta title={<Space><strong>{record.code}</strong><Tag color={record.active === false ? 'default' : 'success'}>{record.active === false ? '停用' : '启用'}</Tag></Space>} description={labelOf(record, resource)} />
            </List.Item>
          )}
        />
      ) : <Table rowKey="id" loading={query.isLoading} dataSource={query.data || []} columns={columns} pagination={{ pageSize: 15 }} />}

      <Drawer open={open} onClose={() => setOpen(false)} title={`${editing ? '编辑' : '新增'}${config[resource].title}`} size={500} footer={<Space className="drawer-footer-actions"><Button onClick={() => setOpen(false)}>取消</Button><Button type="primary" loading={saveMutation.isPending} onClick={() => form.validateFields().then((values) => saveMutation.mutate(values))}>保存</Button></Space>}>
        <Form form={form} layout="vertical" requiredMark="optional">
          <Form.Item name="code" label="编码" rules={[{ required: true, message: '请输入编码' }]}><Input placeholder="必须唯一" disabled={!!editing} /></Form.Item>
          {resource === 'mold-models' ? (
            <Form.Item name="product_name" label="产品名称" rules={[{ required: true, message: '请输入产品名称' }]}><Input /></Form.Item>
          ) : <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}><Input /></Form.Item>}
          {resource === 'processors' && <><Form.Item name="contact" label="联系人"><Input /></Form.Item><Form.Item name="phone" label="联系电话"><Input /></Form.Item></>}
          {resource === 'mold-models' && <Form.Item name="description" label="说明"><Input.TextArea rows={4} maxLength={500} showCount /></Form.Item>}
          <Form.Item name="active" label="状态" valuePropName="checked"><Switch checkedChildren="启用" unCheckedChildren="停用" /></Form.Item>
        </Form>
      </Drawer>
    </>
  )
}

export function MasterDataPage() {
  return (
    <div className="page-container">
      <PageTitle title="标准资料" description="统一维护模具型号、机台和外加工方，状态操作只能从这些资料中选择。" />
      <Card className="data-card master-data-card">
        <Tabs items={[
          { key: 'mold-models', label: '模具型号', children: <DataPanel resource="mold-models" /> },
          { key: 'machines', label: '机台', children: <DataPanel resource="machines" /> },
          { key: 'processors', label: '外加工方', children: <DataPanel resource="processors" /> },
        ]} />
      </Card>
    </div>
  )
}
