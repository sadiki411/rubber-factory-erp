import { Alert, Button, Card, List, Select, Space, Tag, Typography } from 'antd'
import { useMutation } from '@tanstack/react-query'
import { useEffect } from 'react'
import { businessImportApi } from '../api/client'
import type { ImportMoldCheck as MoldCheck } from '../types'

interface Props {
  check?: MoldCheck | null
  token: string
  disabled?: boolean
  onChange?: (check: MoldCheck | null | undefined) => void
  onBusyChange?: (busy: boolean) => void
}

const SOURCE_TEXT: Record<string, string> = { MANUAL: '本次人工选择，导入成功后记住', REMEMBERED: '沿用已保存关联', CUSTOMER_MATCH: '客户模具号精确匹配', UNLINKED: '尚未关联' }
const STATUS_TEXT: Record<string, string> = { IN_STOCK: '在库', ON_MACHINE: '在厂 · 上机占用', CUSTOMER_RETURNED: '客户收回', NO_ASSET: '未找到实物档案', UNLINKED: '待关联', MODEL_INACTIVE: '型号停用', MODEL_MISSING: '型号不存在', UNRESOLVED_PRODUCT: '产品身份待确认' }

export function ImportMoldCheck({ check, token, disabled, onChange, onBusyChange }: Props) {
  const mutation = useMutation({
    throwOnError: false,
    mutationFn: (selection?: { binding_key: string; mold_model_id: number | null }) => businessImportApi.checkMolds(token, selection),
    onSuccess: (result) => onChange?.(result.mold_check),
  })
  useEffect(() => { onBusyChange?.(mutation.isPending) }, [mutation.isPending, onBusyChange])
  useEffect(() => () => { onBusyChange?.(false) }, [onBusyChange])
  if (!check) return null
  const locked = disabled || mutation.isPending
  return <Card title="订单模具在厂核对" size="small" className="import-mold-check">
    <Space orientation="vertical" size={12} style={{ width: '100%', minWidth: 0 }}>
      <Alert showIcon type={check.alert_count ? 'warning' : 'success'} title={check.alert_count ? `${check.rows.length} 个产品中有 ${check.alert_count} 项需核对，仍可继续导入` : '关联模具均在库或上机占用'}
        description="首次可搜索并选择模具型号，导入成功后记住；以后自动沿用，每次重新读取实物状态。上机算在厂占用，台账未找到不等于实物确定不在厂。提醒不强制要求关联，不会移动模具或自动新建模具。" />
      {onChange && <Button disabled={locked} loading={mutation.isPending} onClick={() => mutation.mutate(undefined)}>重新核对模具状态</Button>}
      {mutation.isError && <Alert type="error" showIcon title="模具核对未保存" description={`${mutation.error.message}；原预检与已保存关联保留，可重试，也可继续导入。`} />}
      <List dataSource={check.rows} pagination={check.rows.length > 5 ? { pageSize: 5, size: 'small' } : false} renderItem={(row) => <List.Item key={row.binding_key} style={{ display: 'block', overflowWrap: 'anywhere' }}>
        <Space wrap><Typography.Text strong>{row.specification} / {row.material}</Typography.Text><Tag color={row.needs_attention ? 'warning' : 'success'}>{STATUS_TEXT[row.status] || row.status}</Tag></Space>
        <div>{row.orders.map((order) => `订单 ${order.order_no || '未填写'} / 项次 ${order.item_no || '未填写'}`).join('；')}</div>
        <div>客户原表模具号：{row.customer_mold_no || '未提供'}</div>
        <div>关联型号：{row.mold_model_code || '未关联'} · {SOURCE_TEXT[row.selection_source]}</div>
        {onChange && row.can_bind && <div style={{ marginBlock: 8 }}>
          <Select aria-label={`关联模具型号 ${row.specification} ${row.material}`} showSearch allowClear virtual={false} optionFilterProp="label" value={row.mold_model_id ?? undefined}
            placeholder="搜索模具型号或名称；未找到可继续导入" style={{ width: '100%', minWidth: 0 }} disabled={locked}
            classNames={{ popup: { root: 'import-mold-select-popup' } }}
            notFoundContent="未找到匹配型号，可暂不关联继续导入"
            options={(check.model_options || []).map((model) => ({ value: model.id, disabled: !model.is_active,
              label: `${model.code} · ${model.product_name || '未填写名称'} · 在库${model.in_stock} / 上机${model.on_machine} / 收回${model.returned}${model.is_active ? '' : '（已停用）'}` }))}
            onChange={(value: number | undefined) => mutation.mutate({ binding_key: row.binding_key, mold_model_id: value ?? null })} />
          <Typography.Text type="secondary">清除只撤销本次选择，已保存关联请在产品规格资料修改。</Typography.Text>
        </div>}
        <Typography.Paragraph style={{ marginBottom: 4 }}>{row.message}</Typography.Paragraph>
        {row.notices.map((notice) => <div key={notice}><Typography.Text type="warning">{notice}</Typography.Text></div>)}
        {row.assets.map((asset) => <div key={asset.id}>{asset.asset_code} · {asset.status_display} · {asset.location}</div>)}
      </List.Item>} />
      {!onChange && <Typography.Text type="secondary">以上为导入时的核对快照，后续模具移动不会改写此历史。</Typography.Text>}
    </Space>
  </Card>
}
