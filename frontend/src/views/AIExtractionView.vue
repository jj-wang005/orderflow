<script setup lang="ts">
import {computed,onMounted,onBeforeUnmount,ref,watch,nextTick} from 'vue'
import {validateDraft} from '../utils/aiValidation'
import {useRoute,useRouter} from 'vue-router'
import {ElMessage,ElMessageBox} from 'element-plus'
import PageHeading from '../components/PageHeading.vue'
import {ai,type AIDraft,type AIResult,type AICapabilities,type AIRow,type AIField} from '../api/ai'
import {customerApi,fieldLabels} from '../api/customers'
import {errorText,ApiError} from '../api/client'
import {useEntryUnloadProtection} from '../composables/useEntryUnloadProtection'
defineProps<{embedded?:boolean}>()
const route=useRoute(),router=useRouter()
const capabilities=ref<AICapabilities>(),customers=ref<{customer_id:string;customer_name:string}[]>([])
const customer=ref(''),mode=ref('text'),input=ref(''),file=ref<File>(),authorized=ref(false)
const draft=ref<AIDraft>(),result=ref<AIResult>({rows:[],warnings:[]}),busy=ref(false),error=ref(''),dirty=ref(false),checked=ref(false),note=ref('')
const list=ref<AIDraft[]>([]),page=ref(1),total=ref(0),editKey=ref(crypto.randomUUID()),pendingConfirm=ref(false)
const attempted=ref(false),preview=ref<HTMLElement>()
const required=computed<AIField[]>(()=>draft.value?.required_fields||['quantity'])
const requiredText=computed(()=>required.value.map(f=>fieldLabels[f]).join('、'))
const issues=computed(()=>attempted.value?validateDraft(result.value,[...required.value]):[])
function fieldError(index:number,field:string){return issues.value.find(i=>i.row===index&&i.field===field)?.message||''}
async function locateError(){await nextTick();const cell=preview.value?.querySelector<HTMLElement>('[data-invalid="true"]');cell?.scrollIntoView?.({behavior:'smooth',block:'center',inline:'center'});cell?.querySelector('input')?.focus()}
function removeRow(index:number){(result.value.removed_rows ||= []).push(result.value.rows.splice(index,1)[0]!);changed()}
function restoreRow(index:number){result.value.rows.push(result.value.removed_rows!.splice(index,1)[0]!);changed()}
const editable=computed(()=>draft.value?.status==='DRAFT'&&!pendingConfirm.value&&!busy.value)
const fields=[['customer_order_no','采购单号'],['customer_material_code','客户料号'],['customer_model','机型'],['customer_batch','批次'],['quantity','数量']] as const
const labels:Record<string,string>={PARSING:'解析中',FAILED:'解析失败',DRAFT:'待核对确认',CONFIRMED:'已确认导入'}
const storageKey=(id:number)=>'cvte.ai.confirm.'+id
function receive(value:AIDraft){attempted.value=false;draft.value=value;result.value=structuredClone(value.edited_result||{rows:[],warnings:[]});customer.value=value.customer_id;dirty.value=false;checked.value=false;editKey.value=crypto.randomUUID();pendingConfirm.value=!!localStorage.getItem(storageKey(value.id))&&value.status!=='CONFIRMED';if(value.status==='CONFIRMED')localStorage.removeItem(storageKey(value.id))}
async function history(){try{const data=await ai.list(page.value);list.value=data.items;total.value=data.total}catch(e){error.value=errorText(e)}}
async function load(){if(!route.params.id){draft.value=undefined;pendingConfirm.value=false;return}busy.value=true;error.value='';try{receive(await ai.get(Number(route.params.id)))}catch(e){error.value=errorText(e)}finally{busy.value=false}}
// Cached views observe other pages too: only load explicit AI draft navigation.
watch(()=>[route.name,route.params.id,route.query.mode],()=>{
  if((route.name==='order-entry'&&route.query.mode==='ai'||route.path.startsWith('/ai'))&&route.params.id&&Number(route.params.id)!==draft.value?.id)void load()
})
useEntryUnloadProtection(()=>busy.value||dirty.value||pendingConfirm.value||!draft.value&&!!(input.value||file.value))
// Register for the lifetime of the cached view, including re-entry from tasks.
const removeDraftGuard=router.beforeEach(async to=>{
  if(!(to.name==='order-entry'&&to.query.mode==='ai'||to.path.startsWith('/ai'))||!to.params.id||Number(to.params.id)===draft.value?.id)return
  if(busy.value||pendingConfirm.value){ElMessage.warning('当前解析或确认结果待返回，请先完成原操作。');return false}
  if(dirty.value||!draft.value&&!!(input.value||file.value)){
    try{await ElMessageBox.confirm('打开其他草稿将替换当前未保存的输入或修改，是否继续？','切换解析草稿',{confirmButtonText:'打开草稿',cancelButtonText:'保留当前内容'})}catch{return false}
  }
})
onBeforeUnmount(removeDraftGuard)
onMounted(async()=>{try{const [caps,data]=await Promise.all([ai.capabilities(),customerApi.list()]);capabilities.value=caps;customers.value=data.filter(c=>c.enabled&&c.configured);await load();await history()}catch(e){error.value=errorText(e)}})
function pick(event:Event){file.value=(event.target as HTMLInputElement).files?.[0]}
async function parse(){
  if(!customer.value||mode.value==='file'&&!file.value||mode.value==='text'&&!input.value.trim()){error.value='请选择客户并提供输入';return}
  if(capabilities.value?.provider==='mimo'&&(!capabilities.value.external_enabled||!authorized.value)){error.value='请先确认本次输入获准对外发送，并由管理员启用 MiMo';return}
  busy.value=true;error.value=''
  try{
    const raw=mode.value==='text'?new TextEncoder().encode(input.value):await file.value!.arrayBuffer()
    const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',raw))).map(n=>n.toString(16).padStart(2,'0')).join('')
    const fingerprint=JSON.stringify([customer.value,mode.value,hash,file.value?.name||'',authorized.value])
    const stored=localStorage.getItem('cvte.ai.parse'),operation=stored?JSON.parse(stored):{key:crypto.randomUUID(),fingerprint}
    if(operation.fingerprint!==fingerprint)throw new Error('上次解析结果尚未确认，请恢复原输入重试，或在历史草稿中查到结果后点击开始新解析')
    localStorage.setItem('cvte.ai.parse',JSON.stringify(operation))
    const form=new FormData();form.set('customer_id',customer.value);form.set('request_key',operation.key);form.set('external_authorized',String(authorized.value))
    if(mode.value==='text')form.set('text',input.value);else form.set('file',file.value!)
    const value=await ai.create(form);localStorage.removeItem('cvte.ai.parse');receive(value);await router.replace('/ai/'+value.id);await history()
  }catch(e){if(e instanceof ApiError&&e.status&&e.status>=400&&e.status<500&&e.status!==409)localStorage.removeItem('cvte.ai.parse');error.value=errorText(e)}finally{busy.value=false}
}
function changed(){dirty.value=true;checked.value=false}
function add(){const source=draft.value?.source_manifest[0];if(!source)return;result.value.rows.push({customer_order_no:null,customer_material_code:null,customer_model:null,customer_batch:null,quantity:null,source_id:source.id,source_excerpt:source.text.slice(0,2000),extraction_warnings:['人工补充明细'],uncertain_fields:['quantity']});changed()}
function verify(row:AIRow){row.uncertain_fields=[];changed()}
function uncertainLabels(row:AIRow){return row.uncertain_fields.map(field=>fieldLabels[field]||field).join('、')}
async function save(){if(!draft.value)return;busy.value=true;error.value='';try{const submitted=JSON.parse(JSON.stringify(result.value)) as AIResult;for(const row of [...submitted.rows,...(submitted.removed_rows||[])])for(const [field] of fields)row[field]=row[field]?.trim()||null;receive(await ai.save(draft.value.id,{request_key:editKey.value,expected_revision:draft.value.revision,result:submitted,note:note.value||'销管对照原始输入核对并修正'}));ElMessage.success('预览修正已保存，尚未导入')}catch(e){error.value=errorText(e)}finally{busy.value=false}}
async function confirm(){
  if(!draft.value)return
  if(!pendingConfirm.value){attempted.value=true;if(!result.value.rows.length){error.value='至少保留一条采购明细';return}if(issues.value.length){await locateError();return}}
  if(dirty.value){error.value='请先保存预览修正，再核对确认';return}
  if(!checked.value&&!pendingConfirm.value){error.value='请先勾选已核对本次全部采购明细';return}
  const key=storageKey(draft.value.id)
  busy.value=true;error.value=''
  try{
    const existing=localStorage.getItem(key)
    const body=existing?JSON.parse(existing):{request_key:crypto.randomUUID(),expected_revision:draft.value.revision,confirmed:true,note:note.value||'销管已核对原始输入和采购明细，明确确认导入'}
    if(!existing)await ElMessageBox.confirm('确认后将按客户原规则匹配历史订单，并可能创建销售订单。','确认导入',{confirmButtonText:'确认导入',cancelButtonText:'返回核对'})
    localStorage.setItem(key,JSON.stringify(body));pendingConfirm.value=true
    receive(await ai.confirm(draft.value.id,body));await history();ElMessage.success('已确认，请查看导入任务结果')
  }catch(e){if(e instanceof ApiError&&e.status&&e.status>=400&&e.status<500){localStorage.removeItem(key);pendingConfirm.value=false;if(e.status===409)await load()}if(e!=='cancel'&&e!=='close')error.value=errorText(e)}finally{busy.value=false}
}
async function retry(){if(!draft.value)return;busy.value=true;error.value='';try{receive(await ai.retry(draft.value.id,{expected_revision:draft.value.revision,external_authorized:authorized.value}));await history()}catch(e){error.value=errorText(e)}finally{busy.value=false}}
async function fresh(){try{await ElMessageBox.confirm('请先在历史草稿中核实上次解析结果。开始新解析将更换解析请求标识；已有草稿和订单仍保留。','开始新解析',{confirmButtonText:'开始新解析',cancelButtonText:'返回'});localStorage.removeItem('cvte.ai.parse');draft.value=undefined;file.value=undefined;input.value='';error.value='';dirty.value=false;await router.push('/ai')}catch{}}
</script>
<template>
 <component :is="embedded?'div':PageHeading" :class="{'entry-workflow-actions':embedded}" :title="embedded?undefined:'智能订单解析'" eyebrow="INTELLIGENT EXTRACTION" subtitle="非标准 Excel、PDF、图片及采购文字，先核对明细，再确认导入。"><el-button @click="fresh" :disabled="busy">开始新解析</el-button></component>
 <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="section-alert"/>
 <section class="panel ai-section">
  <div class="panel-heading"><h2>{{draft?'解析草稿 #'+draft.id:'提供采购内容'}}</h2><el-tag>{{capabilities?.provider==='mock'?'Mock 演示 · 未调用 MiMo':capabilities?.model}}</el-tag></div>
  <el-form label-width="110px"><el-form-item label="所属客户"><el-select v-model="customer" :disabled="!!draft||busy" placeholder="手动选择客户" style="width:300px"><el-option v-for="c in customers" :key="c.customer_id" :value="c.customer_id" :label="c.customer_name+'（'+c.customer_id+'）'"/></el-select></el-form-item>
  <template v-if="!draft"><el-form-item label="输入方式"><el-radio-group v-model="mode" :disabled="busy"><el-radio-button value="text">采购文字</el-radio-button><el-radio-button value="file">上传文件</el-radio-button></el-radio-group></el-form-item>
   <el-form-item v-if="mode==='text'" label="采购文字"><el-input v-model="input" type="textarea" :rows="5" :maxlength="30000" :disabled="busy" placeholder="例如：X100 再订 200 台，X200 再订 300 台。"/></el-form-item>
   <el-form-item v-else label="采购文件"><input type="file" accept=".xlsx,.pdf,.jpg,.jpeg,.png" :disabled="busy" @change="pick"><span class="muted">单份 10 MiB；PDF 最多 {{capabilities?.max_pdf_pages||5}} 页；最多 100 条明细</span></el-form-item>
  </template>
  <el-form-item v-if="capabilities?.provider==='mimo'" label="数据使用确认"><el-checkbox v-model="authorized" :disabled="busy">我确认本次上传内容可以用于 AI 解析，不包含未经授权对外传输的客户保密信息。</el-checkbox><el-tooltip content="外部AI调用会将本次输入发送至小米MiMo。勾选不代替企业授权，请先确认客户保密要求；标准Excel本地解析无需此确认。"><el-button circle text aria-label="AI数据传输风险说明">?</el-button></el-tooltip><el-alert v-if="!capabilities.external_enabled" title="后端未启用外部调用，请联系管理员配置" type="warning" :closable="false"/></el-form-item>
  <el-form-item v-if="!draft"><el-button type="primary" :loading="busy" @click="parse">智能解析</el-button></el-form-item></el-form>
  <template v-if="draft"><div class="draft-meta"><el-tag>{{labels[draft.status]}}</el-tag><span>{{draft.file_name}} · 配置 V{{draft.config_version}} / 规则 V{{draft.rule_version}} · 修订 {{draft.revision}}</span><a :href="'/api/v1/ai-extractions/'+draft.id+'/source'">下载原始输入</a></div>
   <el-alert v-if="draft.error_message" :title="draft.error_message" type="error" :closable="false"/>
   <el-button v-if="draft.status==='FAILED'||draft.status==='PARSING'" @click="retry" :loading="busy">重试原草稿解析</el-button><span v-if="draft.status==='PARSING'" class="muted">请求进行中；失联超过三分钟后可重试</span>
   <el-button v-if="draft.import_task_id" type="primary" @click="router.push('/tasks/'+draft.import_task_id)">查看导入结果 · 任务 #{{draft.import_task_id}}</el-button>
  </template>
 </section>
 <template v-if="draft && ['DRAFT','CONFIRMED'].includes(draft.status)">
 <section ref="preview" class="panel ai-section"><div class="panel-heading"><h2>采购明细预览 · {{result.rows.length}} 行</h2><el-button v-if="editable" @click="add" :disabled="result.rows.length>=100">新增明细</el-button></div>
  <el-alert v-for="(warning,i) in result.warnings" :key="i" :title="warning" type="warning" :closable="false"/>
  <p class="muted">本草稿快照必填：{{requiredText}}。其余字段可留空；不完整草稿可保存，正式确认会严格校验。</p><el-alert v-if="attempted&&issues.length" :title="'有 '+issues.length+' 处待处理，请按行查看字段缺失、格式错误或AI识别风险'" type="error" :closable="false"/>
  <el-table :data="result.rows" class="ai-table"><el-table-column type="index" label="行" width="45"/><el-table-column v-for="[field,label] in fields" :key="field" :label="label+(required.includes(field)?' *':'')" :min-width="field==='customer_order_no'?240:190"><template #default="{row,$index}"><div class="ai-field" :data-invalid="!!fieldError($index,field)"><el-input v-model="row[field]" :aria-label="'第'+($index+1)+'行'+label" :disabled="!editable" :maxlength="field==='quantity'?32:128" @input="changed"/><p v-if="fieldError($index,field)" class="field-error">{{fieldError($index,field)}}</p></div></template></el-table-column>
   <el-table-column label="来源及待核实" min-width="290"><template #default="{row,$index}"><div :data-invalid="!!fieldError($index,'risk')"><p v-if="fieldError($index,'risk')" class="field-error">{{fieldError($index,'risk')}}</p></div><el-select v-model="row.source_id" :disabled="!editable" @change="changed"><el-option v-for="source in draft.source_manifest" :key="source.id" :value="source.id" :label="source.location"/></el-select><div>{{row.source_excerpt}}</div><div v-for="warning in row.extraction_warnings" :key="warning" class="muted">{{warning}}</div><el-tag v-if="row.uncertain_fields.length" type="warning">待核实：{{uncertainLabels(row)}}</el-tag><el-button v-if="editable&&row.uncertain_fields.length" link type="primary" @click="verify(row)">已对照原文核实此行</el-button></template></el-table-column>
   <el-table-column v-if="editable" label="操作" width="90" fixed="right"><template #default="{$index}"><el-button link type="danger" @click="removeRow($index)">移除此行</el-button></template></el-table-column>
  </el-table><el-collapse v-if="result.removed_rows?.length"><el-collapse-item :title="'已移除 '+result.removed_rows.length+' 行（确认时不处理，可恢复）'"><div v-for="(row,index) in result.removed_rows" :key="index">{{row.customer_order_no||row.customer_model||row.customer_material_code||'空明细'}} · {{row.quantity||'数量待填'}}<el-button v-if="editable" link type="primary" @click="restoreRow(index)">恢复此行</el-button></div></el-collapse-item></el-collapse>
  <template v-if="draft.status==='DRAFT'"><el-input v-model="note" placeholder="核对或修改说明（选填）" :disabled="pendingConfirm||busy" maxlength="500"/><div class="ai-actions"><el-button @click="save" :disabled="!editable||!dirty" :loading="busy">保存预览修正</el-button><el-checkbox v-model="checked" :disabled="dirty||busy||pendingConfirm">已核对原文、全部明细及数量，明确确认导入</el-checkbox><el-button type="primary" :disabled="busy||dirty||(!checked&&!pendingConfirm)" @click="confirm">{{pendingConfirm?'使用原请求继续确认':'确认导入'}}</el-button><span v-if="dirty" class="muted">尚有未保存修改</span></div><el-alert v-if="error" :title="error" type="error" :closable="false" class="confirm-error"/></template>
 </section>
 <section class="panel ai-section"><el-collapse><el-collapse-item title="原始内容与来源位置" name="source"><div v-for="source in draft.source_manifest" :key="source.id"><strong>{{source.location}}{{source.visual?' · 包含图像，请对照原始文件':''}}</strong><el-image v-if="source.visual" style="display:block;max-width:900px;max-height:500px" fit="contain" :src="'/api/v1/ai-extractions/'+draft.id+'/preview?source_id='+encodeURIComponent(source.id)" :preview-src-list="['/api/v1/ai-extractions/'+draft.id+'/preview?source_id='+encodeURIComponent(source.id)]"/><pre>{{source.text||'视觉来源：请核对上方图像及原始文件'}}</pre></div></el-collapse-item><el-collapse-item title="原始提取结果及操作记录" name="audit"><pre>{{JSON.stringify(draft.original_result,null,2)}}</pre><div v-for="(item,i) in draft.audit" :key="i">{{item.at}} · {{item.action}} {{item.note||''}}</div></el-collapse-item></el-collapse></section>
 </template>
 <section class="panel ai-section"><div class="panel-heading"><h2>历史解析草稿</h2><el-button @click="history">刷新</el-button></div><el-table :data="list"><el-table-column prop="id" label="草稿" width="85"/><el-table-column prop="customer_id" label="客户" width="100"/><el-table-column prop="file_name" label="原始输入"/><el-table-column label="状态"><template #default="{row}">{{labels[row.status]}}</template></el-table-column><el-table-column prop="created_at" label="创建时间"/><el-table-column label="操作"><template #default="{row}"><el-button link type="primary" @click="router.push('/ai/'+row.id)">查看草稿</el-button><el-button v-if="row.import_task_id" link type="primary" @click="router.push('/tasks/'+row.import_task_id)">导入结果</el-button></template></el-table-column></el-table><el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="total,prev,pager,next" @current-change="history"/></section>
</template>
<style scoped>
.field-error{font-size:12px;color:var(--el-color-danger);line-height:1.5;margin:6px 0}.ai-field[data-invalid="true"] :deep(.el-input__wrapper){box-shadow:0 0 0 1px var(--el-color-danger) inset}.ai-section{padding:22px;margin-bottom:20px}.ai-table{margin:16px 0}.draft-meta,.ai-actions{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin:16px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:350px;overflow:auto}.el-alert{margin:8px 0}.muted{font-size:12px}
</style>
