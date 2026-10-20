<script setup lang="ts">
import {ref} from 'vue'
import {ElMessageBox,ElMessage} from 'element-plus'
import {api} from '../api/orders'
import {ApiError,errorText} from '../api/client'
import type {OrderDetail} from '../types/api'
const props=defineProps<{order:OrderDetail}>(),emit=defineEmits<{close:[];saved:[]}>()
const note=ref(''),busy=ref(false),error=ref('')
const key='cvte.void.'+props.order.id
const pending=ref<{request_key:string;expected_revision:number;confirmed:true;note:string}|null>(null)
try{pending.value=JSON.parse(localStorage.getItem(key)||'null')}catch{error.value='作废重试记录不可读，请核对订单结果后重试'}
async function save(){
 if(!pending.value&&!note.value.trim()){error.value='请填写作废原因';return}
 busy.value=true;error.value=''
 try{
  if(!pending.value){await ElMessageBox.confirm('作废后不可恢复、编辑或继续流转。采购来源占用将保留，确认作废？','二次确认作废',{type:'warning',confirmButtonText:'确认作废',cancelButtonText:'返回'});const body={request_key:crypto.randomUUID(),expected_revision:props.order.edit_revision,confirmed:true as const,note:note.value.trim()};localStorage.setItem(key,JSON.stringify(body));pending.value=body}
  await api.voidOrder(props.order.id,pending.value);localStorage.removeItem(key);ElMessage.success('订单已作废，来源及审计记录保留');emit('saved');emit('close')
 }catch(e){if(e!=='cancel'&&e!=='close'){error.value=errorText(e);if(e instanceof ApiError&&[404,409,422].includes(e.status||0)){localStorage.removeItem(key);pending.value=null}}}finally{busy.value=false}
}
</script>
<template><el-dialog :model-value="true" title="作废订单" width="min(540px,94vw)" :close-on-click-modal="false" :before-close="()=>{if(!busy)emit('close')}" :show-close="!busy"><p>{{order.order_no}}</p><el-alert title="仅已创建的业务订单可作废。操作不可恢复，不释放采购来源。" type="warning" :closable="false"/><el-form label-position="top"><el-form-item label="作废原因（必填）" :error="error"><el-input v-model="note" type="textarea" :rows="3" maxlength="500" :disabled="busy||!!pending"/></el-form-item></el-form><p v-if="pending">原作废原因：{{pending.note}}。网络重试沿用原操作。</p><template #footer><el-button :disabled="busy" @click="emit('close')">关闭</el-button><el-button type="danger" :loading="busy" @click="save">{{pending?'重试原作废请求':'作废订单'}}</el-button></template></el-dialog></template>
