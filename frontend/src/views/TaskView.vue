<script setup lang="ts">
import {ref,shallowRef,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {api} from '../api/orders'
import {importApi} from '../api/imports'
import {errorText} from '../api/client'
import {positiveId,dateTime} from '../utils/format'
import {rememberTask} from '../utils/storage'
import type {TaskSummary} from '../types/api'
import PageHeading from '../components/PageHeading.vue'
import TaskStats from '../components/TaskStats.vue'
import StatusBadge from '../components/StatusBadge.vue'
import RowsView from './RowsView.vue'
const route=useRoute(),router=useRouter(),task=shallowRef<TaskSummary|null>(null),loading=ref(false),error=ref(''),reload=ref(0),related=ref<{parent:TaskSummary|null;retries:TaskSummary[];ai_extraction_id?:number|null}>({parent:null,retries:[]})
let sequence=0
async function load(){const token=++sequence,id=positiveId(route.params.id);error.value='';task.value=null;if(!id){error.value='任务编号不合法，请从历史列表重新进入。';return}loading.value=true;try{const [value,links]=await Promise.all([api.task(id),importApi.related(id)]);if(token===sequence){task.value=value;related.value=links;rememberTask(value);reload.value++}}catch(e){if(token===sequence)error.value=errorText(e)}finally{if(token===sequence)loading.value=false}}
watch(()=>route.params.id,load,{immediate:true})
</script>
<template><PageHeading title="导入任务详情" eyebrow="IMPORT DETAIL" subtitle="查看文件、配置版本、错误原因与采购行处理结果。"><el-button @click="router.push('/tasks')">返回历史任务列表</el-button><el-button :disabled="loading" @click="load">刷新任务</el-button></PageHeading><el-alert v-if="error" :title="error" type="error" :closable="false" class="section-alert"/><el-skeleton v-if="loading" :rows="8" animated class="panel"/>
<template v-else-if="task"><el-alert :title="task.error_message&&task.total_rows===0?'文件解析失败：没有采购明细，请修复重传':'已解析 '+task.total_rows+' 行；未匹配行可人工补全，待核实行请确认是否重复采购'" :type="task.error_message&&task.total_rows===0?'error':'info'" :closable="false"/><TaskStats :task="task"/><section class="panel"><div class="panel-heading"><div><h2>任务 #{{task.task_id}}</h2><p>{{task.file_name}}</p></div><StatusBadge :status="task.status" :description="task.status_description"/></div><el-descriptions :column="2" border><el-descriptions-item label="客户">{{task.customer_id}}</el-descriptions-item><el-descriptions-item label="文件名称">{{task.file_name}}</el-descriptions-item><el-descriptions-item label="上传时间">{{dateTime(task.created_at||task.started_at)}}</el-descriptions-item><el-descriptions-item label="完成时间">{{dateTime(task.finished_at)}}</el-descriptions-item><el-descriptions-item label="配置版本">V{{task.config_version??'—'}}</el-descriptions-item><el-descriptions-item label="规则版本">V{{task.rule_version??'—'}}</el-descriptions-item><el-descriptions-item label="重复采购行">{{task.duplicate_rows||0}}</el-descriptions-item><el-descriptions-item label="待核实采购行">{{task.review_rows||0}}</el-descriptions-item><el-descriptions-item label="批次">{{task.batch_id||'原单文件任务'}}</el-descriptions-item><el-descriptions-item label="错误摘要">{{task.error_summary||'无异常摘要'}}</el-descriptions-item></el-descriptions>
<el-alert v-if="task.error_message" :title="task.error_message" type="error" :closable="false" class="section-alert"/>
<div v-if="related.ai_extraction_id" class="result-actions"><el-button type="primary" link @click="router.push('/ai/'+related.ai_extraction_id)">查看原始智能解析、人工修正及确认记录 · 草稿 #{{related.ai_extraction_id}}</el-button></div>
<div v-if="related.parent||related.retries.length" class="result-actions"><el-button v-if="related.parent" link type="primary" @click="router.push('/tasks/'+related.parent.task_id)">原失败任务 #{{related.parent.task_id}}</el-button><el-button v-for="t in related.retries" :key="t.task_id" link type="primary" @click="router.push('/tasks/'+t.task_id)">修复重传任务 #{{t.task_id}}</el-button></div>
<div class="result-actions"><el-button v-if="task.status==='FAILED'&&task.error_message&&task.total_rows===0" type="warning" @click="router.push({path:'/import',query:{retry_of:task.task_id}})">修复文件并重新上传</el-button><el-button @click="router.push('/import')">返回批量导入</el-button></div><el-empty v-if="!task.total_rows" description="此任务没有实际采购明细；文件级错误保留在上方。" :image-size="70"/></section><RowsView v-if="task.total_rows" :key="task.task_id+'-'+reload" embedded @changed="load"/></template></template>
