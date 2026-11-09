import { EditOutlined, FileExcelOutlined, PlusOutlined, SearchOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Descriptions, Drawer, Empty, Grid, Image, Input, List, Select, Space, Table, Tag, Typography } from 'antd'
import type { TableColumnsType } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { productSpecificationApi, toList } from '../api/client'
import { BusinessImportDrawer } from '../components/BusinessImportDrawer'
import { PageTitle } from '../components/PageTitle'
import { ProductSpecificationDrawer } from '../components/ProductSpecificationDrawer'
import { MaterialLayoutSummary } from '../components/MaterialLayoutSummary'
import type { ProductSpecification } from '../types'
import { useReadOnly } from '../access'

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
  const readOnly = useReadOnly()
  const [viewing, setViewing] = useState<ProductSpecification>()
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
    { title: '照片', key: 'image', width: 76, fixed: 'left', render: (_, row) => row.image ? <Image src={row.image} alt={`${productSpecificationTitle(row)}产品照片`} width={48} height={48} style={{ objectFit: 'cover', borderRadius: 6 }} preview /> : <Typography.Text type="secondary">无</Typography.Text> },
    { title: '产品名称', dataIndex: 'product_name', fixed: 'left', width: 190, render: (_, row) => <Button type="link" className="table-primary-link" onClick={() => setViewing(row)}>{productSpecificationTitle(row)}</Button> },
    { title: '客户产品编号', dataIndex: 'customer_product_no', fixed: 'left', width: 155, render: exactText },
    { title: '规格', dataIndex: 'specification', width: 180, render: exactText },
    { title: '材质 / 胶料', dataIndex: 'material', width: 140, render: exactText },
    { title: '最新成品单重', key: 'unit_weight', width: 135, render: (_, row) => row.latest_unit_weight_g == null ? '-' : `${row.latest_unit_weight_g} g/件` },
    { title: '导入料长 / 料重', key: 'material', width: 190, render: (_, row) => <span>{exactText(row.material_length)}<br /><Typography.Text type="secondary">{exactText(row.cut_weight)}</Typography.Text></span> },
    { title: '实际切料长 / 料重', key: 'actual_material', width: 190, render: (_, row) => <span>{exactText(row.actual_material_length)}<br /><Typography.Text type="secondary">{exactText(row.actual_cut_weight)}</Typography.Text></span> },
    { title: '一模排料参数（g / 条）', key: 'material_layout', width: 290, render: (_, row) => <MaterialLayoutSummary record={row} /> },
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
              <Card className="mobile-record-card business-mobile-card" role="button" tabIndex={0} onClick={() => setViewing(record)} onKeyDown={(event) => { if (event.key === 'Enter') setViewing(record) }}>
                <div className="record-card-heading">{record.image ? <Image src={record.image} alt={`${productSpecificationTitle(record)}产品照片`} width={56} height={56} style={{ objectFit: 'cover', borderRadius: 6 }} preview /> : <div className="product-specification-no-image">暂无照片</div>}<div><Typography.Title level={4}>{productSpecificationTitle(record)}</Typography.Title><Tag color={record.is_active ? 'success' : 'default'}>{record.is_active ? '启用' : '停用'}</Tag></div></div>
                <Typography.Text>{exactText(record.customer_product_no)} · {exactText(record.specification)}</Typography.Text>
                <Typography.Text type="secondary">材质 {exactText(record.material)} · 模具型号 {moldModelText(record)}</Typography.Text>
                <div className="business-mobile-grid">
                  <span><small>一次硫化</small><b>{exactText(record.primary_curing)}</b></span>
                  <span><small>二烤参数</small><b>{exactText(record.secondary_curing)}</b></span>
                  <span><small>有效 / 总孔数</small><b>{exactText(record.effective_cavities)} / {exactText(record.total_cavities)}</b></span>
                  <span><small>最新成品单重</small><b>{record.latest_unit_weight_g == null ? '-' : `${record.latest_unit_weight_g} g/件`}</b></span>
                  <span><small>实际切料长 / 料重</small><b>{exactText(record.actual_material_length)} / {exactText(record.actual_cut_weight)}</b></span>
                  <span><small>导入料长 / 料重</small><b>{exactText(record.material_length)} / {exactText(record.cut_weight)}</b></span>
                  <span><small>模具资料</small><b>{moldDetailsText(record)}</b></span>
                </div>
                <MaterialLayoutSummary record={record} />
                <Button block icon={<EditOutlined />} onClick={(event) => { event.stopPropagation(); openForm(record) }}>编辑资料</Button>
              </Card>
            </List.Item>
          )}
        />
      ) : (
        <Card className="data-card" styles={{ body: { padding: 0 } }}>
          <Table rowKey="id" loading={specificationsQuery.isLoading} dataSource={specificationsQuery.data || []} columns={columns} scroll={{ x: 2465 }} pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (total) => `共 ${total} 条` }} />
        </Card>
      )}

      <Drawer className="product-specification-view-drawer" open={!!viewing} onClose={() => setViewing(undefined)} size={600} title={viewing ? `${productSpecificationTitle(viewing)} · 产品资料` : '产品资料'} extra={<Button disabled={readOnly} icon={<EditOutlined />} onClick={() => { openForm(viewing); setViewing(undefined) }}>编辑资料</Button>}>
        {viewing && <>
          {viewing.image && <Image src={viewing.image} alt="产品照片" width={160} />}
          <MaterialLayoutSummary record={viewing} />
          <Descriptions column={1} bordered size="small" items={[
            { key: 'identity', label: '客户产品编号 / 规格 / 材质', children: `${exactText(viewing.customer_product_no)} / ${exactText(viewing.specification)} / ${exactText(viewing.material)}` },
            { key: 'weight', label: '最新成品单重', children: viewing.latest_unit_weight_g == null ? '未登记' : `${viewing.latest_unit_weight_g} g/件` },
            { key: 'actual', label: '实际切料长 / 料重（人工维护）', children: `${exactText(viewing.actual_material_length)} / ${exactText(viewing.actual_cut_weight)}` },
            { key: 'import', label: '导入料长 / 料重', children: `${exactText(viewing.material_length)} / ${exactText(viewing.cut_weight)}` },
            { key: 'strip_count', label: '原记录条数（原文）', children: exactText(viewing.strip_count) },
            { key: 'primary', label: '一次硫化参考', children: exactText(viewing.primary_curing) },
            { key: 'secondary', label: '二烤参考（不代表本批已执行）', children: exactText(viewing.secondary_curing) },
            { key: 'cavities', label: '有效 / 总孔数', children: `${exactText(viewing.effective_cavities)} / ${exactText(viewing.total_cavities)}` },
            { key: 'mold', label: '模具型号 / 尺寸', children: `${moldModelText(viewing)} / ${moldDetailsText(viewing)}` },
            { key: 'mold_stock', label: '模具在库情况（资料记录）', children: exactText(viewing.mold_in_stock) },
            { key: 'notes', label: '备注', children: exactText(viewing.notes) },
            { key: 'active', label: '资料状态', children: viewing.is_active ? '启用' : '停用' },
          ]} />
        </>}
      </Drawer>
      <ProductSpecificationDrawer open={formOpen} specification={editing} onClose={() => setFormOpen(false)} />
      <BusinessImportDrawer open={importOpen} context="product-specifications" onClose={() => setImportOpen(false)} />
    </div>
  )
}
