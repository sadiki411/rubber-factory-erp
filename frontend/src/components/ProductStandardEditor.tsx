import { Alert, App, Button, Card, Checkbox, Form, Input, InputNumber, Modal, Radio, Select, Space, Tabs, Typography, Upload } from 'antd'
import { useState } from 'react'
import { flowApi, flowLimitText, flowRequestKey, finishFlowRequest, type PreparationContext, type PreparationSource, type PreparationStandard, type StandardImportPreview } from '../flowcards'
import { ProductProcessFields } from './ProductProcessFields'
import { inspectionItemLabel } from '../inspectionItemLabel'
import type { CustomerInspection } from '../customerInspection'
import { useReadOnly } from '../access'
import { adoptedConditions, factoryItemLabel, mergeStandardEntries, standardItemKey } from './standardEntry'
import { bakeForForm, bakeForSave, bakeText, NUMERIC_PROCESS_FIELDS, processForForm, processForSave, processParameterText, TEXT_PROCESS_FIELDS } from './productStandardFields'

const STAGES = [{value:'MOLDING_HARDNESS',label:'成型后硬度'}, {value:'BAKED_HARDNESS',label:'二烤后硬度'}, {value:'DIMENSION',label:'尺寸'}, {value:'UPPER_TEMP',label:'上模实测模温'}, {value:'LOWER_TEMP',label:'下模实测模温'}, {value:'SECONDARY_CURE',label:'二烤建议'}]
const STANDARD_TABS = [{key:'quality',label:'硬度与尺寸'}, {key:'process',label:'工艺与二烤'}, {key:'source',label:'来源与适用范围'}, {key:'route',label:'打印项目'}, {key:'import',label:'导入客户标准'}]
const emptyCriterion = (name:string, stage:string) => ({name,stage,lower:'',upper:'',unit:'',method:'',limit_mode:'RANGE',requirement_state:'NOT_PROVIDED',follow_package:true,critical:true,upper_inclusive:true,lower_inclusive:true})

