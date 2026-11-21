import { Alert, App, Button, Card, Checkbox, Form, Input, InputNumber, Modal, Select, Space, Tabs, Typography, Upload } from 'antd'
import { useState } from 'react'
import { flowApi, flowLimitText, flowRequestKey, finishFlowRequest, type PreparationContext, type PreparationStandard, type StandardImportPreview } from '../flowcards'
import { ProductProcessFields } from './ProductProcessFields'
import { inspectionItemLabel } from '../inspectionItemLabel'
import { bakeForForm, bakeForSave, bakeText, NUMERIC_PROCESS_FIELDS, processForForm, processForSave, processParameterText, TEXT_PROCESS_FIELDS } from './productStandardFields'

const STAGES = [{value:'MOLDING_HARDNESS',label:'成型后硬度'}, {value:'BAKED_HARDNESS',label:'二烤后硬度'}, {value:'DIMENSION',label:'尺寸'}, {value:'UPPER_TEMP',label:'上模接触实测'}, {value:'LOWER_TEMP',label:'下模接触实测'}]
const STATES = [{value:'RAW_UNCONFIRMED',label:'原文待核'}, {value:'CONFIRMED',label:'已确认'}, {value:'NOT_PROVIDED',label:'未提供'}, {value:'MEASURE_NO_LIMIT',label:'要测但无界限，不判合格'}, {value:'NOT_APPLICABLE',label:'确认不适用'}]
const emptyCriterion = (name:string, stage:string) => ({name,stage,lower:'',upper:'',unit:'',method:'',limit_mode:'RANGE',requirement_state:'NOT_PROVIDED',follow_package:true,critical:true,upper_inclusive:true,lower_inclusive:true})

