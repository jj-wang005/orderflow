<script setup lang="ts">
import { ref, shallowRef, watch } from 'vue'
import {useCustomers} from '../composables/useCustomers'
const {customers,customerError}=useCustomers()
import { useRoute, useRouter } from 'vue-router'
import { RefreshRight, Search, Plus, Tickets } from '@element-plus/icons-vue'
import { api } from '../api/orders'
import { errorText } from '../api/client'
import type { CustomerId, OrderDetail } from '../types/api'
import { decimal, money, price, dateTime } from '../utils/format'
import { sourceLabel } from '../utils/status'
import PageHeading from '../components/PageHeading.vue'
import OrderStatusEditor from '../components/OrderStatusEditor.vue'
import {orderFilterStates,orderReadOnlyReason} from '../utils/orderStatus'
import VoidOrderDialog from '../components/VoidOrderDialog.vue'
import {download} from '../utils/download'
const voiding=shallowRef<OrderDetail|null>(null),dates=ref<string[]>([]),exporting=ref(false)
let applied:Record<string,unknown>={}
async function exportExcel(){exporting.value=true;try{await download('/api/v1/orders/export.xlsx','销售订单.xlsx',applied)}catch(e){error.value=errorText(e)}finally{exporting.value=false}}

const status=ref('')
import OrderOperationDialog from '../components/OrderOperationDialog.vue'
import {ElMessage} from 'element-plus'
const editing = shallowRef<OrderDetail | null>(null)
const route = useRoute(), router = useRouter()
const customer = ref<CustomerId | ''>(''), source = ref<'SEED' | 'AUTO' | ''>('')
const orders = shallowRef<OrderDetail[]>([]), page = ref(1), pageSize = ref(20), total = ref(0), loading = ref(false), error = ref('')
let sequence = 0
async function load() {
  const token = ++sequence; loading.value = true; error.value = ''
  try { const filters={customer_id:customer.value||undefined,source_type:source.value||undefined,status:status.value||undefined,date_from:dates.value?.[0],date_to:dates.value?.[1]}; const result=await api.orders({page:page.value,page_size:pageSize.value,...filters});if(token===sequence){orders.value=result.items;total.value=result.total;applied=filters} }
  catch (e) { if (token === sequence) error.value = errorText(e) }
  finally { if (token === sequence) loading.value = false }
}
function search() { page.value = 1; router.replace({ path: '/orders', query: { ...(customer.value ? { customer: customer.value } : {}), ...(source.value ? { source: source.value } : {}),...(status.value?{status:status.value}:{}),...(dates.value?.length?{from:dates.value[0],to:dates.value[1]}:{}) } }); load() }
function reset() { customer.value = ''; source.value = ''; status.value=''; dates.value=[]; search() }
watch(() => [route.query.customer, route.query.source,route.query.status,route.query.from,route.query.to], () => {
  customer.value = typeof route.query.customer === 'string' ? route.query.customer : ''
  source.value = ['SEED','AUTO'].includes(String(route.query.source)) ? route.query.source as 'SEED' | 'AUTO' : ''
  status.value=orderFilterStates.some(s=>s.value===route.query.status)?String(route.query.status):''
  dates.value=route.query.from&&route.query.to?[String(route.query.from),String(route.query.to)]:[]
  page.value = 1; load()
}, { immediate: true })
</script>
<template>
  <PageHeading title="销售订单" eyebrow="SALES ORDERS" subtitle="查看销售订单、管理状态并追溯来源，订单金额以系统保存记录为准。"><el-button type="primary" :icon="Plus" @click="router.push('/order-entry')">新建录单</el-button><el-button :icon="Tickets" @click="router.push('/tasks')">查看历史任务</el-button></PageHeading>
  <form class="query-bar order-query" @submit.prevent="search"><label>客户</label><el-select v-model="customer" placeholder="全部客户" clearable style="width:160px" aria-label="筛选客户"><el-option v-for="c in customers" :key="c.customer_id" :label="c.customer_name" :value="c.customer_id" /></el-select><label>订单状态</label><el-select v-model="status" clearable placeholder="全部状态" aria-label="筛选订单状态" style="width:160px"><el-option v-for="s in orderFilterStates" :key="s.value" :label="s.label" :value="s.value" /></el-select><el-date-picker v-model="dates" type="daterange" value-format="YYYY-MM-DD" start-placeholder="创建开始日期" end-placeholder="创建结束日期" aria-label="创建日期范围"/><div class="filter-actions"><el-button @click="reset">重置</el-button><el-button type="primary" :icon="Search" native-type="submit" :disabled="loading">查询</el-button></div></form>
  <el-alert v-if="customerError" :title="customerError" type="error" :closable="false" class="section-alert" />
  <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="section-alert" />
  <section class="panel table-panel" v-loading="loading"><div class="panel-heading"><div><h2>销售订单列表 <span class="count-pill">{{ total }}</span></h2><p>查看订单状态、客户与来源；确认后订单可作为后续采购参考。</p></div><div class="list-actions"><el-button :loading="exporting" :disabled="loading" @click="exportExcel">导出 Excel</el-button><el-button :icon="RefreshRight" :loading="loading" @click="load">刷新列表</el-button></div></div><el-table :data="orders" row-key="id" max-height="640" :empty-text="error ? '当前数据无法加载，请检查服务连接' : '当前筛选条件下暂无销售订单'" class="data-table"><el-table-column prop="id" label="订单 ID" width="95" fixed /><el-table-column prop="order_no" label="业务订单编号" min-width="260" show-overflow-tooltip /><el-table-column prop="customer_id" label="客户" width="75" /><el-table-column prop="customer_order_no" label="客户单号" min-width="150" show-overflow-tooltip><template #default="{ row }">{{ row.customer_order_no || '—' }}</template></el-table-column><el-table-column prop="customer_material_code" label="客户料号" min-width="175" show-overflow-tooltip /><el-table-column prop="product_code" label="我司产品编码" min-width="190" show-overflow-tooltip /><el-table-column prop="product_name" label="我司产品名称" min-width="240" show-overflow-tooltip /><el-table-column label="当前销售单价" width="125" align="right"><template #default="{ row }"><span class="numeric">{{ price(row.unit_price) }}</span></template></el-table-column><el-table-column label="数量" width="115" align="right"><template #default="{ row }"><span class="numeric">{{ decimal(row.quantity) }}</span></template></el-table-column><el-table-column label="订单金额" width="140" align="right"><template #default="{ row }"><strong class="numeric">{{ money(row.amount) }}</strong></template></el-table-column><el-table-column label="来源类型" width="135"><template #default="{ row }"><el-tag :type="row.source_type === 'AUTO' ? 'primary' : 'info'" effect="light">{{ row.creation_description || sourceLabel(row.source_type,row.is_simulated_manual) }}</el-tag></template></el-table-column><el-table-column label="创建时间" width="180"><template #default="{ row }">{{ dateTime(row.created_at) }}</template></el-table-column><el-table-column label="订单状态" width="245"><template #default="{row}"><OrderStatusEditor :key="row.id" :order="row" @saved="load" @refresh="load" /></template></el-table-column><el-table-column label="操作" width="235" fixed="right"><template #default="{ row }"><el-button link type="primary" @click="router.push('/orders/' + row.id)">查看详情</el-button><el-button v-if="!orderReadOnlyReason(row)" link type="primary" @click="editing=row">编辑</el-button><el-button v-if="!orderReadOnlyReason(row)" link type="danger" @click="voiding=row">作废订单</el-button><el-tooltip v-else :content="orderReadOnlyReason(row)"><span class="muted">只读</span></el-tooltip></template></el-table-column></el-table><div class="table-footer"><span>共 {{ total }} 条订单 · 最近创建优先</span><el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" :page-sizes="[10,20,50,100]" layout="sizes, prev, pager, next" :disabled="loading" @current-change="load" @size-change="page = 1; load()" /></div></section>
<VoidOrderDialog v-if="voiding" :key="voiding.id" :order="voiding" @close="voiding=null" @saved="load"/><OrderOperationDialog v-if="editing" :key="editing.id" mode="edit" :order-id="editing.id" @close="editing=null" @saved="ElMessage.success('保存成功，金额 '+money($event.order.amount)); load()" /></template>
