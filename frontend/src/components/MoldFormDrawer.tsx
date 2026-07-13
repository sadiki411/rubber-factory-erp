import { App, Button, Drawer, Form, Input, Modal, Select, Space, Switch, Typography, Upload } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import type { UploadFile } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { ApiError, masterApi, moldApi, slotApi, toList } from '../api/client'
import type { Machine, MoldAsset, MoldStatus, RackSlot } from '../types'
import { moldCode, moldModelOf } from '../types'

interface Props {
  open: boolean
  mold?: MoldAsset
  initialSlot?: RackSlot
  onClose: () => void
  onSuccess?: (mold: MoldAsset) => void
}

export function MoldFormDrawer({ open, mold, initialSlot, onClose, onSuccess }: Props) {
  const [form] = Form.useForm()
  const [files, setFiles] = useState<UploadFile[]>([])
  const queryClient = useQueryClient()
  const { message } = App.useApp()
  const editing = !!mold
  const existingImage = mold?.main_image || mold?.image
  const initialStatus = Form.useWatch<MoldStatus>('initial_status', form)

  const slotsQuery = useQuery({
    queryKey: ['slots', 'available'],
    queryFn: async () => toList(await slotApi.list(true)),
    enabled: open && !editing && initialStatus === 'IN_STOCK' && !initialSlot,
  })
  const machinesQuery = useQuery({
    queryKey: ['machines'],
    queryFn: async () => toList(await masterApi<Machine>('machines').list()),
    enabled: open && !editing && initialStatus === 'ON_MACHINE',
  })

  useEffect(() => {
    if (!open) return
    form.resetFields()
    if (mold) {
      form.setFieldsValue({
        asset_code: moldCode(mold),
        model_code: moldModelOf(mold)?.code,
        product_name: moldModelOf(mold)?.product_name || moldModelOf(mold)?.name,
        note: mold.note,
        can_stack: mold.can_stack ?? false,
      })
      const image = mold.main_image || mold.image
      // Keep the controlled upload list in sync when a different mold is opened.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setFiles(image ? [{ uid: '-1', name: '模具照片', status: 'done', url: image }] : [])
    } else {
      form.resetFields()
      form.setFieldsValue({
        can_stack: false,
        initial_status: 'IN_STOCK',
        slot_id: initialSlot?.id,
      })
      setFiles([])
    }
  }, [form, initialSlot, mold, open])

  const mutation = useMutation({
    mutationFn: async ({ values, confirmWarnings }: { values: Record<string, any>; confirmWarnings: boolean }) => {
      const body = new FormData()
      Object.entries(values).forEach(([key, value]) => {
        if (value !== undefined && value !== null) body.append(key, typeof value === 'boolean' ? String(value) : value)
      })
      const original = files[0]?.originFileObj
      if (original) body.append('image', original)
      if (editing && existingImage && files.length === 0) body.append('remove_image', 'true')
      if (!editing) body.append('confirm_warnings', String(confirmWarnings))
      return editing ? moldApi.update(mold.id, body) : moldApi.create(body)
    },
    onSuccess: async (result) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['molds'] }),
        queryClient.invalidateQueries({ queryKey: ['mold'] }),
        queryClient.invalidateQueries({ queryKey: ['racks'] }),
        queryClient.invalidateQueries({ queryKey: ['slots'] }),
      ])
      message.success(editing ? '模具资料已保存' : '模具已建档')
      onSuccess?.(result)
      onClose()
    },
  })

  const submit = async (confirmWarnings = false) => {
    try {
      const values = await form.validateFields()
      await mutation.mutateAsync({ values, confirmWarnings })
    } catch (error) {
      if (!(error instanceof ApiError)) return
      const warnings = error.data?.warnings
      if (!editing && !confirmWarnings && error.status === 409 && Array.isArray(warnings) && warnings.length) {
        Modal.confirm({
          title: '需要确认叠放风险',
          content: <div>{warnings.map((warning: string) => <p key={warning}>{warning}</p>)}</div>,
          okText: '已检查，继续建档',
          cancelText: '返回检查',
          onOk: () => submit(true),
        })
      } else {
        message.error(error.message)
      }
    }
  }

  return (
    <Drawer
      open={open}
      onClose={onClose}
      size={520}
      title={editing ? '编辑模具资料' : initialSlot ? `在 ${initialSlot.display_code} 放入模具` : '新增模具'}
      footer={<Space className="drawer-footer-actions"><Button onClick={onClose}>取消</Button><Button type="primary" loading={mutation.isPending} onClick={() => submit()}>保存</Button></Space>}
    >
      <Form form={form} layout="vertical" requiredMark="optional">
        <Form.Item
          name="asset_code"
          label="模具编号"
          extra={editing ? '可直接修改；清空后系统会根据当前型号重新生成唯一编号' : '可留空，由系统根据型号自动生成唯一编号'}
          rules={[{ max: 100, message: '模具编号最多100个字符' }]}
        >
          <Input placeholder="可选，例如 MJ-001" />
        </Form.Item>
        <Form.Item name="model_code" label="模具型号" rules={[{ required: true, whitespace: true, message: '请输入模具型号' }, { max: 100 }]}>
          <Input placeholder="直接输入型号，例如 ABC-100" autoComplete="off" />
        </Form.Item>
        <Form.Item name="product_name" label="产品名称（可选）" extra="同一型号共用产品名称；清空后将恢复为型号名称" rules={[{ max: 200 }]}>
          <Input placeholder="例如 密封圈" autoComplete="off" />
        </Form.Item>
        {!editing && (
          <>
            <Form.Item name="initial_status" label="当前位置" rules={[{ required: true, message: '请选择当前位置' }]}>
              <Select
                disabled={!!initialSlot}
                options={[
                  { value: 'IN_STOCK', label: '在库' },
                  { value: 'ON_MACHINE', label: '上机' },
                  { value: 'OUTSOURCED', label: '客户收回' },
                ]}
              />
            </Form.Item>
            {initialStatus === 'IN_STOCK' && (initialSlot ? (
              <>
                <Form.Item name="slot_id" hidden preserve><Input /></Form.Item>
                <Form.Item label="在库位置"><Input value={initialSlot.display_code} disabled /></Form.Item>
              </>
            ) : (
              <Form.Item name="slot_id" preserve={false} label="在库位置" rules={[{ required: true, message: '请选择在库位置' }]}>
                <Select
                  showSearch
                  optionFilterProp="label"
                  loading={slotsQuery.isLoading}
                  placeholder="选择空闲库位"
                  options={(slotsQuery.data || []).map((item) => ({ value: item.id, label: item.display_code }))}
                />
              </Form.Item>
            ))}
            {initialStatus === 'ON_MACHINE' && (
              <Form.Item name="machine_id" preserve={false} label="具体机台" rules={[{ required: true, message: '请选择具体机台' }]}>
              <Select
                showSearch
                optionFilterProp="label"
                loading={machinesQuery.isLoading}
                placeholder="选择模具所在机台"
                options={(machinesQuery.data || []).filter((item) => item.active !== false).map((item) => ({ value: item.id, label: `${item.code} · ${item.name}` }))}
              />
              </Form.Item>
            )}
            {initialStatus === 'OUTSOURCED' && (
              <Typography.Text type="secondary">客户已将模具收回，不需要填写库位或去向。</Typography.Text>
            )}
          </>
        )}
        <Form.Item name="can_stack" label="允许作为叠放下层" valuePropName="checked">
          <Switch checkedChildren="允许" unCheckedChildren="不允许" />
        </Form.Item>
        <Form.Item label="主图">
          <Upload
            listType="picture-card"
            fileList={files}
            beforeUpload={() => false}
            accept="image/png,image/jpeg,image/webp"
            maxCount={1}
            onChange={({ fileList }) => setFiles(fileList)}
          >
            {files.length < 1 && <div><PlusOutlined /><div style={{ marginTop: 8 }}>选择照片</div></div>}
          </Upload>
        </Form.Item>
        <Form.Item name="note" label="备注">
          <Input.TextArea rows={4} maxLength={500} showCount />
        </Form.Item>
      </Form>
    </Drawer>
  )
}