export function ProductStandardEditor({data,open,close,changed,initialTab='quality'}: {data:PreparationContext;open:boolean;close:()=>void;changed:()=>void;initialTab?:string}) {
  const initial = data.standards.find(s=>s.id===data.auto_standard_id) || data.standards.find(s=>s.status==='ACTIVE'&&!s.applicable_order_id) || data.standards[0]
  const valuesFor = (s?:PreparationStandard) => ({customer_key:s?.customer_key||data.orders[0]?.customer||'',source_kind:s?.source_kind||'FACTORY_PROCESS',source:s?.source||'',scope:s?.scope||'',applicable_order_id:s?.applicable_order_id,
    whole_mold:s?.whole_mold||'UNKNOWN',applicability:s?.snapshot.applicability||{},appearance_ids:(s?.snapshot.appearance||data.appearance.slice(0,4)).map(a=>a.id),
    route:s?.snapshot.route||{confirmed:false,molding:true,patrol:true,deburr:true,inspection:true},packaging:s?.snapshot.packaging||{molds_per_card:100},
    hardness:s?.snapshot.hardness||{},bake:bakeForForm(s?.snapshot.bake),process_parameters:processForForm(s?.snapshot.process_parameters),import_source:s?.snapshot.import_source,
    criteria:s?.snapshot.criteria.map(c=>({...c,criterion_id:c.id,follow_package:c.follow_package!==false,critical:c.critical!==false,mode:'OVERRIDE'}))||[emptyCriterion('成型硬度','MOLDING_HARDNESS'),emptyCriterion('线径','DIMENSION')]})
  const [form] = Form.useForm()
  const [busy,setBusy] = useState(false)
  const [error,setError] = useState('')
  const [saved,setSaved] = useState<PreparationStandard>()
  const [imported,setImported] = useState<StandardImportPreview>()
  const [selected,setSelected] = useState<number>()
  const [identityChecked,setIdentityChecked] = useState(false)
  const [versionId,setVersionId] = useState(initial?.id)
  const [tab,setTab] = useState(initialTab)
  const {message} = App.useApp()
  const write = data.can_maintain_standard && data.write_enabled
  const markChanged = () => setSaved(undefined)
  const save = async (activate:boolean) => {
    setBusy(true);setError('')
    try {
      await form.validateFields()
      const values = form.getFieldsValue(true)
      const criteria = (values.criteria||[]).map((c:Record<string,unknown>)=>{
        const mode=c.requirement_state==='NOT_APPLICABLE'?'NONE':c.limit_mode==='RANGE'&&!String(c.lower??'').trim()&&!String(c.upper??'').trim()?'PENDING':c.limit_mode
        return {...c,...(mode==='UPPER'?{lower:''}:mode==='LOWER'?{upper:''}:{}),limit_mode:mode,override:Boolean(c.criterion_id),numeric:['UPPER','LOWER','RANGE'].includes(String(mode)),
          critical:c.follow_package!==false && c.requirement_state!=='NOT_APPLICABLE',follow_package:c.follow_package!==false}
      })
      const parameters = processForSave(values.process_parameters)
      const bake = bakeForSave(values.bake)
      const route = bake.state==='NOT_APPLICABLE' ? {...values.route,bake:false,baked_hardness:false} : values.route
      const body = {...values,bake,route,process_parameters:parameters,criteria,dimension_count:criteria.filter((c:{stage:string;follow_package:boolean;limit_mode:string})=>c.stage==='DIMENSION'&&c.follow_package&&c.limit_mode!=='NONE').length,product_id:data.product.id,mold_id:data.mold_id}
      const slot=`product-standard:${data.product.id}`
      const result = saved || await flowApi.saveStandard({...body,request_key:flowRequestKey(slot,body)})
      setSaved(result);finishFlowRequest(slot);changed()
      if(activate) {await flowApi.activateStandard(result.id);changed();message.success('已保存到本产品并生效，新订单引用；旧卡和旧账不变');close()}
      else message.success('已保存到本产品的标准草稿，尚未生效')
    } catch(e) {if(typeof e==='object'&&e&&'errorFields' in e){setError('请补全标出的必填项；来源依据及四项外观检查不能遗漏。');setTab('source')}else setError((e as Error).message||'保存失败，请重试；不会把读取失败当作保存成功。')}
    finally {setBusy(false)}
  }
  const candidate = selected===undefined?undefined:imported?.candidates[selected]
  const normalize = (v?:string) => (v||'').trim().toLocaleLowerCase().replace(/[×*＊]/g,'x').replace(/\s+/g,'')
  const identityConflicts = candidate ? ([['客户料号',candidate.customer_product_no,data.product.customer_product_no],['规格',candidate.specification,data.product.specification],['材质',candidate.material,data.product.material]] as const).filter(([,a,b])=>a&&b&&normalize(a)!==normalize(b)).map(([name])=>name) : []
  const importFile = async (file:File) => {
    setBusy(true);setError('')
    try {setImported(await flowApi.importStandards(file));setSelected(undefined);setIdentityChecked(false)}
    catch(e) {setError((e as Error).message)} finally {setBusy(false)}
    return false
  }
  const applyImport = () => {
    if(!candidate||!identityChecked||identityConflicts.length)return
    const exceptionOrder = candidate.order_no?data.orders.find(o=>o.order_no===candidate.order_no&&o.item_no===candidate.item_no):undefined
    if(candidate.order_no&&!exceptionOrder) {setError('此页没有唯一对应的订单项次。请从对应订单打开产品标准，不能把本单例外保存为通用标准。');return}
    const old = form.getFieldsValue(true)
    form.resetFields()
    form.setFieldsValue({...old,...candidate,bake:bakeForForm(candidate.bake),process_parameters:processForForm(candidate.process_parameters),source:`${imported?.filename} / ${candidate.source}`,applicable_order_id:exceptionOrder?.id,applicability:{customer_product_no:candidate.customer_product_no,drawing_revision:candidate.version||candidate.base_version},
      import_source:{...candidate.import_source,filename:imported?.filename},
      criteria:candidate.criteria.map(c=>({...c,mode:'OVERRIDE',provenance:{...c.provenance,file:imported?.filename}}))})
    markChanged();setTab('quality');message.info('已带入待核字段，尚未保存。请核对界限、量表、方法和是否随包测。')
  }
  return <Modal open={open} onCancel={close} title={`${data.product.specification} / ${data.product.material} · 产品标准`} width={1080} className="product-standard-editor" footer={<Space wrap><Button onClick={close}>关闭</Button><Button disabled={!write} loading={busy} onClick={()=>void save(false)}>保存草稿</Button><Button type="primary" disabled={!write} loading={busy} onClick={()=>void save(true)}>保存并生效</Button></Space>}>
    <Alert type="info" showIcon title="这里填写标准，不是本批实测值" description="硬度、尺寸和工艺仍保存在这条产品规格资料关联的检验要求与版本中。可手填，或导入客户Excel后核对。订单选择此产品即可引用；来源不明的数值留空待核，旧卡保存当时快照。" />
    {!write&&<Alert type="warning" title="当前只读，不能修改产品标准" />}
    {error&&<Alert type="error" title="未完成保存，请核对" description={error} />}
    <Space wrap style={{marginBlock:12}}><Typography.Text>查看已有版本：</Typography.Text><Select style={{minWidth:220,maxWidth:'100%'}} value={versionId} options={data.standards.map(s=>({value:s.id,label:`v${s.version} · ${{ACTIVE:'生效',DRAFT:'草稿',SUPERSEDED:'已替代',LEGACY:'旧记录待核'}[s.status]||s.status} · ${s.source}`}))} onChange={id=>{setVersionId(id);form.resetFields();form.setFieldsValue(valuesFor(data.standards.find(s=>s.id===id)));markChanged()}} /></Space>
    <Form form={form} layout="vertical" disabled={!write} initialValues={valuesFor(initial)} onValuesChange={markChanged}>
      <Tabs activeKey={tab} onChange={setTab} items={[
        {key:'quality',label:'硬度与尺寸',children:<>
          <Typography.Paragraph type="secondary">线径可填下限和上限。只有客户明确单边要求才选“仅上限/仅下限”。不从规格或材质编号推算硬度、公差。</Typography.Paragraph>
          <Form.List name="criteria">{(fields,{add,remove})=><>{fields.map(field=><Card key={field.key} size="small" className="product-standard-field-card" title={<Form.Item noStyle shouldUpdate>{()=>form.getFieldValue(['criteria',field.name,'name'])||`项目${field.name+1}`}</Form.Item>} extra={<Button size="small" onClick={()=>remove(field.name)}>移除本版项目</Button>}>
            <Form.Item name={[field.name,'criterion_id']} hidden><InputNumber /></Form.Item>
            <div className="flow-form-grid">
              <Form.Item name={[field.name,'name']} label="项目名称" rules={[{required:true}]}><Input placeholder="线径 / 内径 / 硬度" /></Form.Item>
              <Form.Item name={[field.name,'stage']} label="检测阶段" rules={[{required:true}]}><Select options={STAGES} /></Form.Item>
              <Form.Item name={[field.name,'limit_mode']} label="界限要求"><Select options={[{value:'RANGE',label:'上下限（两端都判）'},{value:'UPPER',label:'仅上限（下限留空）'},{value:'LOWER',label:'仅下限（上限留空）'},{value:'PENDING',label:'界限待确认'},{value:'NONE',label:'不要求测'}]} /></Form.Item>
              <Form.Item noStyle shouldUpdate>{()=>{const mode=form.getFieldValue(['criteria',field.name,'limit_mode']);return <>{['RANGE','LOWER'].includes(mode)&&<Form.Item name={[field.name,'lower']} label="下限"><Input inputMode="decimal" placeholder="有下限就填，不用0代替空白" /></Form.Item>}{['RANGE','UPPER'].includes(mode)&&<Form.Item name={[field.name,'upper']} label="上限"><Input inputMode="decimal" placeholder="有上限就填" /></Form.Item>}</>}}</Form.Item>
              <Form.Item name={[field.name,'unit']} label="单位 / 硬度量表"><Input placeholder="mm、Shore A等，以依据为准" /></Form.Item>
              <Form.Item name={[field.name,'method']} label="测量方法"><Input placeholder="按客户要求填写，未知可留空" /></Form.Item>
              <Form.Item name={[field.name,'requirement_state']} label="确认状态"><Select options={STATES} /></Form.Item>
            </div>
            <Space wrap><Form.Item noStyle shouldUpdate>{()=>{const mode=form.getFieldValue(['criteria',field.name,'limit_mode']);return <>{['RANGE','LOWER'].includes(mode)&&<Form.Item name={[field.name,'lower_inclusive']} valuePropName="checked"><Checkbox>下限含等号 ≥</Checkbox></Form.Item>}{['RANGE','UPPER'].includes(mode)&&<Form.Item name={[field.name,'upper_inclusive']} valuePropName="checked"><Checkbox>上限含等号 ≤</Checkbox></Form.Item>}</>}}</Form.Item><Form.Item name={[field.name,'follow_package']} valuePropName="checked"><Checkbox>随包测（未勾仍保留图纸要求）</Checkbox></Form.Item></Space>
            <Form.Item noStyle shouldUpdate>{()=>{const p=form.getFieldValue(['criteria',field.name,'provenance']);return p?<Typography.Paragraph type="secondary">原文：{String(p.raw_text||'未提供')} · {String(p.file||'')} / {String(p.sheet||'')} 第{String(p.row||'未登记')}行</Typography.Paragraph>:null}}</Form.Item>
          </Card>)}<Space wrap><Button onClick={()=>add(emptyCriterion('线径','DIMENSION'))}>增加线径 / 尺寸要求</Button><Button onClick={()=>add(emptyCriterion('成型硬度','MOLDING_HARDNESS'))}>增加成型硬度</Button><Button onClick={()=>add(emptyCriterion('烤后硬度','BAKED_HARDNESS'))}>增加烤后硬度</Button></Space></>}</Form.List>
          <Form.Item noStyle shouldUpdate>{()=>{const criteria=form.getFieldValue('criteria')||[];return (['MOLDING_HARDNESS','BAKED_HARDNESS'] as const).filter(stage=>criteria.some((c:{stage:string;limit_mode:string;requirement_state:string})=>c.stage===stage&&c.limit_mode!=='NONE'&&c.requirement_state!=='NOT_APPLICABLE')).map(stage=><Card key={stage} size="small" title={stage==='MOLDING_HARDNESS'?'成型硬度的对象与方法':'烤后硬度的对象与方法'} className="product-standard-field-card"><div className="flow-form-grid">
            <Form.Item name={['hardness',stage,'object']} label="测量对象"><Select allowClear options={[{value:'UNKNOWN',label:'待确认'},{value:'PRODUCT',label:'成品'},{value:'COUPON',label:'当批胶料自制胶片'}]} /></Form.Item>
            {([['scale','量表'],['instrument','仪器'],['accepted_method','客户认可测法'],['points','测点'],['conditioning','冷却 / 调节'],['reading_time','读数时间']] as const).map(([k,label])=><Form.Item key={k} name={['hardness',stage,k]} label={label}><Input /></Form.Item>)}
          </div></Card>)}}</Form.Item>
        </>},
        {key:'process',label:'工艺与二烤',children:<ProductProcessFields />},
        {key:'source',label:'来源与适用范围',children:<>
          <div className="flow-form-grid"><Form.Item name="source" label="来源文件、版号 / 人工确认依据" rules={[{required:true,message:'请填写标准来源，不能把猜测当作依据'}]}><Input placeholder="例如 客户标准书 E01，或本厂实测确认日期" /></Form.Item><Form.Item name="source_kind" label="资料性质"><Select options={[{value:'CUSTOMER',label:'客户要求'},{value:'FACTORY_PROCESS',label:'本厂工艺'},{value:'FACTORY_EXPERIENCE',label:'本厂经验待核'}]} /></Form.Item><Form.Item name="customer_key" label="适用客户（空表示确认通用）"><Input /></Form.Item><Form.Item name="scope" label="适用范围 / 模具说明"><Input /></Form.Item><Form.Item name="applicable_order_id" label="仅此订单项次（不影响下一单）"><Select allowClear options={data.orders.map(o=>({value:o.id,label:`${o.order_no} / ${o.item_no||'-'}`}))} /></Form.Item>{[['customer_product_no','客户料号'],['drawing_no','客户图号'],['drawing_revision','图纸版次']].map(([k,l])=><Form.Item key={k} name={['applicability',k]} label={l}><Input /></Form.Item>)}</div>
          <Typography.Title level={5}>已有导入检验原值（未确认不会自动生效）</Typography.Title>
          {data.criteria.map(c=><Card key={c.id} size="small" className="product-standard-field-card"><Typography.Text>{inspectionItemLabel(c.inspection_item)} · {flowLimitText({lower:c.lower_limit,upper:c.upper_limit,unit:c.unit,limit_mode:c.limit_mode})} · {c.source_file||'人工资料'} / {c.source_sheet} 第{c.source_row||'未登记'}行</Typography.Text><Button onClick={()=>{form.setFieldValue('criteria',[...(form.getFieldValue('criteria')||[]),{...emptyCriterion(c.inspection_item,c.stage||'DIMENSION'),criterion_id:c.id,lower:c.lower_limit,upper:c.upper_limit,unit:c.unit,method:c.method,limit_mode:c.limit_mode||'PENDING'}]);markChanged();setTab('quality')}}>带入核对</Button></Card>)}
        </>},
        {key:'route',label:'随包记录与包装',children:<>
          <Form.Item name="whole_mold" label="整模工序"><Select options={[{value:'UNKNOWN',label:'待确认'},{value:'REQUIRED',label:'需要整模'},{value:'NOT_REQUIRED',label:'确认不需整模'}]} /></Form.Item>
          <Space wrap>{[['molding','成型实测'],['patrol','巡检'],['deburr','毛边'],['bake','二烤'],['baked_hardness','烤后硬度'],['inspection','品检']].map(([k,l])=><Form.Item key={k} name={['route',k]} valuePropName="checked"><Checkbox>{l}</Checkbox></Form.Item>)}</Space><Form.Item name={['route','confirmed']} valuePropName="checked"><Checkbox>已核对本产品适用工序（不代表已执行）</Checkbox></Form.Item><div className="flow-form-grid"><Form.Item name={['packaging','molds_per_card']} label="每包模数"><InputNumber min={1} precision={0} /></Form.Item><Form.Item name={['packaging','patrol_every_molds']} label="每隔多少模巡检"><InputNumber min={1} precision={0} /></Form.Item></div><Form.Item name="appearance_ids" label="四项外观检查" rules={[{required:true},{type:'array',len:4}]}><Select mode="multiple" options={data.appearance.map(a=>({value:a.id,label:a.name}))} /></Form.Item>
          <div className="flow-form-grid">
            <Form.Item name={['packaging','temperature_plan','mode']} label="产品标准默认测温时机" initialValue="PENDING"><Select options={[{value:'PENDING',label:'待确认，按本批安排'},{value:'FIRST',label:'首件、换模/调机、异常复测'},{value:'EVERY',label:'每包记录接触实测'}]} /></Form.Item>
            <Form.Item noStyle shouldUpdate>{()=>{const mode=form.getFieldValue(['packaging','temperature_plan','mode']);return mode&&mode!=='PENDING'?<Form.Item name={['packaging','temperature_plan','basis']} label="测温时机确认依据" rules={[{required:true,whitespace:true}]}><Input maxLength={500} /></Form.Item>:null}}</Form.Item>
          </div>
          <Typography.Paragraph type="secondary">只复用已核对的时机；具体实测值和原卡引用按本批记录。不要把机台设定温度写成接触实测。</Typography.Paragraph>
        </>},
        {key:'import',label:'导入客户标准',children:<>
          <Alert type="info" title="先识别预览，再带入本产品核对；不会自动新增产品或改订单" description="支持有项目/要求列或横排字段的xlsx。图片和未识别排版请按原件手填。客户、料号、模具和版次要明确匹配，不能只靠同规格猜配。" />
          <Upload accept=".xlsx" showUploadList={false} beforeUpload={file=>importFile(file)}><Button disabled={!write} loading={busy}>选择客户Excel标准表</Button></Upload>
          {imported&&<><Typography.Paragraph>{imported.notice}</Typography.Paragraph><Select style={{width:'100%'}} placeholder="选择本次要带入的标准块" value={selected} onChange={i=>{setSelected(i);setIdentityChecked(false)}} options={imported.candidates.map((c,i)=>({value:i,label:`${c.sheet} · ${c.customer_key||'客户待核'} · ${c.customer_product_no} · ${c.order_no?`${c.order_no}/${c.item_no}`:c.version||'版号待核'}`}))} />
            {candidate&&<Card size="small" className="product-standard-field-card"><Typography.Paragraph>识别：{candidate.customer_product_no} / {candidate.specification||'规格待核'} / {candidate.material||'材质待核'} / 模具 {candidate.mold_no||'待核'}</Typography.Paragraph>{identityConflicts.length>0&&<Alert type="error" title={`与当前产品的${identityConflicts.join('、')}不符，请选择正确产品或核对原文件，不能猜配`} />}{candidate.warnings.map((w,i)=><Typography.Paragraph key={i} type="warning">{w}</Typography.Paragraph>)}{candidate.criteria.map((c,i)=><Typography.Paragraph key={i}>{c.stage} · {inspectionItemLabel(c.name)} · {flowLimitText(c)} · {c.follow_package?'随包测':'仅保留图纸要求'} · 原文 {String(c.provenance?.raw_text||'')}</Typography.Paragraph>)}<Typography.Paragraph>二烤：{bakeText(candidate.bake)}</Typography.Paragraph>{[...NUMERIC_PROCESS_FIELDS,...TEXT_PROCESS_FIELDS].map(([key,label])=>{const p=candidate.process_parameters?.[key];return p?<Typography.Paragraph key={key}>{label}：{processParameterText(p)}</Typography.Paragraph>:null})}<Checkbox checked={identityChecked} onChange={e=>setIdentityChecked(e.target.checked)}>我已核对，这是当前产品的客户料号、材质和适用模具</Checkbox><Button disabled={!identityChecked||identityConflicts.length>0} onClick={applyImport}>带入本产品待核表单（不保存）</Button></Card>}
          </>}
        </>},
      ]} />
    </Form>
  </Modal>
}
