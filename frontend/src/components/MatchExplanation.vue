<script setup lang="ts">
import {ref,onMounted} from 'vue'
import {http,errorText} from '../api/client'
import {fieldLabels} from '../api/customers'
import type {RowDetail} from '../types/api'
const props=defineProps<{row:RowDetail}>(),emit=defineEmits<{close:[]}>()
const info=ref<{customer_id:string;config_version:number;rule_version:number;match_fields:string[];match_values:Record<string,string|null>;historical_quantity:{operator:string;value:string}|null;saved_row_status:string;saved_error:string|null;current_result:{status:string;message:string}|null;safety:string}>(),error=ref('')
onMounted(async()=>{try{info.value=(await http.get('/api/v1/imports/'+props.row.task_id+'/rows/'+props.row.id+'/match-explanation')).data.data}catch(e){error.value=errorText(e)}})
</script>
<template><el-drawer :model-value="true" title="检查匹配规则（只读）" size="min(560px,94vw)" @close="emit('close')"><el-alert v-if="error" :title="error" type="error" :closable="false"/><template v-if="info"><p>客户 {{info.customer_id}} · 原任务配置 V{{info.config_version}} / 规则 V{{info.rule_version}}</p><el-descriptions :column="1" border><el-descriptions-item v-for="f in info.match_fields" :key="f" :label="fieldLabels[f]||f">{{info.match_values[f]||'未提供'}}</el-descriptions-item><el-descriptions-item label="历史数量条件">{{info.historical_quantity?({'gt':'>','gte':'>=','eq':'='}[info.historical_quantity.operator]||info.historical_quantity.operator)+' '+info.historical_quantity.value:'无附加数量条件'}}</el-descriptions-item></el-descriptions><p>{{info.safety}}</p><el-alert :title="info.current_result?.message||'该行未标准化，先修正采购字段'" type="info" :closable="false"/><p>原处理状态：{{info.saved_row_status}}；原原因：{{info.saved_error||'无'}}</p><p>当前检查基于此刻历史库，可能与原处理时不同；不改写旧任务。无合格参考不等于数据库完全没有该产品。</p><router-link to="/customers">查看客户配置（修改仅影响后续任务）</router-link></template></el-drawer></template>
