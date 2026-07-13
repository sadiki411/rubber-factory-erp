import { App, Button, Drawer, Form, Input, Modal, Select, Space, Switch, Upload } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import type { UploadFile } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { ApiError, masterApi, moldApi, slotApi, toList } from '../api/client'
import type { MoldAsset, MoldModel } from '../types'
import { moldCode, moldModelOf } from '../types'

interface Props {
  open: boolean
  mold?: MoldAsset
  onClose: () => void
  onSuccess?: (mold: MoldAsset) => void
}

export function MoldFormDrawer({ open, mold, onClose, onSuccess }: Props) {
  const [form] = Form.useForm()
  const [files, setFiles] = useState<UploadFile[]>([])
  const queryClient = useQueryClient()
  const { message } = App.useApp()
  const editing = !!mold

  const modelsQuery = useQuery({
    queryKey: ['mold-models'],
    queryFn: async () => toList(await masterApi<MoldModel>('mold-models').list()),
    enabled: open,
  })
  const slotsQuery = useQuery({
    queryKey: ['slots', 'available'],
    queryFn: async () => toList(await slotApi.list(true)),
    enabled: open && !editing,
  })

  useEffect(() => {
    if (!open) return
    form.resetFields()
    if (mold) {
      form.setFieldsValue({
        asset_code: moldCode(mold),
        mold_model_id: moldModelOf(mold)?.id,
        note: mold.note,
        can_stack: mold.can_stack ?? false,
      })
      const image = mold.main_image || mold.image
      // Keep the controlled upload list in sync when a different mold is opened.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setFiles(image ? [{ uid: '-1', name: '模具照片', status: 'done', url: image }] : [])
    } else {
      form.resetFields()
      form.setFieldValue('can_stack', false)
      setFiles([])
    }
  }, [form, mold, open])

  const mutation = useMutation({
    mutationFn: async ({ values, confirmWarnings }: { values: Record<string, any>; confirmWarnings: boolean }) => {
      const body = new FormData()
      Object.entries(values).forEach(([key, value]) => {
        if (value !== undefined && value !== null) body.append(key, typeof value === 'boolean' ? String(value) : value)
      })
      const original = files[0]?.originFileObj
      if (original) body.append('image', original)
      if (!editing) body.append('confirm_warnings', String(confirmWarnings))
      return editing ? moldApi.update(mold.id, body) : moldApi.create(body)
    },
    onSuccess: async (result) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['molds'] }),
        queryClient.invalidateQueries({ queryKey: ['mold'] }),
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
      title={editing ? '编辑模具资料' : '新增模具'}
      footer={<Space className="drawer-footer-actions"><Button onClick={onClose}>取消</Button><Button type="primary" loading={mutation.isPending} onClick={() => submit()}>保存</Button></Space>}
    >
      <Form form={form} layout="vertical" requiredMark="optional">
        <Form.Item name="asset_code" label="模具编号" rules={[{ required: true, message: '请输入模具编号' }, { max: 60 }]}>
          <Input placeholder="例如 MJ-001" disabled={editing} />
        </Form.Item>
        <Form.Item name="mold_model_id" label="型号" rules={[{ required: true, message: '请选择模具型号' }]}>
          <Select
            showSearch
            optionFilterProp="label"
            loading={modelsQuery.isLoading}
            placeholder="选择型号"
            options={(modelsQuery.data || []).filter((item) => item.active !== false).map((item) => ({ value: item.id, label: `${item.code} · ${item.product_name}` }))}
          />
        </Form.Item>
        {!editing && (
          <Form.Item name="slot_id" label="初始库位" rules={[{ required: true, message: '请选择初始库位' }]}>
            <Select
              showSearch
              optionFilterProp="label"
              loading={slotsQuery.isLoading}
              placeholder="选择空闲库位"
              options={(slotsQuery.data || []).map((item) => ({ value: item.id, label: item.display_code }))}
            />
          </Form.Item>
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
