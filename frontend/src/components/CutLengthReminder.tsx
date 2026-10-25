import { Alert, App, Button, Card, Checkbox, Descriptions, List, Space, Tag, Typography } from 'antd'
import { businessImportApi } from '../api/client'
import type { CutLengthCheck, CutLengthConfirmation } from '../types'

interface Props {
  check?: CutLengthCheck | null
  token: string
  acknowledged?: boolean
  onAcknowledgedChange?: (value: boolean) => void
  onRefresh?: () => void
  refreshing?: boolean
  disabled?: boolean
  confirmation?: CutLengthConfirmation | null
}

function cutLengthText(check: CutLengthCheck) {
  return ['翔邦料长核对（单位mm，差额=客户-实际；仅比较手填实际切料长）',
    ...check.rows.filter((row) => row.status !== 'IN_TOLERANCE').map((row) =>
      `${row.order_no} / 项次${row.item_no} / ${row.specification} / ${row.material}：客户${row.customer_length || '未提供'}，实际${row.actual_length || '未填写'}，差额${row.difference_mm ?? '无法比较'}。${row.message}`),
  ].join('\n')
}

export function CutLengthReminder({ check, token, acknowledged, onAcknowledgedChange, onRefresh, refreshing, disabled, confirmation }: Props) {
  const { message } = App.useApp()
  if (!check) return null
  const alerts = check.rows.filter((row) => row.status !== 'IN_TOLERANCE')
  return <Card title="客户料长核对" size="small" style={{ marginBlock: 16, minWidth: 0 }}>
    <Space direction="vertical" size={12} style={{ width: '100%', minWidth: 0 }}>
      <Alert showIcon type={alerts.length ? 'warning' : 'success'}
        title={alerts.length ? `${check.rows.length} 个订单项次中有 ${alerts.length} 项需核对` : `${check.rows.length} 个订单项次的料长差值均在±5mm以内`}
        description="只比较本次文件料长与手工填写的实际切料长，单位mm；差值超过±5mm才提醒。不比较旧客户值、重量或模具号。未填实际值也可知悉后导入，不会覆盖实际参数。可先在产品规格资料补录实际切料长，再回到这里重新核对。" />
      <Space wrap>
        {onRefresh && <Button loading={refreshing} disabled={disabled} onClick={onRefresh}>重新核对实际料长</Button>}
        {!!alerts.length && <>
          <Button onClick={async () => {
            try { await navigator.clipboard.writeText(cutLengthText(check)); message.success('料长提醒已复制') }
            catch { message.warning('复制不可用，请下载料长提醒清单') }
          }}>复制反馈清单</Button>
          <Button href={businessImportApi.cutLengthReportUrl(token)}>下载料长提醒清单</Button>
        </>}
      </Space>
      {!!alerts.length && <List dataSource={alerts} pagination={alerts.length > 5 ? { pageSize: 5, size: 'small' } : false}
        renderItem={(row) => <List.Item key={row.row_key} style={{ display: 'block', overflowWrap: 'anywhere' }}>
          <Space wrap><Typography.Text strong>{row.specification} / {row.material}</Typography.Text><Tag color={row.status === 'SHORTER' ? 'red' : 'orange'}>{row.status === 'SHORTER' ? '客户偏短' : row.status === 'LONGER' ? '客户偏长' : '待人工核对'}</Tag></Space>
          <div>订单 {row.order_no || '未填写'} · 项次 {row.item_no || '未填写'} · {row.sheet}/{row.row}</div>
          <Descriptions size="small" column={{ xs: 1, sm: 3 }} style={{ marginTop: 8 }}>
            <Descriptions.Item label="客户料长(mm)">{row.customer_length || '未提供'}</Descriptions.Item>
            <Descriptions.Item label="实际料长(mm)">{row.actual_length || '未填写'}</Descriptions.Item>
            <Descriptions.Item label="差额(mm)">{row.difference_mm ?? '无法比较'}</Descriptions.Item>
          </Descriptions>
          <Typography.Text type={row.status === 'SHORTER' ? 'danger' : 'warning'}>{row.message}</Typography.Text>
          {!!row.candidates.length && <div>候选档案：{row.candidates.map((item) => `#${item.id} 实际料长：${item.actual_material_length || '未填写'}`).join('；')}</div>}
        </List.Item>} />}
      {!!alerts.length && onAcknowledgedChange && <Checkbox checked={acknowledged} disabled={disabled} onChange={(event) => onAcknowledgedChange(event.target.checked)}>
        已知悉上述料长提醒，仍继续导入（不代表已补录实际值或客户已更正）
      </Checkbox>}
      {confirmation && <Typography.Text type="secondary">{confirmation.username} 已于 {new Date(confirmation.confirmed_at).toLocaleString()} 知悉提醒并导入。{confirmation.meaning}</Typography.Text>}
      {!onAcknowledgedChange && <Typography.Text type="secondary">以上为该批次核对时的快照，不随产品档案后续修改而重算。</Typography.Text>}
    </Space>
  </Card>
}