export function ProductStandardEditor({data,open,close,changed,initialTab='quality',adopt,onActivated}: {data:PreparationContext;open:boolean;close:()=>void;changed:()=>void;initialTab?:string;adopt?:CustomerInspection;onActivated?:(row:PreparationStandard)=>void}) {
  const bases = adopt ? data.standards.filter(s=>(!s.customer_key||s.customer_key===adopt.customer_code)&&(!s.applicable_order_id||s.applicable_order_id===adopt.order_id)) : data.standards
  const initial = bases.find(s=>s.id===data.auto_standard_id) || bases.find(s=>s.status==='ACTIVE'&&!s.applicable_order_id) || bases[0]
  const valuesFor = (s?:PreparationStandard) => ({customer_key:s?.customer_key||data.orders[0]?.customer_code||data.orders[0]?.customer||'',source_kind:s?.source_kind||'FACTORY_PROCESS',source:s?.source||'',scope:s?.scope||'',applicable_order_id:s?.applicable_order_id,
    whole_mold:s?.whole_mold||'UNKNOWN',applicability:s?.snapshot.applicability||{},appearance_ids:(s?.snapshot.appearance||data.appearance.slice(0,4)).map(a=>a.id),
    route:s?.snapshot.route||{confirmed:false,molding:true,patrol:true,deburr:true,inspection:true},packaging:s?.snapshot.packaging||{molds_per_card:100},
    hardness:s?.snapshot.hardness||{},bake:bakeForForm(s?.snapshot.bake),process_parameters:processForForm(s?.snapshot.process_parameters),import_source:s?.snapshot.import_source,
    customer_reference:s?.snapshot.customer_reference,
    criteria:s?.snapshot.criteria.map(c=>({...c,criterion_id:c.id,follow_package:c.follow_package!==false,critical:c.critical!==false,mode:'OVERRIDE'}))||[]})
  const initialValues=valuesFor(initial)
  const fromOrder=adopt ? adoptedConditions(adopt) : undefined
  const adoptedValues=fromOrder ? {...initialValues,...fromOrder,source_kind:'CUSTOMER',applicability:{customer_product_no:adopt?.project_no},
    process_parameters:processForForm({...initialValues.process_parameters,...fromOrder.process_parameters}),bake:fromOrder.bake?bakeForForm(fromOrder.bake):initialValues.bake,
    route:fromOrder.bake?{...initialValues.route,bake:fromOrder.bake.state==='APPLICABLE',baked_hardness:fromOrder.bake.state==='APPLICABLE'&&(initialValues.route as Record<string,boolean>).baked_hardness===true}:initialValues.route} : initialValues
  const [form] = Form.useForm()
  const [busy,setBusy] = useState(false)
  const [error,setError] = useState('')
  const [saved,setSaved] = useState<PreparationStandard>()
  const [imported,setImported] = useState<StandardImportPreview>()
  const [selected,setSelected] = useState<number>()
  const [identityChecked,setIdentityChecked] = useState(false)
  const [versionId,setVersionId] = useState(initial?.id)
  const [tab,setTab] = useState(initialTab)
  const [sourceStages,setSourceStages] = useState<Record<number,string>>({})
  const {message} = App.useApp()
  const readOnly=useReadOnly()
  const write = !readOnly&&data.can_maintain_standard && data.write_enabled
  const markChanged = () => setSaved(undefined)
  const bringCriterion = (c:PreparationSource) => {
    const stage=sourceStages[c.id]||c.stage
    if(!stage) return
    try {
      const old=form.getFieldValue('criteria')||[]
      const name=old.find((row:{criterion_id?:number;stage:string;name:string})=>row.criterion_id===c.id&&row.stage===stage)?.name||c.inspection_item
      const next=mergeStandardEntries(old,[{...emptyCriterion(name,stage),criterion_id:c.id,lower:c.lower_limit,upper:c.upper_limit,unit:c.unit,method:c.method,
        limit_mode:c.limit_mode||'PENDING',lower_inclusive:c.lower_inclusive,upper_inclusive:c.upper_inclusive}])
      form.setFieldValue('criteria',next)
      markChanged();setError('');setTab('quality')
    } catch(e) {setError((e as Error).message)}
  }
  const save = async (activate:boolean) => {
    setBusy(true);setError('')
    try {
      await form.validateFields()
      const values = form.getFieldsValue(true)
      if(!String(values.source||'').trim()) {setTab('source');throw new Error('请填写来源说明；已经导入的标准会自动填写。')}
      if(new Set(values.appearance_ids||[]).size!==4) {setTab('route');throw new Error('请在「打印项目」选好四项外观检查。')}
      const criteria = (values.criteria||[]).map((c:Record<string,unknown>)=>{
        const hasLower=String(c.lower??'').trim()!==''
        const hasUpper=String(c.upper??'').trim()!==''
        const mode=c.requirement_state==='NOT_APPLICABLE'||c.limit_mode==='NONE'?'NONE':hasLower&&hasUpper?'RANGE':hasUpper?'UPPER':hasLower?'LOWER':'PENDING'
        return {...c,...(mode==='UPPER'?{lower:''}:mode==='LOWER'?{upper:''}:{}),limit_mode:mode,override:Boolean(c.criterion_id),numeric:['UPPER','LOWER','RANGE'].includes(String(mode)),
          requirement_state:mode==='NONE'?'NOT_APPLICABLE':mode==='PENDING'?(c.follow_package===false?'RAW_UNCONFIRMED':'MEASURE_NO_LIMIT'):'CONFIRMED',
          critical:c.follow_package!==false && mode!=='NONE',follow_package:c.follow_package!==false}
      })
      const keys=criteria.map((c:{name:string;stage:string})=>standardItemKey(c.name,c.stage))
      if(new Set(keys).size!==keys.length) throw new Error('同一项目、同一检验阶段只保留一行。核对下限、上限后删除重复项。')
      const parameters = processForSave(values.process_parameters)
      if(activate) {
        for(const [key,p] of Object.entries(parameters)) {
          if(NUMERIC_PROCESS_FIELDS.some(([k])=>k===key)&&!p.unit&&key==='primary_curing_time'&&[p.value,p.lower,p.upper].some(v=>v!=null&&v!=='')) p.unit='s'
          if([p.value,p.lower,p.upper].some(v=>v!=null&&v!=='')&&p.unit) p.state='CONFIRMED'
          if(TEXT_PROCESS_FIELDS.some(([k])=>k===key)&&p.raw_text&&!p.state) p.state='CONFIRMED'
        }
      }
      const bake = bakeForSave(values.bake)
      const requiresBakedHardness = criteria.some((c:{stage:string;follow_package:boolean;limit_mode:string})=>c.stage==='BAKED_HARDNESS'&&c.follow_package&&c.limit_mode!=='NONE')
      if(activate&&requiresBakedHardness&&bake.state!=='APPLICABLE') {setTab('process');throw new Error('已选择在卡上记录二烤后硬度，请先确认二烤要求。')}
      const route = bake.state==='NOT_APPLICABLE' ? {...values.route,bake:false,baked_hardness:false} : {...values.route,bake:bake.state==='APPLICABLE'||values.route?.bake===true,baked_hardness:requiresBakedHardness}
      if(activate&&adopt&&values.whole_mold==='UNKNOWN') {setTab('route');throw new Error('制卡需要知道是否整模检查，请在「打印项目」选一下。')}
      if(activate) route.confirmed=true
      const body = {...values,bake,route,packaging:{...values.packaging,simple_entry:true},process_parameters:parameters,criteria,dimension_count:criteria.filter((c:{stage:string;follow_package:boolean;limit_mode:string})=>c.stage==='DIMENSION'&&c.follow_package&&c.limit_mode!=='NONE').length,product_id:data.product.id,mold_id:data.mold_id}
      const slot=`product-standard:${data.product.id}`
      const result = saved && saved.snapshot?.route?.confirmed===route.confirmed ? saved : await flowApi.saveStandard({...body,request_key:flowRequestKey(slot,body)})
      setSaved(result);finishFlowRequest(slot);changed()
      if(activate) {const active=await flowApi.activateStandard(result.id);onActivated?.(active);changed();message.success(values.applicable_order_id?'已保存并用于本单制卡，旧卡不变':'已保存到本产品并生效，旧卡不变');close()}
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
    const sameScope = (!old.customer_key||!candidate.customer_key||normalize(old.customer_key)===normalize(candidate.customer_key))&&(!old.applicable_order_id||old.applicable_order_id===exceptionOrder?.id)
    const base = sameScope ? old : valuesFor()
    try {
      const criteria=mergeStandardEntries(base.criteria||[],candidate.criteria.map(c=>({...c,provenance:{...c.provenance,file:imported?.filename}})))
      form.resetFields()
      form.setFieldsValue({...base,...candidate,bake:candidate.bake?.state==='UNKNOWN'?base.bake:bakeForForm(candidate.bake),process_parameters:processForForm({...base.process_parameters,...candidate.process_parameters}),source:`${imported?.filename} / ${candidate.source}`,applicable_order_id:exceptionOrder?.id,applicability:{...base.applicability,customer_product_no:candidate.customer_product_no},customer_reference:undefined,
        import_source:{...candidate.import_source,filename:imported?.filename},criteria})
      markChanged();setTab('quality');message.info('已带入。相同项目补到原行，检查上下限后保存即可。')
    } catch(e) {setError((e as Error).message)}
  }
  return <Modal open={open} onCancel={close} title={`${data.product.specification} / ${data.product.material} · 产品标准`} width={920} className="product-standard-editor" footer={<Space wrap><Button onClick={close}>关闭</Button><Button disabled={!write||tab==='import'} loading={busy} onClick={()=>void save(false)}>保存草稿</Button><Button type="primary" disabled={!write||tab==='import'} loading={busy} onClick={()=>void save(true)}>{adopt?'保存并用于本单制卡':'保存并生效'}</Button></Space>}>
    {!adopt&&<Alert type="info" showIcon title="这里填写标准，不是本批实测值" />}
    {!write&&<Alert type="warning" title="当前只读，不能修改产品标准" />}
    {error&&<Alert type="error" title="未完成保存，请核对" description={error} />}
    {!adopt&&data.standards.length>0&&<Space wrap style={{marginBlock:12}}><Typography.Text>已有版本：</Typography.Text><Select style={{minWidth:180,maxWidth:'100%'}} value={versionId} options={data.standards.map(s=>({value:s.id,label:`v${s.version} · ${{ACTIVE:'生效',DRAFT:'草稿',SUPERSEDED:'已替代',LEGACY:'旧记录待核'}[s.status]||s.status} · ${s.source}`}))} onChange={id=>{setVersionId(id);form.resetFields();form.setFieldsValue(valuesFor(data.standards.find(s=>s.id===id)));markChanged()}} /></Space>}
    {adopt&&<Alert type="success" title={`已带入本单翔邦原表 · 客户 ${adopt.customer_code}`} description="模温、加硫和二烤已带入；检验原值留在附件里，可另选要印的项目。" />}
    <Form form={form} layout="vertical" disabled={!write} initialValues={adoptedValues} onValuesChange={markChanged}>
      <Tabs activeKey={tab} onChange={setTab} renderTabBar={()=> <div role="tablist" aria-label="产品标准分区" style={{marginBlock:12}}><Space wrap>{STANDARD_TABS.map(({key,label})=><Button key={key} role="tab" aria-selected={tab===key} type={tab===key?'primary':'default'} onClick={()=>setTab(key)}>{label}</Button>)}</Space></div>} items={[
        {key:'quality',label:'硬度与尺寸',children:<>
          <Typography.Paragraph type="secondary">这里只填要印在本厂卡上的项目。原文件只给一边时，只填对应的下限或上限；空白不按 0 算。</Typography.Paragraph>
          <Form.List name="criteria">{(fields,{add,remove})=><>{fields.map(field=><Card key={field.key} size="small" className="product-standard-field-card" title={<Form.Item noStyle shouldUpdate>{()=>inspectionItemLabel(factoryItemLabel(form.getFieldValue(['criteria',field.name,'name'])||`项目${field.name+1}`,form.getFieldValue(['criteria',field.name,'stage'])||''))}</Form.Item>} extra={<Button size="small" onClick={()=>remove(field.name)}>移除本版项目</Button>}>
            <Form.Item name={[field.name,'criterion_id']} hidden><InputNumber /></Form.Item>
            <div className="flow-form-grid">
              <Form.Item name={[field.name,'name']} label="项目名称" rules={[{required:true}]}><Input placeholder="线径 / 内径 / 硬度" /></Form.Item>
              <Form.Item name={[field.name,'stage']} label="检测阶段" rules={[{required:true}]}><Select options={STAGES} /></Form.Item>
              <Form.Item name={[field.name,'lower']} label="下限"><Input inputMode="decimal" placeholder="没有下限可留空" /></Form.Item>
              <Form.Item name={[field.name,'upper']} label="上限"><Input inputMode="decimal" placeholder="没有上限可留空" /></Form.Item>
              <Form.Item name={[field.name,'unit']} label="单位 / 硬度量表"><Input placeholder="mm、Shore A等，以依据为准" /></Form.Item>
            </div>
            <Form.Item name={[field.name,'follow_package']} valuePropName="checked"><Checkbox>印到流转卡（取消后只留标准资料）</Checkbox></Form.Item>
            <Form.Item noStyle shouldUpdate>{()=>{const p=form.getFieldValue(['criteria',field.name,'provenance']);return p?<Typography.Paragraph type="secondary">原文：{String(p.raw_text||'未提供')} · {String(p.file||'')} / {String(p.sheet||'')} 第{String(p.row||'未登记')}行</Typography.Paragraph>:null}}</Form.Item>
          </Card>)}<Space wrap><Button onClick={()=>add(emptyCriterion('线径','DIMENSION'))}>增加线径 / 尺寸要求</Button><Button onClick={()=>add(emptyCriterion('成型硬度','MOLDING_HARDNESS'))}>增加成型硬度</Button><Button onClick={()=>add(emptyCriterion('烤后硬度','BAKED_HARDNESS'))}>增加烤后硬度</Button></Space></>}</Form.List>
        </>},
        {key:'process',label:'工艺与二烤',children:<ProductProcessFields />},
        {key:'source',label:'来源与适用范围',children:<>
          <div className="flow-form-grid"><Form.Item name="source" label="来源文件、版号 / 人工确认依据" rules={[{required:true,message:'请填写标准来源'}]}><Input placeholder="导入自动填写，手填写明依据" /></Form.Item><Form.Item name="customer_key" label="适用客户（空表示通用）"><Input disabled={Boolean(adopt)} /></Form.Item>{!adopt&&<Form.Item name="applicable_order_id" label="仅此订单项次（选填）"><Select allowClear options={data.orders.map(o=>({value:o.id,label:o.order_no+' / '+(o.item_no||'-')}))} /></Form.Item>}</div>
          {data.criteria.length>0&&<details><summary>已有导入原值（按原项目采用）</summary>
          {data.criteria.filter(c=>!c.order_id||(adopt ? c.order_id===adopt.order_id : data.order_ids.includes(c.order_id))).map(c=><Card key={c.id} size="small" className="product-standard-field-card">
            <Typography.Paragraph>{inspectionItemLabel(c.inspection_item)} · {c.lower_limit||'—'} ～ {c.upper_limit||'—'} {c.unit} · {c.category||'原类别未登记'} · {c.source_file||'人工资料'}</Typography.Paragraph>
            <Space wrap><Select aria-label={`选择 ${c.inspection_item} 的检验阶段`} placeholder="选择检验阶段" value={sourceStages[c.id]||c.stage} options={STAGES} style={{minWidth:150}} onChange={stage=>setSourceStages({...sourceStages,[c.id]:stage})} /><Button disabled={!sourceStages[c.id]&&!c.stage} onClick={()=>bringCriterion(c)}>采用这行原值</Button></Space>
          </Card>)}
          </details>}
        </>},
        {key:'route',label:'打印项目',children:<>
          <Form.Item name="whole_mold" label="整模工序"><Radio.Group options={[{value:'REQUIRED',label:'需要整模'},{value:'NOT_REQUIRED',label:'不需整模'}]} /></Form.Item>
          <Space wrap>{[['molding','成型实测'],['patrol','巡检'],['deburr','毛边'],['inspection','品检']].map(([k,l])=><Form.Item key={k} name={['route',k]} valuePropName="checked"><Checkbox>{l}</Checkbox></Form.Item>)}</Space>
          <div className="flow-form-grid"><Form.Item name={['packaging','molds_per_card']} label="每包模数"><InputNumber min={1} precision={0} /></Form.Item><Form.Item name={['packaging','patrol_every_molds']} label="每隔多少模巡检（选填）"><InputNumber min={1} precision={0} /></Form.Item></div>
          <Form.Item name="appearance_ids" label="四项外观检查" rules={[{required:true},{type:'array',len:4}]}><Select mode="multiple" options={data.appearance.map(a=>({value:a.id,label:a.name}))} /></Form.Item>
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
