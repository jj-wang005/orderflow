<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import { ElMessage, ElMessageBox, type UploadFile } from 'element-plus'
import { Plus, RefreshRight, UploadFilled } from '@element-plus/icons-vue'
import PageHeading from '../components/PageHeading.vue'
import { customerApi, fieldLabels, trialStatusLabels, type TrialPreview, type Customer, type CustomerDetail, type ConfigBody, type MappingPreview, type ConfigPreview, type RuleTemplate } from '../api/customers'
import { ApiError, errorText } from '../api/client'
import { useCustomers } from '../composables/useCustomers'
const {customers,customerError,loadCustomers}=useCustomers()
const busy=ref(false), error=ref(''), createOpen=ref(false), basicOpen=ref(false), configOpen=ref(false), historyOpen=ref(false)
const newCustomer=reactive({customer_id:'',customer_name:''}), current=ref<CustomerDetail|null>(null), basicName=ref('')
const body=reactive<ConfigBody>({expected_version:0,customer_name:'',sheet_name:'Sheet1',header_row:1,field_mapping:{},match_fields:[],historical_quantity:null,enabled:true})
const sample=shallowRef<File|null>(null), suggestions=ref<MappingPreview|null>(null), preview=ref<ConfigPreview|null>(null), templates=ref<RuleTemplate[]>([])
const trial=ref<TrialPreview|null>(null)
const previewAction=ref<'purchase'|'trial'|null>(null), previewError=ref(''), trialStale=ref(false)
const previewArea=ref<HTMLElement|null>(null), purchaseResult=ref<HTMLElement|null>(null), trialResult=ref<HTMLElement|null>(null)
const columnRows=ref<{index:number;column:string|null;mappable:boolean;samples:unknown[];reason:string;target:string}[]>([])
const headerLocation=ref(''), fullHeaders=ref(false), mappingOpen=ref(false)
const location=computed(()=>JSON.stringify([body.sheet_name,body.header_row]))
const headersChanged=computed(()=>headerLocation.value!==location.value)
const quantityOperator=ref<'none'|'gt'|'gte'|'eq'>('none'), quantityValue=ref('500')
const mappedColumns=computed(()=>columnRows.value.filter(c=>c.mappable&&c.column&&c.target))
const availableFields=computed(()=>Object.values(body.field_mapping))
const matchOptions=computed(()=>Object.keys(fieldLabels).filter(f=>f!=='quantity'))
const legal=computed(()=>!headersChanged.value && availableFields.value.length===new Set(availableFields.value).size && availableFields.value.includes('quantity') && body.match_fields.length>0 && body.match_fields.length===new Set(body.match_fields).size && body.match_fields.every(f=>matchOptions.value.includes(f)&&availableFields.value.includes(f)) && body.customer_name.trim() && body.sheet_name.trim() && (quantityOperator.value==='none' || /^\d{1,18}$/.test(quantityValue.value)))
function matchValuesText(values:Record<string,unknown>){return Object.entries(values).map(([field,value])=>fieldLabels[field]+'：'+(value??'（缺失）')).join('；')}
function errorFieldsText(fields:string[]){return fields.map(f=>fieldLabels[f]||f).join('、')}
function syncMapping(){const mapping:Record<string,string>={};for(const row of columnRows.value){if(row.target&&row.column&&row.mappable)mapping[row.column]=row.target}body.field_mapping=mapping;preview.value=null}
function payload():ConfigBody{return {...body,source_columns:fullHeaders.value?columnRows.value.map(c=>({index:c.index,name:c.column})):undefined,field_mapping:{...body.field_mapping},match_fields:[...body.match_fields],historical_quantity:quantityOperator.value!=='none'?{operator:quantityOperator.value,value:quantityValue.value}:null}}
function invalidatePreview(){if(trial.value)trialStale.value=true;preview.value=null;trial.value=null;previewError.value=''}
watch(()=>JSON.stringify(payload()),invalidatePreview)
watch(sample,invalidatePreview)
async function reveal(target:typeof previewArea){await nextTick();target.value?.scrollIntoView({behavior:'smooth',block:'start'});target.value?.focus({preventScroll:true})}
async function previewRequest(kind:'purchase'|'trial',action:()=>Promise<void>){
 if(busy.value)return
 previewError.value=''
 if(!current.value||!sample.value||!legal.value){
  previewError.value=!sample.value?'请先上传一次 Excel 样例，再预览采购数据或试匹配历史订单。':headersChanged.value?'工作表或表头行已改变，请先点击重新识别。':'请检查草稿：采购数量必须映射，至少选择一个已映射的匹配字段，并填写合法的历史数量条件。'
  await reveal(previewArea);return
 }
 previewAction.value=kind
 await run(async()=>{try{await action()}catch(e){previewError.value=(kind==='trial'?'历史订单试匹配失败：':'采购数据预览失败：')+(e instanceof ApiError&&e.status===404?'当前后端未提供此预览接口，请重启当前环境后端并刷新页面。':errorText(e));await reveal(previewArea)}})
 previewAction.value=null
}
async function previewMapping(){if(busy.value)return;if(!columnRows.value.length||headersChanged.value){previewError.value=headersChanged.value?'工作表或表头行已改变，请先重新识别。':'请先上传 Excel 样例读取原始表头。';await reveal(previewArea);return}previewError.value='';mappingOpen.value=!mappingOpen.value}
async function run(action:()=>Promise<void>){if(busy.value)return;busy.value=true;error.value='';try{await action()}catch(e){error.value=errorText(e)}finally{busy.value=false}}
async function create(){await run(async()=>{const c=await customerApi.create(newCustomer);createOpen.value=false;await loadCustomers();await openConfig(c);ElMessage.success('客户已新增，请完成配置')})}
async function openConfig(customer:Customer){try{error.value='';const detail=await customerApi.get(customer.customer_id);current.value=detail;const config=detail.configuration;body.expected_version=detail.config_version;body.customer_name=detail.customer_name;body.sheet_name=config?.customer.sheet_name||'Sheet1';body.header_row=config?.customer.header_row||1;body.enabled=config?detail.enabled:true;body.field_mapping={...(config?.customer.field_mapping||{})};body.match_fields=[...(config?.rule.match_fields||[]),...(config?.rule.validation_config.consistency_fields||[])];const filter=config?.rule.filter_conditions.historical_quantity;quantityOperator.value=filter?.operator||'none';quantityValue.value=filter?.value||'500';const columns=config?.source_columns||[];fullHeaders.value=columns.length>0;columnRows.value=(columns.length?columns:Object.keys(body.field_mapping).map((name,i)=>({index:i+1,name}))).map(c=>({index:c.index,column:c.name,mappable:!!c.name&&c.name===c.name.trim()&&(columns.length?columns.filter(x=>x.name===c.name).length===1:true),target:c.name?body.field_mapping[c.name]||'':'',samples:[],reason:c.name&&body.field_mapping[c.name]?'已保存映射':'未映射'}));headerLocation.value=location.value;mappingOpen.value=false;sample.value=null;suggestions.value=null;preview.value=null;trial.value=null;trialStale.value=false;previewError.value='';configOpen.value=true}catch(e){error.value=errorText(e)}}
async function choose(upload:UploadFile){if(!upload.raw||busy.value)return;sample.value=upload.raw;await recognize(false)}
async function recognize(keepSheet=true){if(!sample.value)return;await run(async()=>{const old={...body.field_mapping};const known=new Set(fullHeaders.value?columnRows.value.map(c=>c.column):Object.keys(old));const result=await customerApi.suggest(sample.value!,keepSheet?body.sheet_name:'',body.header_row);suggestions.value=result;body.sheet_name=result.sheet_name;body.header_row=result.header_row;const used=new Set<string>();columnRows.value=result.columns.map((c,i)=>{const target=c.mappable===false?'':c.column&&known.has(c.column)?old[c.column]||'':c.recommended_field||'';return {index:c.index||i+1,column:c.column,mappable:c.mappable!==false,samples:c.samples,reason:c.reason,target}});for(const c of columnRows.value){if(c.target&&used.has(c.target))c.target='';if(c.target)used.add(c.target)}fullHeaders.value=true;headerLocation.value=location.value;syncMapping()})}
function useTemplate(template:RuleTemplate){body.match_fields=[...template.match_fields];quantityOperator.value=template.historical_quantity?.operator||'none';quantityValue.value=template.historical_quantity?.value||'500';preview.value=null}
async function previewPurchase(){await previewRequest('purchase',async()=>{preview.value=null;preview.value=await customerApi.preview(current.value!.customer_id,sample.value!,payload());await reveal(purchaseResult)})}
async function previewTrial(){await previewRequest('trial',async()=>{trial.value=null;trialStale.value=false;trial.value=await customerApi.trial(current.value!.customer_id,sample.value!,payload());await reveal(trialResult)})}
async function save(){if(!current.value||!legal.value)return;try{await ElMessageBox.confirm('确认字段含义和匹配规则符合该客户业务？保存后仅后续新导入使用此版本，历史任务保持原配置。','确认保存正式配置',{confirmButtonText:'确认保存',cancelButtonText:'继续核对',type:'warning'})}catch{return}await run(async()=>{current.value=await customerApi.save(current.value!.customer_id,payload());configOpen.value=false;await loadCustomers();ElMessage.success('正式配置已保存')})}
async function basic(customer:Customer){await run(async()=>{current.value=await customerApi.get(customer.customer_id);basicName.value=current.value.customer_name;basicOpen.value=true})}
async function saveBasic(){await run(async()=>{current.value=await customerApi.edit(current.value!.customer_id,{expected_version:current.value!.config_version,customer_name:basicName.value});basicOpen.value=false;await loadCustomers();ElMessage.success('客户信息已保存')})}
async function history(customer:Customer){await run(async()=>{current.value=await customerApi.get(customer.customer_id);historyOpen.value=true})}
onMounted(async()=>{try{templates.value=(await customerApi.templates()).templates}catch(e){error.value=errorText(e)}})
</script>
<template>
  <PageHeading title="客户配置管理" eyebrow="CUSTOMER CONFIGURATION" subtitle="维护客户基本信息、Excel 字段映射与自动翻单规则。每个客户只使用一套当前正式配置。"><el-button :icon="RefreshRight" :disabled="busy" @click="loadCustomers">刷新</el-button><el-button type="primary" :icon="Plus" :disabled="busy" @click="newCustomer.customer_id='';newCustomer.customer_name='';error='';createOpen=true">新增客户</el-button></PageHeading>
  <el-alert v-if="customerError || error" :title="customerError || error" type="error" :closable="false" show-icon class="section-alert" />
  <section class="panel table-panel"><div class="panel-heading"><div><h2>客户列表 <span class="count-pill">{{customers.length}}</span></h2><p>新客户无需已有历史订单即可保存合法配置。尚未配置的客户暂不可导入。</p></div></div>
  <el-table :data="customers" row-key="customer_id" v-loading="busy"><el-table-column prop="customer_id" label="客户标识" width="140"/><el-table-column prop="customer_name" label="客户名称" min-width="220"/><el-table-column label="配置状态" width="150"><template #default="{row}"><el-tag :type="!row.configured?'warning':row.enabled?'success':'info'">{{!row.configured?'待配置':row.enabled?'已启用':'已暂停'}}</el-tag></template></el-table-column><el-table-column label="当前版本" width="130"><template #default="{row}">{{row.configured?'V'+row.config_version:'—'}}</template></el-table-column><el-table-column label="操作" min-width="280"><template #default="{row}"><el-button link type="primary" :disabled="busy" @click="basic(row)">编辑信息</el-button><el-button link type="primary" :disabled="busy" @click="openConfig(row)">{{row.configured?'维护配置':'首次配置'}}</el-button><el-button link :disabled="busy" @click="history(row)">版本记录</el-button></template></el-table-column></el-table></section>
  <el-dialog v-model="createOpen" title="新增客户" width="480px" :close-on-click-modal="false"><el-alert v-if="error" :title="error" type="error" :closable="false"/><el-form label-position="top"><el-form-item label="客户标识" required><el-input v-model="newCustomer.customer_id" maxlength="64" placeholder="例如 D、E；字母数字、下划线或短横线"/></el-form-item><el-form-item label="客户名称" required><el-input v-model="newCustomer.customer_name" maxlength="255"/></el-form-item></el-form><template #footer><el-button :disabled="busy" @click="createOpen=false">取消</el-button><el-button type="primary" :loading="busy" :disabled="!newCustomer.customer_id.trim()||!newCustomer.customer_name.trim()" @click="create">新增并配置</el-button></template></el-dialog>
  <el-dialog v-model="basicOpen" title="编辑客户基本信息" width="480px" :close-on-click-modal="false"><el-alert v-if="error" :title="error" type="error" :closable="false"/><el-form label-position="top"><el-form-item label="客户标识"><el-input :model-value="current?.customer_id" disabled/></el-form-item><el-form-item label="客户名称" required><el-input v-model="basicName" maxlength="255"/></el-form-item></el-form><template #footer><el-button :disabled="busy" @click="basicOpen=false">取消</el-button><el-button type="primary" :loading="busy" :disabled="!basicName.trim()" @click="saveBasic">保存</el-button></template></el-dialog>
  <el-dialog v-model="configOpen" :title="'客户配置 · '+(current?.customer_name||'')" width="min(1100px, 95vw)" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="section-alert"/>
    <el-alert title="客户隔离、有效历史状态、产品唯一和正数成交单价由系统强制校验；确认状态后需新采购导入才会翻单。" type="info" :closable="false" show-icon/>
    <el-form label-position="top" class="config-form" :disabled="busy"><div class="config-grid"><el-form-item label="客户名称" required><el-input v-model="body.customer_name" maxlength="255"/></el-form-item><el-form-item label="正式配置启用"><el-switch v-model="body.enabled" active-text="后续新导入可使用"/></el-form-item></div>
    <h3>1. Excel 字段映射</h3><p class="muted">首次上传代表性样例，系统只填写有依据的建议；请核对后保存。后续导入直接复用。</p>
    <el-alert v-if="current?.configured&&!fullHeaders" title="旧配置未保存完整表头，目前只能显示已映射列。请上传原模板恢复全部表头；系统不会猜测缺失列。" type="warning" :closable="false"/>
    <p v-else-if="current?.header_source==='original_template'&&!sample" class="field-help">全部表头已从项目原始模板恢复；保存新版本后会一并保留。历史任务保持原样。</p>
    <el-upload :key="current?.customer_id" :auto-upload="false" :show-file-list="false" accept=".xlsx" :on-change="choose" :disabled="busy"><el-button :icon="UploadFilled">{{sample?'更换 Excel 样例':'上传 Excel 样例'}}</el-button><template #tip><div class="el-upload__tip">{{sample?.name||'维护已有配置时可直接编辑，无需重新上传。'}} · 仅 XLSX</div></template></el-upload>
    <div class="config-grid"><el-form-item label="采购数据工作表" required><el-select v-if="suggestions" v-model="body.sheet_name"><el-option v-for="s in suggestions.sheet_names" :key="s" :value="s" :label="s"/></el-select><el-input v-else v-model="body.sheet_name" maxlength="128"/></el-form-item><el-form-item label="表头行号" required><div><el-input-number v-model="body.header_row" :min="1" :max="10000" :precision="0"/><el-button v-if="sample" :disabled="busy" @click="recognize()">重新识别</el-button></div></el-form-item></div>
    <el-alert v-if="headersChanged" title="工作表或表头行已改变，请使用当前样例点击重新识别，再预览和保存。" type="warning" :closable="false"/>
    <p class="field-help">{{fullHeaders?'全部原始列':'已知映射列'}} {{columnRows.length}} 列 · 已映射 {{Object.keys(body.field_mapping).length}} 列 · 其余列保留为未映射</p>
    <el-table :data="columnRows" max-height="340" row-key="index"><el-table-column prop="index" label="列位置" width="85"/><el-table-column label="Excel 原表头" min-width="170"><template #default="{row}">{{row.column===null||row.column===''?'（空表头）':row.column}}</template></el-table-column><el-table-column label="样例值" min-width="200"><template #default="{row}"><span>{{row.samples.map(String).join(' / ')||'—'}}</span></template></el-table-column><el-table-column label="系统字段" min-width="210"><template #default="{row}"><el-select v-model="row.target" clearable placeholder="不映射 / 人工选择" :disabled="busy||!row.mappable||headersChanged" @change="syncMapping"><el-option v-for="(label,field) in fieldLabels" :key="field" :value="field" :label="label" :disabled="columnRows.some(r=>r!==row&&r.target===field)"/></el-select><span v-if="!row.target" class="muted">未映射</span></template></el-table-column><el-table-column prop="reason" label="建议依据" min-width="190"/></el-table>
    <p v-if="!columnRows.length" class="muted">上传样例后展示原始表头和映射建议。未知列可以忽略或由销管选择。</p>
    <h3>2. 可视化匹配规则</h3><p class="muted">候选模板仅供建议，字段齐全不代表业务已确认可靠。请按该客户实际业务选择。</p>
    <div class="template-options"><el-button v-for="t in templates" :key="t.id" :disabled="busy||!t.match_fields.every(f=>availableFields.includes(f))" @click="useTemplate(t)">{{t.name}}</el-button></div>
    <el-form-item label="历史匹配字段（至少一项）" required><el-checkbox-group v-model="body.match_fields" @change="preview=null"><el-checkbox v-for="field in matchOptions" :key="field" :value="field" :disabled="!availableFields.includes(field)">{{fieldLabels[field]}}</el-checkbox></el-checkbox-group></el-form-item>
    <p class="field-help">可自由选择一个或多个字段，所选字段与采购数量均须映射且有有效值。字段越少越需确认业务可靠性，即使候选只有一种产品也不代表业务上绝对准确。保存后仅新任务使用新规则，历史任务保留原校验。</p>
    <p class="field-help">只有料号或机型等信息时，历史匹配通过后仍可能需要独立的采购人工核实；产品歧义不会由采购核实自动消除。</p>
    <div class="config-grid"><el-form-item label="附加筛选：历史销售订单数量"><el-select v-model="quantityOperator"><el-option label="不限制历史数量" value="none"/><el-option label="大于 >" value="gt"/><el-option label="大于等于 ≥" value="gte"/><el-option label="等于 =" value="eq"/></el-select></el-form-item><el-form-item v-if="quantityOperator!=='none'" label="历史数量阈值（非负整数）"><el-input v-model="quantityValue" inputmode="numeric" placeholder="例如 500"/></el-form-item></div>
    <p class="field-help">同产品的合格历史按创建时间倒序、ID 倒序选择；更新时间不参与排序。客户 Excel 单价不作为自动成交价。</p>
    <section ref="previewArea" class="preview-area" tabindex="-1" data-testid="preview-actions"><h3>3. 预览与确认</h3>
    <el-button :disabled="busy" @click="previewMapping">预览映射关系</el-button>
    <el-button :disabled="busy" :loading="previewAction==='purchase'" @click="previewPurchase">预览采购数据</el-button>
    <el-button :disabled="busy" :loading="previewAction==='trial'" @click="previewTrial">历史订单试匹配</el-button>
    <p class="muted">映射关系只展示已映射列；采购数据只解析样例；历史试匹配每次点击均查询当前历史订单。三项预览均不保存配置、不创建任务或订单。</p>
    <p v-if="!sample" class="field-help">当前会话没有 Excel 样例文件。预览采购数据或历史订单试匹配需上传一次，之后修改规则可直接复用；已保存表头不能代替实际采购行。</p>
    <el-alert v-if="previewError" :title="previewError" type="error" :closable="false" show-icon data-testid="preview-error"/>
    <el-alert v-if="trialStale" title="样例或草稿配置已变更，原试匹配结果已过期，请重新点击历史订单试匹配。" type="warning" :closable="false" show-icon data-testid="trial-stale"/>
    <p v-if="previewAction" role="status">{{previewAction==='trial'?'正在查询历史订单并试匹配，请稍候…':'正在解析样例采购数据，请稍候…'}}</p>
    </section>
    <div v-if="mappingOpen" data-testid="mapping-preview"><p>已映射 {{mappedColumns.length}} 列，按 Excel 原始列顺序展示。</p><el-table :data="mappedColumns" max-height="260"><el-table-column prop="index" label="列位置" width="85"/><el-table-column prop="column" label="Excel 原始列名"/><el-table-column label="系统字段"><template #default="{row}">{{fieldLabels[row.target]}}</template></el-table-column></el-table></div>
    <section v-if="preview&&!headersChanged" ref="purchaseResult" class="preview-area" tabindex="-1" data-testid="purchase-preview"><h4>采购数据预览结果</h4><el-alert v-if="preview.file_error" :title="preview.file_error.message" type="error" :closable="false"/><template v-else><p>样例共 {{preview.total_rows}} 行，可解析 {{preview.valid_rows}} 行，需核查 {{preview.invalid_rows}} 行。以下展示前 20 行。</p><el-table :data="preview.rows" max-height="260"><el-table-column prop="row_number" label="Excel 行" width="100"/><el-table-column label="标准化采购信息" min-width="280"><template #default="{row}"><span v-if="row.data">{{Object.entries(fieldLabels).filter(([field])=>row.data[field]!=null).map(([field,label])=>label+'：'+row.data[field]).join('；')}}</span><span v-else>{{row.errors.map((e:{message:string})=>e.message).join('；')}}</span></template></el-table-column></el-table></template></section>
    <section v-if="trial&&!headersChanged" ref="trialResult" class="preview-area" tabindex="-1" data-testid="trial-preview">
      <h4>历史订单试匹配结果</h4>
      <el-alert v-if="trial.file_error" :title="trial.file_error.message" type="error" :closable="false"/>
      <template v-else>
        <p>样例采购行共 {{trial.total_rows}} 行 · 本次试匹配 {{trial.evaluated_rows}} 行 · 唯一产品匹配成功 {{trial.summary.matched_rows}} 行 · 未匹配 {{trial.summary.unmatched_rows}} 行 · 多产品歧义 {{trial.summary.ambiguous_rows}} 行 · 其他异常 {{trial.summary.other_rows}} 行。</p>
        <el-alert v-if="trial.truncated" :title="'为控制查询量，本次仅试匹配前 '+trial.row_limit+' 行；以上分类统计仅覆盖这些行，其余行未试匹配。'" type="warning" :closable="false"/>
        <p v-if="trial.summary.ambiguous_rows" class="field-help">当前条件对应多个我司产品，建议核实是否需要增加其他匹配字段。同产品的多条历史订单不视为歧义。</p>
        <p v-if="trial.summary.unmatched_rows" class="field-help">未找到符合规则的历史订单，请检查匹配字段、历史数据和筛选条件。</p>
        <el-table :data="trial.rows" max-height="420" row-key="row_number">
          <el-table-column prop="row_number" label="Excel 行" width="90"/>
          <el-table-column label="匹配字段及值" min-width="210"><template #default="{row}">{{matchValuesText(row.match_values)}}</template></el-table-column>
          <el-table-column label="试匹配结果" width="125"><template #default="{row}"><el-tag :type="row.result.status==='MATCHED'?'success':'warning'">{{trialStatusLabels[row.result.status]||row.result.status}}</el-tag></template></el-table-column>
          <el-table-column label="我司产品编码 / 名称" min-width="190"><template #default="{row}">{{row.result.matched_order?row.result.matched_order.product_code+' / '+row.result.matched_order.product_name:'—'}}</template></el-table-column>
          <el-table-column label="参考历史销售订单 / 单价" min-width="210"><template #default="{row}"><template v-if="row.result.matched_order">{{row.result.matched_order.order_no}} / {{row.result.matched_order.unit_price}}<el-tag v-if="row.result.matched_order.source_type==='SEED'" type="info" size="small">模拟历史</el-tag></template><span v-else>—</span></template></el-table-column>
          <el-table-column label="原因 / 有限候选" min-width="320"><template #default="{row}"><p>{{row.result.message}}</p><p v-if="row.result.error_fields.length">相关字段：{{errorFieldsText(row.result.error_fields)}}</p><p v-for="c in row.candidates" :key="c.id">候选：{{c.product_code}} / {{c.product_name}} · {{c.order_no}} · {{c.unit_price}} <span v-if="c.source_type==='SEED'">（模拟历史）</span></p></template></el-table-column>
        </el-table>
      </template>
      <p class="field-help">查询时间：{{new Date(trial.checked_at).toLocaleString()}}。结果仅供核对当前草稿，唯一产品不等于百分之百准确；模拟历史结果不是实际 OMS 准确率。正式导入仍重新查询历史并执行产品、价格、采购防重和建单校验，已有订单不会随试匹配变化。</p>
    </section>
    </el-form><template #footer><span class="muted">当前 V{{body.expected_version}} · 保存产生新版本，旧任务保持原配置</span><el-button :disabled="busy" @click="configOpen=false">取消</el-button><el-button type="primary" :loading="busy" :disabled="!legal" @click="save">确认保存正式配置</el-button></template>
  </el-dialog>
  <el-drawer v-model="historyOpen" :title="'配置版本 · '+(current?.customer_name||'')" size="65%"><p>每次正式保存保留配置快照；使用次数来自已记录该版本的导入任务。</p><el-table :data="current?.versions||[]"><el-table-column type="expand"><template #default="{row}"><pre class="version-json">{{JSON.stringify(row.snapshot,null,2)}}</pre></template></el-table-column><el-table-column prop="config_version" label="配置版本"/><el-table-column prop="rule_version" label="规则版本"/><el-table-column prop="task_count" label="导入任务数"/><el-table-column prop="created_at" label="版本留档时间" min-width="170"/></el-table></el-drawer>
</template>
<style scoped>
.preview-area{scroll-margin-top:20px;outline:none}.config-form{margin-top:20px}.config-form h3{margin:26px 0 12px}.config-grid{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-top:18px}.template-options{display:flex;flex-wrap:wrap;gap:10px;margin:18px 0}.template-options .el-button{margin:0}.version-json{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f7fa;padding:20px;margin:10px;font-size:12px} .el-dialog__footer .muted{margin-right:12px}@media(max-width:760px){.config-grid{grid-template-columns:1fr;gap:4px}}
</style>
