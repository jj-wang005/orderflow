<script setup lang="ts">
import {ref,onMounted,computed,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {ElMessageBox,type UploadFile} from 'element-plus'
import {UploadFilled,RefreshRight} from '@element-plus/icons-vue'
import PageHeading from '../components/PageHeading.vue'
import StatusBadge from '../components/StatusBadge.vue'
import {useCustomers} from '../composables/useCustomers'
import {useBatchImport} from '../composables/useBatchImport'
import {api} from '../api/orders'
import type {TaskSummary} from '../types/api'
import {readOperation} from '../utils/storage'
import {useEntryUnloadProtection} from '../composables/useEntryUnloadProtection'
defineProps<{embedded?:boolean}>()
const route=useRoute(),router=useRouter(),{customers,customerError}=useCustomers(),batch=useBatchImport()
const {entries,busy,error,canSubmit,summary}=batch
const unified=ref(''),retry=ref<TaskSummary|null>(null),retryError=ref(''),oldPending=ref(false)
const retryId=computed(()=>Number(route.query.retry_of))
useEntryUnloadProtection(()=>busy.value||entries.value.some(e=>!e.task&&!e.cancelled))
async function loadRetry(){
  retry.value=null;retryError.value=''
  if(!retryId.value)return
  try{
    retry.value=await api.task(retryId.value)
    if(!entries.value.some(e=>e.retry_of_task_id===retryId.value)){
      if(entries.value.some(e=>!e.task&&!e.cancelled))retryError.value='当前批次仍有未完成文件，请先处理原批次，再进行文件修复重传。'
      else batch.startNew()
    }
    if(!retry.value.error_message||retry.value.total_rows!==0||retry.value.status!=='FAILED')retryError.value='该任务不属于文件级失败，请回原采购行处理异常。'
  }catch{retryError.value='原失败任务无法读取，请从历史任务列表重新进入。'}
}
watch(()=>route.name==='order-entry'?route.query.retry_of:undefined,()=>{if(route.name==='order-entry')void loadRetry()})
onMounted(async()=>{if(entries.value.some(e=>e.task))await batch.refreshResults();try{const old=readOperation();oldPending.value=!!old&&['sending','uncertain'].includes(old.status)}catch{oldPending.value=true}await loadRetry()})
async function choose(upload:UploadFile){if(upload.raw&&!retryError.value)await batch.addFiles([upload.raw],retry.value||undefined)}
async function newBatch(){try{await ElMessageBox.confirm('确认开始新的批次？仍需为每份文件指定客户，已有结果可从历史任务查询。','新建批次',{confirmButtonText:'新建批次',cancelButtonText:'保留当前批次'});batch.startNew()}catch{}}
</script>
<template>
<component :is="embedded?'div':PageHeading" :class="{'entry-workflow-actions':embedded}" :title="embedded?undefined:'批量订单导入'" eyebrow="BATCH IMPORT" subtitle="一次选择多份标准 Excel，每份手动指定客户；每个文件独立任务，失败互不影响。"><el-button @click="router.push('/tasks')">浏览历史任务</el-button><el-button v-if="entries.some(e=>e.task)" :disabled="busy" @click="batch.refreshResults">刷新本批结果</el-button><el-button :disabled="busy" :icon="RefreshRight" @click="newBatch">新建批次</el-button></component>
<el-alert v-if="customerError||error||retryError" :title="customerError||error||retryError" type="error" :closable="false" class="section-alert"/>
<el-alert v-if="oldPending" title="浏览器保留了单文件导入记录，请先确认原操作结果。" type="warning" :closable="false" class="section-alert"><el-button link type="primary" @click="router.push('/import/single')">恢复原单文件操作</el-button></el-alert>
<el-alert v-if="retry" :title="'修复重传：任务 #'+retry.task_id+' · '+retry.file_name" type="info" :closable="false" class="section-alert"><p>原失败任务和错误原因保留；新文件将创建关联任务，客户沿用已核实的 {{retry.customer_id}}。</p></el-alert>
<section class="panel"><div class="panel-heading"><div><h2>准备采购文件</h2><p>最多 20 份，每份 10 MiB，每批合计 100 MiB。系统不根据文件名猜客户。</p></div></div>
<el-upload :key="batch.batchId.value" drag :multiple="!retryId" :auto-upload="false" :show-file-list="false" accept=".xlsx" :disabled="busy||!!retryError||batch.blocked.value" :on-change="choose"><el-icon><UploadFilled/></el-icon><p>拖入文件或点击选择多份 Excel</p><small>选择文件不会自动创建任务或订单</small></el-upload>
<div class="unified"><label>统一指定客户</label><el-select v-model="unified" placeholder="手动选择客户" :disabled="busy||!!retryId" style="width:260px"><el-option v-for="c in customers" :key="c.customer_id" :value="c.customer_id" :label="c.customer_name+'（'+c.customer_id+'）'" :disabled="!c.enabled||!c.configured"/></el-select><el-button :disabled="busy||!unified||!!retryId" @click="batch.assign(unified)">应用到未提交文件</el-button></div>
<el-table :data="entries" row-key="slot_id" class="data-table"><el-table-column prop="file_name" label="采购文件" min-width="200" show-overflow-tooltip/><el-table-column label="所属客户（每份必选）" min-width="210"><template #default="{row}"><el-select :model-value="row.customer_id" :disabled="busy||row.sent||!!row.task||!!row.retry_of_task_id" placeholder="请选择客户" @change="batch.setCustomer(row,$event)"><el-option v-for="c in customers" :key="c.customer_id" :value="c.customer_id" :label="c.customer_name+'（'+c.customer_id+'）'" :disabled="!c.enabled||!c.configured"/></el-select></template></el-table-column>
<el-table-column label="处理结果" min-width="240"><template #default="{row}"><template v-if="row.task"><StatusBadge :status="row.task.status" :description="row.task.status_description"/><p>新建 {{row.task.success_rows}} · 未匹配 {{row.task.unmatched_rows}} · 失败 {{row.task.failed_rows}} · 重复 {{row.task.duplicate_rows||0}} · 待核实 {{row.task.review_rows||0}}</p><p v-if="row.task.error_message" class="file-error">{{row.task.error_message}}</p></template><span v-else-if="row.cancelled">已取消，未提交</span><span v-else-if="!row.file">请重新选择原文件以恢复此操作</span><span v-else-if="row.error">{{row.error.message}}</span><span v-else>{{row.sent?'结果待确认，请沿用原操作重试':'待提交'}}</span></template></el-table-column>
<el-table-column label="操作" min-width="290"><template #default="{row}"><el-button v-if="row.task" link type="primary" @click="router.push('/tasks/'+row.task.task_id)">查看本次结果</el-button><template v-else-if="row.error?.code==='DUPLICATE_FILE'&&!row.cancelled"><el-button v-if="row.previous" link type="primary" @click="router.push('/tasks/'+row.previous.task_id)">查看上次结果</el-button><el-button link :disabled="busy" @click="batch.cancel(row)">取消</el-button><el-button type="warning" size="small" :disabled="busy||!canSubmit" @click="batch.confirmAgain(row)">确认再次导入</el-button></template><el-button v-else-if="!row.sent&&!row.cancelled" link :disabled="busy" @click="batch.cancel(row)">取消此文件</el-button></template></el-table-column></el-table>
<div class="batch-submit"><span class="muted">每份文件使用客户已保存的配置快照。再次导入仍需经过后端采购防重。</span><el-button type="primary" :loading="busy" :disabled="!canSubmit||!!retryError" @click="batch.execute">{{entries.some(e=>e.sent&&!e.task&&!e.cancelled)?'沿用原操作继续处理':'开始批量导入'}}</el-button></div>
</section>
<section v-if="entries.length" class="panel batch-summary"><h2>本批结果汇总</h2><p>文件 {{summary.files}} · 已返回任务 {{summary.completed}} · 已取消 {{summary.cancelled}} · 文件级错误 {{summary.fileErrors}}</p><p>新建订单 {{summary.success}} · 未匹配 {{summary.unmatched}} · 行失败 {{summary.failed}} · 已关联重复采购 {{summary.duplicate}} · 待人工核实 {{summary.review}}</p><p class="muted">文件级失败不虚构采购行；异常行与待核实行请进入对应任务继续处理。</p></section>
</template>
<style scoped>.unified{display:flex;align-items:center;gap:14px;margin:24px 0}.batch-submit{display:flex;justify-content:space-between;gap:20px;align-items:center;margin-top:24px}.batch-summary{margin-top:24px}.file-error{color:var(--el-color-danger);font-size:12px}.batch-summary p{line-height:1.9}</style>
