import { CalculatorOutlined, CheckCircleOutlined, EditOutlined } from '@ant-design/icons'
import { Alert, App, Button, Col, Descriptions, Form, Input, InputNumber, Row, Space, Tag, Typography } from 'antd'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { productionApi } from '../api/client'
import { formatProductionDate, productionSettlementInitialValues, productionSettlementRiskReasons, settlementExpectedQuantity, settlementQuantityMatches } from '../production'
import type { ProductionRun, ProductionSettlementInput } from '../types'

interface Props {
  run: ProductionRun
  onRunChange: (run: ProductionRun) => void
}

function numberText(value: number | string | null | undefined, digits = 2) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed.toLocaleString('zh-CN', { maximumFractionDigits: digits }) : '-'
}

export function ProductionSettlement({ run, onRunChange }: Props) {
  const [form] = Form.useForm<ProductionSettlementInput>()
  const { message, modal } = App.useApp()
  const queryClient = useQueryClient()
  const dirtyRef = useRef(false)
  const initializedRunIdRef = useRef<number | undefined>(undefined)
  const serverFingerprintRef = useRef('')
  const expectedQuantity = settlementExpectedQuantity(Number(run.produced_mold_count || 0), Number(run.cavities || 0))
  const goodQuantity = Form.useWatch('actual_good_quantity', form)
  const defectiveQuantity = Form.useWatch('actual_defective_quantity', form)
  const hasQuantityInput = goodQuantity !== undefined && goodQuantity !== null && defectiveQuantity !== undefined && defectiveQuantity !== null
  const quantityMatches = hasQuantityInput && settlementQuantityMatches(
    Number(run.produced_mold_count || 0),
    Number(run.cavities || 0),
    Number(goodQuantity),
    Number(defectiveQuantity),
  )
  const serverFingerprint = JSON.stringify([
    run.is_settled,
    run.settled_at,
    run.actual_good_quantity,
    run.actual_defective_quantity,
    run.total_material_kg,
    run.labor_cost,
    run.energy_cost,
    run.other_cost,
    run.settlement_notes,
    run.produced_mold_count,
    run.cavities,
  ])

  useEffect(() => {
    const runChanged = initializedRunIdRef.current !== run.id
    const serverChanged = serverFingerprintRef.current !== serverFingerprint
    if (runChanged || (serverChanged && !dirtyRef.current)) {
      form.resetFields()
      form.setFieldsValue(productionSettlementInitialValues(run, expectedQuantity))
      dirtyRef.current = false
      initializedRunIdRef.current = run.id
      serverFingerprintRef.current = serverFingerprint
    }
  }, [expectedQuantity, form, run, serverFingerprint])

  const mutation = useMutation({
    mutationFn: (payload: ProductionSettlementInput) => productionApi.settleRun(run.id, payload),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ['production'] })
      dirtyRef.current = false
      onRunChange(result)
      message.success(run.is_settled ? '完工结算已更新' : '完工结算已保存')
    },
    onError: (error: Error) => message.error(error.message),
  })

  const submit = async () => {
    const values = await form.validateFields()
    const payload = {
      actual_good_quantity: Number(values.actual_good_quantity),
      actual_defective_quantity: Number(values.actual_defective_quantity),
      total_material_kg: Number(values.total_material_kg),
      labor_cost: Number(values.labor_cost),
      energy_cost: Number(values.energy_cost),
      other_cost: Number(values.other_cost),
      settlement_notes: String(values.settlement_notes || '').trim(),
    }
    const riskReasons = productionSettlementRiskReasons({
      unitPrice: run.unit_price,
      materialUnitPrice: run.material_unit_price,
      totalMaterialKg: payload.total_material_kg,
      laborCost: payload.labor_cost,
      energyCost: payload.energy_cost,
      otherCost: payload.other_cost,
    })
    if (riskReasons.length) {
      modal.confirm({
        title: '请确认结算数据已填写完整',
        content: `系统检测到：${riskReasons.join('；')}。如数据属实可以继续，否则请返回补充，避免利润失真。`,
        okText: '数据属实，继续保存',
        cancelText: '返回检查',
        onOk: () => mutation.mutate(payload),
      })
      return
    }
    mutation.mutate(payload)
  }

  return (
    <section className="production-settlement">
      <div className="section-heading production-settlement-heading">
        <div>
          <Typography.Title level={4}>完工结算</Typography.Title>
          <Typography.Text type="secondary">订单结束后一次性登记实际产量、材料与各项成本，可随时修改。</Typography.Text>
        </div>
        <Tag icon={run.is_settled ? <CheckCircleOutlined /> : undefined} color={run.is_settled ? 'success' : 'warning'}>
          {run.is_settled ? '已结算' : '待结算'}
        </Tag>
      </div>

      {run.is_settled && (
        <div className="production-settlement-result">
          <Descriptions size="small" column={{ xs: 2, sm: 3 }}>
            <Descriptions.Item label="实际良品">{numberText(run.actual_good_quantity, 0)}</Descriptions.Item>
            <Descriptions.Item label="实际不良">{numberText(run.actual_defective_quantity, 0)}</Descriptions.Item>
            <Descriptions.Item label="总材料">{numberText(run.total_material_kg, 3)} kg</Descriptions.Item>
            <Descriptions.Item label="结算收入">¥{numberText(run.revenue)}</Descriptions.Item>
            <Descriptions.Item label="结算成本">¥{numberText(run.total_cost)}</Descriptions.Item>
            <Descriptions.Item label="结算利润"><strong className={Number(run.profit) < 0 ? 'negative-value' : 'profit-value'}>¥{numberText(run.profit)}</strong></Descriptions.Item>
            <Descriptions.Item label="结算人">{run.settled_by_name || '-'}</Descriptions.Item>
            <Descriptions.Item label="结算时间">{formatProductionDate(run.settled_at)}</Descriptions.Item>
          </Descriptions>
        </div>
      )}

      <Alert
        className="production-reconciliation"
        type={quantityMatches ? 'success' : 'warning'}
        showIcon
        title={`产量勾稽：良品 ${numberText(goodQuantity, 0)} + 不良 ${numberText(defectiveQuantity, 0)} = ${numberText(expectedQuantity, 0)} 件`}
        description={`累计模数 ${numberText(run.produced_mold_count, 0)} × 模具孔数 ${numberText(run.cavities, 0)} = ${numberText(expectedQuantity, 0)} 件${quantityMatches ? '，数量一致。' : '，请调整良品或不良数量。'}`}
      />

      <Form form={form} layout="vertical" requiredMark="optional" onValuesChange={() => { dirtyRef.current = true }}>
        <Row gutter={12}>
          <Col xs={12} sm={8}>
            <Form.Item name="actual_good_quantity" label="实际良品数" rules={[{ required: true, message: '请输入实际良品数' }]}>
              <InputNumber min={0} precision={0} style={{ width: '100%' }} />
            </Form.Item>
          </Col>
          <Col xs={12} sm={8}>
            <Form.Item
              name="actual_defective_quantity"
              label="实际不良数"
              dependencies={['actual_good_quantity']}
              rules={[
                { required: true, message: '请输入实际不良数' },
                {
                  validator: (_, value) => {
                    const good = Number(form.getFieldValue('actual_good_quantity'))
                    const defective = Number(value)
                    return settlementQuantityMatches(Number(run.produced_mold_count || 0), Number(run.cavities || 0), good, defective)
                      ? Promise.resolve()
                      : Promise.reject(new Error('良品数＋不良数必须等于累计模数×模具孔数'))
                  },
                },
              ]}
            >
              <InputNumber min={0} precision={0} style={{ width: '100%' }} />
            </Form.Item>
          </Col>
          <Col xs={12} sm={8}>
            <Form.Item name="total_material_kg" label="总材料用量(kg)" rules={[{ required: true, message: '请输入总材料用量' }]}>
              <InputNumber min={0} precision={3} style={{ width: '100%' }} />
            </Form.Item>
          </Col>
          <Col xs={12} sm={8}>
            <Form.Item name="labor_cost" label="人工成本" rules={[{ required: true, message: '请输入人工成本' }]}>
              <InputNumber min={0} precision={2} prefix="¥" style={{ width: '100%' }} />
            </Form.Item>
          </Col>
          <Col xs={12} sm={8}>
            <Form.Item name="energy_cost" label="能耗成本" rules={[{ required: true, message: '请输入能耗成本' }]}>
              <InputNumber min={0} precision={2} prefix="¥" style={{ width: '100%' }} />
            </Form.Item>
          </Col>
          <Col xs={12} sm={8}>
            <Form.Item name="other_cost" label="其他成本" rules={[{ required: true, message: '请输入其他成本' }]}>
              <InputNumber min={0} precision={2} prefix="¥" style={{ width: '100%' }} />
            </Form.Item>
          </Col>
        </Row>
        <Form.Item name="settlement_notes" label="结算备注">
          <Input.TextArea rows={2} maxLength={500} showCount placeholder="可记录损耗、异常成本或结算说明" />
        </Form.Item>
        <Space>
          <Button type="primary" icon={run.is_settled ? <EditOutlined /> : <CalculatorOutlined />} loading={mutation.isPending} onClick={() => void submit()}>
            {run.is_settled ? '更新结算' : '保存结算'}
          </Button>
          <Typography.Text type="secondary">收入、总成本和利润由系统按订单单价自动计算。</Typography.Text>
        </Space>
      </Form>
    </section>
  )
}
