import { EditOutlined, ExportOutlined, HomeOutlined, MoreOutlined, PlusOutlined, SearchOutlined, SwapOutlined, ToolOutlined } from '@ant-design/icons'
import { Button, Card, Dropdown, Empty, Grid, Input, List, Select, Table, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { moldApi, toList } from '../api/client'
import { MoldFormDrawer } from '../components/MoldFormDrawer'
import { OperationDrawer, type MoldAction } from '../components/OperationDrawer'
import { PageTitle } from '../components/PageTitle'
import { StatusTag } from '../components/StatusTag'
import type { MoldAsset, MoldStatus } from '../types'
import { moldCode, moldLocation, moldModelOf } from '../types'

function actionItems(mold: MoldAsset) {
  const items: { key: MoldAction | 'edit'; label: string; icon: ReactNode }[] = []
  if (mold.status === 'IN_STOCK') items.push({ key: 'move', label: '库内移位', icon: <SwapOutlined /> })
  else items.push({ key: 'putaway', label: '归位入库', icon: <HomeOutlined /> })
  if (mold.status !== 'ON_MACHINE') items.push({ key: 'load-machine', label: '安排上机', icon: <ToolOutlined /> })
  if (mold.status !== 'OUTSOURCED') items.push({ key: 'send-out', label: '客户收回', icon: <ExportOutlined /> })
  items.push({ key: 'edit', label: '编辑资料', icon: <EditOutlined /> })
  return items
}

export function MoldsPage() {
  const navigate = useNavigate()
  const screens = Grid.useBreakpoint()
  const mobile = !screens.md
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<MoldStatus | ''>('')
  const [operation, setOperation] = useState<{ mold: MoldAsset; action: MoldAction }>()
  const [editing, setEditing] = useState<MoldAsset | undefined>()
  const [formOpen, setFormOpen] = useState(false)
  const moldsQuery = useQuery({
    queryKey: ['molds', { query, status }],
    queryFn: async () => toList(await moldApi.list({ q: query, status, page_size: 500 })),
  })

  const onMenu = (mold: MoldAsset, key: string) => {
    if (key === 'edit') {
      setEditing(mold)
      setFormOpen(true)
    } else setOperation({ mold, action: key as MoldAction })
  }

  const columns = useMemo<TableColumnsType<MoldAsset>>(() => [
    {
      title: '模具编号', dataIndex: 'asset_code', key: 'asset_code', fixed: 'left', width: 145,
      render: (_, record) => <Button type="link" className="table-primary-link" onClick={() => navigate(`/molds/${record.id}`)}>{moldCode(record)}</Button>,
    },
    { title: '型号', key: 'model', width: 150, render: (_, record) => moldModelOf(record)?.code || '-' },
    { title: '产品名称', key: 'product', width: 180, render: (_, record) => moldModelOf(record)?.product_name || moldModelOf(record)?.name || '-' },
    { title: '状态', dataIndex: 'status', key: 'status', width: 120, render: (value: MoldStatus) => <StatusTag status={value} /> },
    { title: '当前位置 / 去向', key: 'location', width: 220, render: (_, record) => <strong>{moldLocation(record)}</strong> },
    { title: '更新时间', dataIndex: 'status_changed_at', key: 'updated', width: 170, render: (value) => value ? new Date(value).toLocaleString('zh-CN') : '-' },
    {
      title: '操作', key: 'actions', fixed: 'right', width: 80,
      render: (_, record) => <Dropdown trigger={['click']} menu={{ items: actionItems(record), onClick: ({ key }) => onMenu(record, key) }}><Button type="text" icon={<MoreOutlined />} aria-label="更多操作" /></Dropdown>,
    },
  ], [navigate])

  return (
    <div className="page-container">
      <PageTitle
        title="模具台账"
        description="每副实物模具独立建档，同型号的多副模具也能分别追踪。"
        extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => { setEditing(undefined); setFormOpen(true) }}>新增模具</Button>}
      />
      <Card className="filter-card">
        <div className="filter-row">
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="搜索编号、型号或产品名称"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <Select
            value={status}
            onChange={setStatus}
            options={[
              { value: '', label: '全部状态' },
              { value: 'IN_STOCK', label: '在库' },
              { value: 'ON_MACHINE', label: '上机' },
              { value: 'OUTSOURCED', label: '客户收回' },
            ]}
          />
        </div>
      </Card>

      {mobile ? (
        <List
          className="mobile-record-list"
          loading={moldsQuery.isLoading}
          dataSource={moldsQuery.data || []}
          locale={{ emptyText: <Empty description="暂无模具数据" /> }}
          renderItem={(mold) => (
            <List.Item>
              <Card className="mobile-record-card" onClick={() => navigate(`/molds/${mold.id}`)}>
                <div className="record-card-heading"><Typography.Title level={4}>{moldCode(mold)}</Typography.Title><StatusTag status={mold.status} /></div>
                <Typography.Text>{moldModelOf(mold)?.code} · {moldModelOf(mold)?.product_name}</Typography.Text>
                <div className="record-location"><span>当前位置 / 去向</span><strong>{moldLocation(mold)}</strong></div>
                <Dropdown trigger={['click']} menu={{ items: actionItems(mold), onClick: ({ key, domEvent }) => { domEvent.stopPropagation(); onMenu(mold, key) } }}>
                  <Button block onClick={(event) => event.stopPropagation()}>模具操作 <MoreOutlined /></Button>
                </Dropdown>
              </Card>
            </List.Item>
          )}
        />
      ) : (
        <Card className="data-card" styles={{ body: { padding: 0 } }}>
          <Table rowKey="id" loading={moldsQuery.isLoading} dataSource={moldsQuery.data || []} columns={columns} scroll={{ x: 1100 }} pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (total) => `共 ${total} 副` }} />
        </Card>
      )}

      <MoldFormDrawer open={formOpen} mold={editing} onClose={() => setFormOpen(false)} />
      <OperationDrawer open={!!operation} mold={operation?.mold} action={operation?.action} onClose={() => setOperation(undefined)} />
    </div>
  )
}
