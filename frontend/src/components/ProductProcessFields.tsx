import { Alert, AutoComplete, Card, Collapse, Form, Input, Radio, Select, Typography } from 'antd'
import { NUMERIC_PROCESS_FIELDS, TEXT_PROCESS_FIELDS, parameterMode, type ProcessParameter } from './productStandardFields'

const STATES = [{ value: 'RAW_UNCONFIRMED', label: '原文待核' }, { value: 'CONFIRMED', label: '已确认' }, { value: 'NOT_PROVIDED', label: '未提供' }, { value: 'NOT_APPLICABLE', label: '确认不适用' }]
export function ProductProcessFields() {
  const form = Form.useFormInstance()
  return <>
    <Typography.Paragraph type="secondary">照原表填写：数值条件默认填一个设定值，原表明确给范围时再选范围；洒油、脱模剂、排气等直接填写文字，不要求上下限。未规定的项目可留空。</Typography.Paragraph>
    <Card size="small" title="二烤要求" className="product-standard-field-card" data-testid="bake-fields">
      <Form.Item name={['bake', 'state']} label="是否需要二烤"><Select options={[{ value: 'UNKNOWN', label: '未规定 / 待确认' }, { value: 'APPLICABLE', label: '需要二烤' }, { value: 'NOT_APPLICABLE', label: '无需二烤' }]} /></Form.Item>
      <Form.Item noStyle shouldUpdate>{() => form.getFieldValue(['bake', 'state']) === 'NOT_APPLICABLE' && (form.getFieldValue('criteria') || []).some((c: { stage?: string; limit_mode?: string; requirement_state?: string }) => c.stage === 'BAKED_HARDNESS' && c.limit_mode !== 'NONE' && c.requirement_state !== 'NOT_APPLICABLE') ? <Alert type="warning" showIcon title="仍有烤后硬度要求，请核对是否矛盾" description="不会自动删除原有客户检验要求。请回到硬度与尺寸核对；未解决的冲突会阻止正式发卡。" /> : null}</Form.Item>
      <Form.Item noStyle shouldUpdate>{() => form.getFieldValue(['bake', 'state']) === 'APPLICABLE' ? <div className="flow-form-grid">{([['temperature', '二烤温度', '℃'], ['minutes', '二烤时间', 'min']] as const).map(([key, label, unit]) => <div key={key}>
        <Form.Item name={['bake', `_${key}_mode`]} label={`${label}填写方式`}><Radio.Group optionType="button" options={[{ value: 'FIXED', label: '指定值' }, { value: 'RANGE', label: '原表给了范围' }]} /></Form.Item>
        <Form.Item noStyle shouldUpdate>{() => form.getFieldValue(['bake', `_${key}_mode`]) === 'RANGE' ? <div className="flow-form-grid"><Form.Item name={['bake', `${key}_lower`]} label={`${label}下限（${unit}）`}><Input inputMode="decimal" /></Form.Item><Form.Item name={['bake', `${key}_upper`]} label={`${label}上限（${unit}）`}><Input inputMode="decimal" /></Form.Item></div> : <Form.Item name={['bake', `${key}_value`]} label={`${label}（${unit}）`}><Input inputMode="decimal" /></Form.Item>}</Form.Item>
      </div>)}</div> : <Typography.Paragraph type="secondary">不要求填写二烤温度、时间。未规定不等于无需二烤，确认不需要时请选择“无需二烤”。</Typography.Paragraph>}</Form.Item>
      <Form.Item name={['process_parameters', 'bake_reference', 'raw_text']} label="二烤原文 / 特殊说明（选填）"><Input.TextArea autoSize={{ minRows: 1, maxRows: 4 }} /></Form.Item>
    </Card>
    <Card size="small" title="操作要求（直接填写原表文字）" className="product-standard-field-card" data-testid="text-process-fields">
      <div className="flow-form-grid">{TEXT_PROCESS_FIELDS.map(([key, label, options]) => <Form.Item key={key} name={['process_parameters', key, 'raw_text']} label={label}>
        {key === 'experience' ? <Input.TextArea autoSize={{ minRows: 2, maxRows: 6 }} placeholder="按原表或已确认经验填写" /> : <AutoComplete options={options.map(value => ({ value }))} placeholder={key === 'oil' ? '如：不洒油、少量；也可直接输入' : '可选填，也可直接输入原表要求'} />}
      </Form.Item>)}</div>
    </Card>
    {NUMERIC_PROCESS_FIELDS.map(([key, label, unit]) => <Card key={key} size="small" title={label} className="product-standard-field-card" data-testid={`process-${key}`}>
      <Form.Item name={['process_parameters', key, '_mode']} label={`${label}填写方式`} initialValue="FIXED"><Radio.Group optionType="button" options={[{ value: 'FIXED', label: '指定值' }, { value: 'RANGE', label: '原表给了范围' }]} /></Form.Item>
      <div className="flow-form-grid"><Form.Item noStyle shouldUpdate>{() => parameterMode(form.getFieldValue(['process_parameters', key]) as ProcessParameter) === 'RANGE' ? <><Form.Item name={['process_parameters', key, 'lower']} label={`${label}下限`}><Input inputMode="decimal" /></Form.Item><Form.Item name={['process_parameters', key, 'upper']} label={`${label}上限`}><Input inputMode="decimal" /></Form.Item></> : <Form.Item name={['process_parameters', key, 'value']} label={`${label}指定值`}><Input inputMode="decimal" placeholder="未规定可留空" /></Form.Item>}</Form.Item>
        <Form.Item name={['process_parameters', key, 'unit']} label={`${label}单位`}><Input placeholder={unit} /></Form.Item>
      </div>
      <Collapse size="small" items={[{ key: 'basis', label: '原文、出处与确认状态（选填）', children: <div className="flow-form-grid"><Form.Item name={['process_parameters', key, 'raw_text']} label={`${label}原文`}><Input /></Form.Item><Form.Item name={['process_parameters', key, 'source']} label={`${label}出处 / 类别`}><Input /></Form.Item><Form.Item name={['process_parameters', key, 'state']} label={`${label}确认状态`}><Select allowClear options={STATES} /></Form.Item></div> }]} />
    </Card>)}
    <Collapse items={[{ key: 'text-basis', label: '操作要求的出处与确认状态（选填）', children: TEXT_PROCESS_FIELDS.map(([key, label]) => <div key={key} className="flow-form-grid"><Form.Item name={['process_parameters', key, 'state']} label={`${label}确认状态`}><Select allowClear options={STATES} /></Form.Item><Form.Item name={['process_parameters', key, 'source']} label={`${label}出处`}><Input /></Form.Item></div>) }]} />
  </>
}
