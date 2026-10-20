<script setup lang="ts">
import { ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, ArrowRight, Link, Document } from '@element-plus/icons-vue'
import { api } from '../api/orders'
import { errorText } from '../api/client'
import type { OrderDetail } from '../types/api'
import { positiveId, decimal, price, money, dateTime } from '../utils/format'
import { sourceLabel } from '../utils/status'
import PageHeading from '../components/PageHeading.vue'
import OrderStatusEditor from '../components/OrderStatusEditor.vue'
import type {OrderEditContext} from '../types/api'
const changeHistory=ref<OrderEditContext['history']>([])
import OrderOperationDialog from '../components/OrderOperationDialog.vue'
import {ElMessage} from 'element-plus'
const editing = shallowRef<OrderDetail | null>(null)
import StatusBadge from '../components/StatusBadge.vue'
import VoidOrderDialog from '../components/VoidOrderDialog.vue'
import {orderReadOnlyReason} from '../utils/orderStatus'
import {download} from '../utils/download'
const voiding=ref(false),exporting=ref(false)
async function exportPDF(){if(!order.value)return;exporting.value=true;try{await download('/api/v1/orders/'+order.value.id+'/report.pdf','销售订单-'+order.value.id+'.pdf')}catch(e){error.value=errorText(e)}finally{exporting.value=false}}
const route = useRoute(), router = useRouter(), order = shallowRef<OrderDetail | null>(null), loading = ref(false), error = ref('')
let sequence = 0
async function load() {
  const token = ++sequence; order.value = null; error.value = ''; const id = positiveId(route.params.id)
  if (!id) { loading.value = false; error.value = '订单编号不合法，请输入正整数。'; return }
  loading.value = true
  try { const [result, context] = await Promise.all([api.order(id), api.editContext(id)]); if (token === sequence) { order.value = result; changeHistory.value = context.history } }
  catch (e) { if (token === sequence) error.value = errorText(e) }
  finally { if (token === sequence) loading.value = false }
}
watch(() => route.params.id, load, { immediate: true })
</script>
<template><PageHeading title="订单详情" eyebrow="ORDER DETAIL" subtitle="核查我司产品、采购数量和金额，追溯这张销售订单的历史参考来源。"><el-button :icon="ArrowLeft" @click="router.push('/orders')">返回订单列表</el-button><el-button v-if="order && !orderReadOnlyReason(order)" type="primary" @click="editing=order">编辑</el-button><el-button v-if="order" :loading="exporting" @click="exportPDF">导出 PDF</el-button><el-button v-if="order && !orderReadOnlyReason(order)" type="danger" plain @click="voiding=true">作废订单</el-button></PageHeading><el-alert v-if="error" :title="error" type="error" show-icon :closable="false" /><el-skeleton v-if="loading" :rows="10" animated class="panel skeleton-panel" /><template v-else-if="order"><section class="panel order-hero"><div class="order-title"><span class="order-symbol"><el-icon><Document /></el-icon></span><div><p>{{ order.creation_description || sourceLabel(order.source_type,order.is_simulated_manual) }} · #{{ order.id }}</p><h2>{{ order.order_no }}</h2></div><StatusBadge :status="order.status" :description="order.status_description" /></div><el-alert v-if="orderReadOnlyReason(order)" :title="orderReadOnlyReason(order)" type="info" :closable="false"/><div class="order-metrics"><div><label>{{ order.source_type === 'SEED' ? '历史订单数量' : '本次采购数量' }}</label><strong>{{ decimal(order.quantity) }}</strong></div><div><label>销售单价</label><strong>{{ price(order.unit_price) }}</strong></div><div><label>订单金额</label><strong>{{ money(order.amount) }}</strong></div></div><OrderStatusEditor :key="order.id" :order="order" @saved="load" @refresh="load" /><el-collapse><el-collapse-item title="查看产品价格来源说明"><p>{{order.price_description}}</p></el-collapse-item></el-collapse></section><div class="order-detail-grid"><section class="panel"><div class="panel-heading"><h2>产品与客户信息</h2></div><el-descriptions :column="1" border><el-descriptions-item label="我司产品编码">{{ order.product_code }}</el-descriptions-item><el-descriptions-item label="我司产品名称">{{ order.product_name }}</el-descriptions-item><el-descriptions-item label="客户">{{ order.customer_id }} 客户</el-descriptions-item><el-descriptions-item label="客户采购单号">{{ order.customer_order_no || '—' }}</el-descriptions-item><el-descriptions-item label="客户料号">{{ order.customer_material_code || '—' }}</el-descriptions-item><el-descriptions-item label="客户批次">{{ order.customer_batch || '—' }}</el-descriptions-item><el-descriptions-item label="客户机型">{{ order.customer_model || '—' }}</el-descriptions-item></el-descriptions></section><section class="panel"><div class="panel-heading"><h2><el-icon><Link /></el-icon> 订单来源追溯</h2></div><el-descriptions :column="1" border><el-descriptions-item label="来源类型">{{ order.creation_description || sourceLabel(order.source_type,order.is_simulated_manual) }}（{{ order.source_type }}）</el-descriptions-item><el-descriptions-item label="历史参考订单"><el-button v-if="order.source_order_id" link type="primary" :icon="ArrowRight" @click="router.push('/orders/' + order.source_order_id)">查看历史订单 #{{ order.source_order_id }}</el-button><span v-else>无历史参考（手动订单或历史单本身）</span></el-descriptions-item><el-descriptions-item label="导入任务"><el-button v-if="order.import_task_id" link type="primary" @click="router.push('/tasks/' + order.import_task_id)">任务 #{{ order.import_task_id }}</el-button><span v-else>—</span></el-descriptions-item><el-descriptions-item label="Excel 工作表">{{ order.source_sheet_name || '—' }}</el-descriptions-item><el-descriptions-item label="Excel 行号">{{ order.source_row_number ?? '—' }}</el-descriptions-item><el-descriptions-item label="创建时间">{{ dateTime(order.created_at) }}</el-descriptions-item><el-descriptions-item label="订单状态">{{ order.status_description }}（{{ order.status }}）</el-descriptions-item></el-descriptions><div class="trace-note">{{ order.source_type === 'SEED' ? '这是模拟历史参考，数量表示历史销售数量。仅符合业务规则的有效历史单才可用于录单。' : '有历史来源时须保持参考可靠；手动新单无历史来源。编辑后当前数量和金额可能与最初采购快照不同，修改记录可追溯；金额由后端计算。' }}</div><el-button v-if="order.import_task_id" type="primary" plain class="trace-button" @click="router.push('/rows/' + order.import_task_id)">查看来源采购行</el-button></section></div></template><section v-if="order && changeHistory.length" class="panel"><h3>订单变更记录</h3><el-collapse><el-collapse-item v-for="h in changeHistory" :key="h.id" :title="h.created_at+' · '+h.note"><p>{{h.actor_label}}</p><p>状态：{{h.before_data?.status || '新建'}} → {{h.after_data.status}}</p><pre class="json-view">{{JSON.stringify({修改前:h.before_data,修改后:h.after_data,核对依据:h.payload.verification_basis},null,2)}}</pre></el-collapse-item></el-collapse></section><VoidOrderDialog v-if="voiding && order" :order="order" @close="voiding=false" @saved="load"/><OrderOperationDialog v-if="editing" :key="editing.id" mode="edit" :order-id="editing.id" @close="editing=null" @saved="ElMessage.success('保存成功，金额 '+money($event.order.amount)); load()" /></template>
