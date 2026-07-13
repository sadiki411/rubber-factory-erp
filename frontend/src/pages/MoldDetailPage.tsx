import { ArrowLeftOutlined, EditOutlined, ExportOutlined, HomeOutlined, SwapOutlined, ToolOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Col, Descriptions, Empty, Image, Row, Skeleton, Space, Timeline, Typography } from 'antd'
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'
import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { moldApi, toList } from '../api/client'
import { MoldFormDrawer } from '../components/MoldFormDrawer'
import { OperationDrawer, type MoldAction } from '../components/OperationDrawer'
import { PageTitle } from '../components/PageTitle'
import { StatusTag } from '../components/StatusTag'
import type { MoldMovement } from '../types'
import { moldCode, moldLocation, moldModelOf } from '../types'

const movementColor: Record<string, string> = {
  PUTAWAY: 'green', MOVE: 'blue', LOAD_MACHINE: 'blue', SEND_OUT: 'orange',
}

export function MoldDetailPage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const [action, setAction] = useState<MoldAction>()
  const [editing, setEditing] = useState(false)
  const moldQuery = useQuery({ queryKey: ['mold', id], queryFn: () => moldApi.detail(id), enabled: !!id })
  const historyQuery = useQuery({ queryKey: ['mold', id, 'history'], queryFn: async () => toList(await moldApi.history(id)), enabled: !!id })

  if (moldQuery.isLoading) return <div className="page-container"><Skeleton active /></div>
  if (moldQuery.isError || !moldQuery.data) return <div className="page-container"><Alert type="error" showIcon title="无法读取模具详情" description={(moldQuery.error as Error)?.message} /></div>
  const mold = moldQuery.data
  const model = moldModelOf(mold)
  const image = mold.main_image || mold.image

  const actions = mold.status === 'IN_STOCK'
    ? <><Button icon={<SwapOutlined />} onClick={() => setAction('move')}>移位</Button><Button icon={<ToolOutlined />} onClick={() => setAction('load-machine')}>上机</Button><Button icon={<ExportOutlined />} onClick={() => setAction('send-out')}>外出加工</Button></>
    : <><Button type="primary" icon={<HomeOutlined />} onClick={() => setAction('putaway')}>归位入库</Button>{mold.status !== 'ON_MACHINE' && <Button icon={<ToolOutlined />} onClick={() => setAction('load-machine')}>上机</Button>}{mold.status !== 'OUTSOURCED' && <Button icon={<ExportOutlined />} onClick={() => setAction('send-out')}>外出加工</Button>}</>

  return (
    <div className="page-container">
      <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate(-1)} className="back-button">返回</Button>
      <PageTitle title={moldCode(mold)} description={`${model?.code || '-'} · ${model?.product_name || model?.name || '-'}`} extra={<Button icon={<EditOutlined />} onClick={() => setEditing(true)}>编辑资料</Button>} />

      <Row gutter={[20, 20]}>
        <Col xs={24} lg={16}>
          <Card className="detail-card" title="当前状态">
            <div className="current-status-block">
              <StatusTag status={mold.status} />
              <Typography.Title level={3}>{moldLocation(mold)}</Typography.Title>
              <Typography.Text type="secondary">状态更新于 {mold.status_changed_at ? dayjs(mold.status_changed_at).format('YYYY-MM-DD HH:mm') : '-'}</Typography.Text>
            </div>
            <div className="detail-actions"><Space wrap>{actions}</Space></div>
          </Card>
          <Card className="detail-card" title="模具资料">
            <Descriptions column={{ xs: 1, sm: 2 }}>
              <Descriptions.Item label="模具编号">{moldCode(mold)}</Descriptions.Item>
              <Descriptions.Item label="型号">{model?.code || '-'}</Descriptions.Item>
              <Descriptions.Item label="产品名称">{model?.product_name || model?.name || '-'}</Descriptions.Item>
              <Descriptions.Item label="允许叠放下层">{mold.can_stack ? '是' : '否'}</Descriptions.Item>
              <Descriptions.Item label="备注" span={2}>{mold.note || '无'}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card className="detail-card" title="模具照片">
            {image ? <Image src={image} alt={`${moldCode(mold)}模具照片`} className="mold-image" /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无照片" />}
          </Card>
        </Col>
      </Row>

      <Card className="detail-card" title="操作历史">
        {historyQuery.isLoading ? <Skeleton active /> : historyQuery.data?.length ? (
          <Timeline items={historyQuery.data.map((item: MoldMovement) => ({
            color: movementColor[item.action] || 'gray',
            content: (
              <div className="history-entry">
                <div><strong>{item.action_display || item.action}</strong><Typography.Text type="secondary">{dayjs(item.created_at).format('YYYY-MM-DD HH:mm:ss')}</Typography.Text></div>
                <p>{item.from_location || item.from_status || '新建'} → {item.to_location || item.to_status}</p>
                {item.note && <Typography.Text type="secondary">备注：{item.note}</Typography.Text>}
              </div>
            ),
          }))} />
        ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无操作记录" />}
      </Card>

      <OperationDrawer open={!!action} mold={mold} action={action} onClose={() => setAction(undefined)} />
      <MoldFormDrawer open={editing} mold={mold} onClose={() => setEditing(false)} />
    </div>
  )
}
