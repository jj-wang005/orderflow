<script setup lang="ts">
import {ref,shallowRef,watch,computed,onMounted} from 'vue'
import {api} from '../api/orders'
import {ApiError,errorText} from '../api/client'
import {orderStates,nextState} from '../utils/orderStatus'
import type {OrderDetail,OrderStatus,StatusRequest} from '../types/api'
const props=defineProps<{order:OrderDetail}>(),emit=defineEmits<{saved:[OrderDetail];refresh:[]}>()
const selected=ref<OrderStatus>(props.order.status as OrderStatus),busy=ref(false),error=ref(''),blocked=ref(false),pending=shallowRef<StatusRequest|null>(null)
const editable=computed(()=>props.order.source_type==='AUTO' && orderStates.some(x=>x.value===props.order.status))
const storageKey=`cvte:order-status:${props.order.id}`
watch(()=>[props.order.status,props.order.edit_revision],()=>{if(!pending.value)selected.value=props.order.status as OrderStatus})
onMounted(()=>{try{const raw=localStorage.getItem(storageKey);if(raw){const b=JSON.parse(raw) as StatusRequest;if(typeof b.request_key!=='string'||!Number.isSafeInteger(b.expected_revision)||!orderStates.some(s=>s.value===b.status))throw new Error();pending.value=b;selected.value=b.status}}catch{blocked.value=true;error.value='无法读取状态重试记录，请先核查服务器结果'}})
async function save(){
 if(busy.value||blocked.value||!editable.value)return
 if(!pending.value){pending.value={request_key:crypto.randomUUID(),expected_revision:props.order.edit_revision,status:selected.value};try{localStorage.setItem(storageKey,JSON.stringify(pending.value))}catch{pending.value=null;blocked.value=true;error.value='无法保存重试标识，尚未提交';return}}
 busy.value=true;error.value=''
 try{const result=await api.statusOrder(props.order.id,pending.value);localStorage.removeItem(storageKey);pending.value=null;emit('saved',result.order)}
 catch(e){error.value=errorText(e);if(e instanceof ApiError&&[404,409,422].includes(e.status||0)){try{localStorage.removeItem(storageKey);pending.value=null;selected.value=props.order.status as OrderStatus;emit('refresh')}catch{blocked.value=true}}}
 finally{busy.value=false}
}
</script>
<template><div class="order-status-editor"><template v-if="editable"><el-select v-model="selected" aria-label="订单状态" style="width:125px" :disabled="busy||!!pending||blocked"><el-option v-for="s in orderStates" :key="s.value" :label="s.label" :value="s.value" :disabled="s.value!==order.status&&s.value!==nextState[order.status as OrderStatus]" /></el-select><el-button type="primary" plain :loading="busy" :disabled="blocked||(!pending&&selected===order.status)" @click="save">{{pending?'重试':'保存'}}</el-button></template><span v-else>{{order.status_description}}</span><p v-if="error" class="status-error">{{error}}</p></div></template>
<style scoped>.order-status-editor{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.status-error{color:var(--el-color-danger);font-size:12px;margin:0;width:100%}</style>
