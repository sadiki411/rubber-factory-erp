import { EditOutlined, FileExcelOutlined, PlusOutlined, SearchOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Empty, Grid, Input, List, Select, Space, Table, Tag, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { productSpecificationApi, toList } from '../api/client'
import { BusinessImportDrawer } from '../components/BusinessImportDrawer'
import { PageTitle } from '../components/PageTitle'
import { ProductSpecificationDrawer } from '../components/ProductSpecificationDrawer'
import type { ProductSpecification } from '../types'

function exactText(value: unknown) {
  return value === null || value === undefined || value === '' ? '-' : String(value)
}

function productSpecificationTitle(record: ProductSpecification) {
  return record.product_name || record.customer_product_no || record.specification || '未命名产品'
}

function moldModelText(record: ProductSpecification) {
  return record.mold_model?.code || record.mold_no || '-'
}

function moldDetailsText(record: ProductSpecification) {
  return [
    record.mold_model?.product_name,
    record.mold_no && record.mold_no !== record.mold_model?.code ? `原表 ${record.mold_no}` : '',
    record.mold_size,
  ].filter(Boolean).join(' · ') || '-'
}

export function ProductSpecificationsPage() {
  const screens = Grid.useBreakpoint()
  const mobile = screens.md === false
  const [query, setQuery] = useState('')
  const [active, setActive] = useState<'' | 'active' | 'inactive'>('active')
  const [editing, setEditing] = useState<ProductSpecification>()
  const [formOpen, setFormOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const specificationsQuery = useQuery({
    queryKey: ['product-specifications', { query, active }],
    queryFn: async () => toList(await productSpecificationApi.list({
      q: query || undefined,
      active: active === '' ? undefined : active === 'active',
      page_size: 1000,
    })),
  })

  const openForm = (record?: ProductSpecification) => {
    setEditing(record)
    setFormOpen(true)
  }

  const columns: TableColumnsType<ProductSpecification> = [
    { title: '产品名称', dataIndex: 'product_name', fixed: 'left', width: 190, render: (_, row) => <Button type="link" className="table-primary-link" onClick={() => openForm(row)}>{productSpecificationTitle(row)}</Button> },
    { title: '客户产品编号', dataIndex: 'customer_product_no', fixed: 'left', width: 155, render: exactText },
    { title: '规格', dataIndex: 'specification', width: 180, render: exactText },
    { title: '材质 / 胶料', dataIndex: 'material', width: 140, render: exactText },
    { title: '胶料长度 / 裁重', key: 'material', width: 190, render: (_, row) => <span>{exactText(row.material_length)}<br /><Typography.Text type="secondary">{exactText(row.cut_weight)}</Typography.Text></span> },
    { title: '一次硫化', dataIndex: 'primary_curing', width: 190, ellipsis: true, render: exactText },
    { title: '二烤参数', dataIndex: 'secondary_curing', width: 190, ellipsis: true, render: exactText },
    { title: '孔数', key: 'cavities', width: 120, render: (_, row) => `${exactText(row.effective_cavities)} / ${exactText(row.total_cavities)}` },
    { title: '模具型号 / 尺寸', key: 'mold', width: 230, render: (_, row) => <span>{moldModelText(row)}<br /><Typography.Text type="secondary">{moldDetailsText(row)}</Typography.Text></span> },
    { title: '状态', dataIndex: 'is_active', width: 90, render: (value) => <Tag color={value ? 'success' : 'default'}>{value ? '启用' : '停用'}</Tag> },
    { title: '操作', key: 'action', fixed: 'right', width: 80, render: (_, row) => <Button type="link" icon={<EditOutlined />} onClick={() => openForm(row)}>编辑</Button> },
  ]

  return (
    <div className="page-container product-specifications-page">
      <PageTitle
        title="产品规格资料"
        description="集中维护产品名称、规格、上机工艺、二烤参数和模具资料；导入的原始文本不会被强制改写。"
        extra={<Space wrap><Button icon={<FileExcelOutlined />} onClick={() => setImportOpen(true)}>导入产品规格</Button><Button type="primary" icon={<PlusOutlined />} onClick={() => openForm()}>新增产品规格</Button></Space>}
      />
      <Card className="filter-card">
        <div className="business-filter-row product-specification-filter-row">
          <Input allowClear prefix={<SearchOutlined />} placeholder="搜索产品名称、客户产品编号、规格、材质或模具编号" value={query} onChange={(event) => setQuery(event.target.value)} />
          <Select value={active} onChange={setActive} options={[{ value: 'active', label: '启用资料' }, { value: 'inactive', label: '已停用' }, { value: '', label: '全部状态' }]} />
        </div>
      </Card>

      {specificationsQuery.isError && <Alert className="business-page-alert" type="error" showIcon title="产品规格资料读取失败" description={(specificationsQuery.error as Error).message} />}
      {mobile ? (
        <List
          className="mobile-record-list business-mobile-list"
          loading={specificationsQuery.isLoading}
          dataSource={specificationsQuery.data || []}
          locale={{ emptyText: <Empty description="暂无产品规格资料" /> }}
          renderItem={(record) => (
            <List.Item>
              <Card className="mobile-record-card business-mobile-card" role="button" tabIndex={0} onClick={() => openForm(record)}>
                <div className="record-card-heading"><Typography.Title level={4}>{productSpecificationTitle(record)}</Typography.Title><Tag color={record.is_active ? 'success' : 'default'}>{record.is_active ? '启用' : '停用'}</Tag></div>
                <Typography.Text>{exactText(record.customer_product_no)} · {exactText(record.specification)}</Typography.Text>
                <Typography.Text type="secondary">材质 {exactText(record.material)} · 模具型号 {moldModelText(record)}</Typography.Text>
                <div className="business-mobile-grid">
                  <span><small>一次硫化</small><b>{exactText(record.primary_curing)}</b></span>
                  <span><small>二烤参数</small><b>{exactText(record.secondary_curing)}</b></span>
                  <span><small>有效 / 总孔数</small><b>{exactText(record.effective_cavities)} / {exactText(record.total_cavities)}</b></span>
                  <span><small>模具资料</small><b>{moldDetailsText(record)}</b></span>
                </div>
                <Button block icon={<EditOutlined />} onClick={(event) => { event.stopPropagation(); openForm(record) }}>编辑资料</Button>
              </Card>
            </List.Item>
          )}
        />
      ) : (
        <Card className="data-card" styles={{ body: { padding: 0 } }}>
          <Table rowKey="id" loading={specificationsQuery.isLoading} dataSource={specificationsQuery.data || []} columns={columns} scroll={{ x: 1765 }} pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (total) => `共 ${total} 条` }} />
        </Card>
      )}

      <ProductSpecificationDrawer open={formOpen} specification={editing} onClose={() => setFormOpen(false)} />
      <BusinessImportDrawer open={importOpen} context="product-specifications" onClose={() => setImportOpen(false)} />
    </div>
  )
}
