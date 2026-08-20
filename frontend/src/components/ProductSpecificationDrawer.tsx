import { Alert, App, Button, Col, Drawer, Form, Input, Row, Select, Space, Switch, Typography } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { masterApi, productSpecificationApi, toList } from '../api/client'
import type { MoldModel, ProductSpecification } from '../types'

interface Props {
  open: boolean
  specification?: ProductSpecification
  onClose: () => void
}

const moldModelApi = masterApi<MoldModel>('mold-models')

export function ProductSpecificationDrawer({ open, specification, onClose }: Props) {
  const [form] = Form.useForm<Record<string, unknown>>()
  const queryClient = useQueryClient()
  const { message } = App.useApp()
  const moldModelsQuery = useQuery({
    queryKey: ['mold-models', 'product-specification-options'],
    queryFn: async () => toList(await moldModelApi.list()),
    enabled: open,
  })

  useEffect(() => {
    if (!open) return
    form.resetFields()
    form.setFieldsValue(specification ? {
      ...specification,
      mold_model_id: specification.mold_model_id ?? specification.mold_model?.id,
    } : { is_active: true })
  }, [form, open, specification])

  const mutation = useMutation({
    mutationFn: (values: Record<string, unknown>) => {
      const body = {
        ...values,
        mold_model_id: typeof values.mold_model_id === 'number' ? values.mold_model_id : null,
      } as Partial<ProductSpecification>
      return specification
        ? productSpecificationApi.update(specification.id, body)
        : productSpecificationApi.create(body)
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['product-specifications'] }),
        queryClient.invalidateQueries({ queryKey: ['orders'] }),
        queryClient.invalidateQueries({ queryKey: ['production'] }),
      ])
      message.success(specification ? '产品规格资料已更新' : '产品规格资料已创建')
      onClose()
    },
    onError: (error: Error) => message.error(error.message),
  })

  const submit = async () => mutation.mutate(await form.validateFields())

  return (
    <Drawer
      className="business-data-drawer"
      open={open}
      onClose={onClose}
      size={760}
      title={specification ? `编辑产品规格 · ${specification.product_name || specification.customer_product_no || specification.specification || '未命名产品'}` : '新增产品规格资料'}
      footer={<Space className="drawer-footer-actions"><Button onClick={onClose}>取消</Button><Button type="primary" loading={mutation.isPending} onClick={() => void submit()}>保存</Button></Space>}
    >
      {specification?.source_sheet && (
        <Alert
          className="business-source-alert"
          type="info"
          showIcon
          title={`来源：${specification.source_sheet}${specification.source_row ? ` 第 ${specification.source_row} 行` : ''}`}
          description="以下字段均可在线修正；系统仍保留原始导入数据用于核对。"
        />
      )}
      {specification?.latest_unit_weight_g != null && (
        <Alert
          className="business-source-alert"
          type="success"
          showIcon
          title={`最近成品单重：${specification.latest_unit_weight_g} g/件`}
          description={specification.latest_unit_weight_measured_on ? `最后确认日期：${specification.latest_unit_weight_measured_on}；历史记录 ${specification.unit_weight_history_count || 0} 条。出货确认时会继续保留历史版本。` : '该单重来自已确认出货记录。'}
        />
      )}
      <Form form={form} layout="vertical" requiredMark="optional">
        <div className="business-form-section">基本资料</div>
        <Row gutter={14}>
          <Col xs={24} sm={12}><Form.Item name="product_name" label="产品名称" dependencies={['customer_product_no', 'specification']} rules={[{ validator: (_, value) => [value, form.getFieldValue('customer_product_no'), form.getFieldValue('specification')].some((item) => String(item || '').trim()) ? Promise.resolve() : Promise.reject(new Error('产品名称、客户产品编号、规格至少填写一项')) }]}><Input /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="customer_product_no" label="客户产品编号"><Input /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="specification" label="规格"><Input /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="material" label="材质 / 胶料"><Input /></Form.Item></Col>
        </Row>

        <div className="business-form-section">上机参数</div>
        <Alert className="business-form-hint" type="info" showIcon title="工艺参数按原始文本保存，可填写单位、温度、时间范围或备注，不会被强制转换成数字。" />
        <Row gutter={14}>
          <Col xs={24} sm={12}><Form.Item name="material_length" label="胶料长度 / 尺寸"><Input placeholder="保留原表写法" /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="cut_weight" label="裁料重量"><Input placeholder="例如 10g、0.010kg" /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="strip_count" label="条数 / 每批条数"><Input /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="primary_curing" label="一次硫化参数"><Input placeholder="温度、时间、压力等原始内容" /></Form.Item></Col>
        </Row>

        <div className="business-form-section">二烤与模具</div>
        <Row gutter={14}>
          <Col xs={24}><Form.Item name="secondary_curing" label="二次硫化 / 二烤参数"><Input.TextArea rows={3} maxLength={1000} showCount /></Form.Item></Col>
          <Col xs={12} sm={8}><Form.Item name="total_cavities" label="总孔数"><Input /></Form.Item></Col>
          <Col xs={12} sm={8}><Form.Item name="effective_cavities" label="有效孔数"><Input /></Form.Item></Col>
          <Col xs={24} sm={8}><Form.Item name="mold_in_stock" label="模具在库情况"><Input placeholder="保留原表描述" /></Form.Item></Col>
          <Col xs={24} sm={12}>
            <Form.Item name="mold_model_id" label="关联模具型号" extra="关联模具台账中的标准型号；同型号的多副实物模具会共用此关联。">
              <Select
                allowClear
                showSearch
                optionFilterProp="label"
                loading={moldModelsQuery.isLoading}
                placeholder="按型号或产品名称搜索"
                options={(moldModelsQuery.data || []).map((model) => ({
                  value: model.id,
                  label: `${model.code} · ${model.product_name || '未填写产品名称'}`,
                  disabled: !model.is_active && model.id !== specification?.mold_model?.id,
                }))}
              />
            </Form.Item>
          </Col>
          <Col xs={24} sm={12}><Form.Item name="mold_no" label="原表模具号" extra="保留客户或历史表格中的原始写法，用于核对。"><Input /></Form.Item></Col>
          <Col xs={24} sm={12}><Form.Item name="mold_size" label="模具尺寸"><Input /></Form.Item></Col>
        </Row>
        <Form.Item name="notes" label="备注"><Input.TextArea rows={4} maxLength={2000} showCount /></Form.Item>
        <Form.Item name="is_active" label="资料状态" valuePropName="checked">
          <Switch checkedChildren="启用" unCheckedChildren="停用" />
        </Form.Item>
        <Typography.Text type="secondary">停用后历史订单和生产记录仍保留该规格，新建记录时可优先隐藏。</Typography.Text>
      </Form>
    </Drawer>
  )
}
