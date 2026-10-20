<script setup lang="ts">
import { ref, shallowRef, watch } from 'vue'
defineProps<{embedded?:boolean}>()
const emit=defineEmits<{changed:[]}>()
function refreshAfterChange(){load();emit('changed')}
import { useRoute, useRouter } from 'vue-router'
import { RefreshRight, Tickets } from '@element-plus/icons-vue'
import { api } from '../api/orders'
import { errorText } from '../api/client'
import type { RowDetail, TaskSummary } from '../types/api'
import { decimal, positiveId } from '../utils/format'
import { rememberTask } from '../utils/storage'
import PageHeading from '../components/PageHeading.vue'
import TaskStats from '../components/TaskStats.vue'
import StatusBadge from '../components/StatusBadge.vue'
import RowDrawer from '../components/RowDrawer.vue'
import MatchExplanation from '../components/MatchExplanation.vue'
const explaining=shallowRef<RowDetail|null>(null)
import OrderOperationDialog from '../components/OrderOperationDialog.vue'
import {ElMessage} from 'element-plus'
import {money} from '../utils/format'
const manualRow = shallowRef<RowDetail | null>(null)
const route = useRoute(), router = useRouter(), task = shallowRef<TaskSummary | null>(null)
const rows = shallowRef<RowDetail[]>([]), selected = shallowRef<RowDetail | null>(null), open = ref(false)
const page = ref(1), pageSize = ref(20), total = ref(0), loading = ref(false), error = ref('')
let sequence = 0
async function load() {
  const token = ++sequence, id = positiveId(route.params.id); error.value = ''
  if (!id) { loading.value = false; if (route.params.id) error.value = '任务编号不合法，请输入正整数。'; return }
  loading.value = true
  try { const [summary, result] = await Promise.all([api.task(id), api.rows(id, page.value, pageSize.value)]); if (token === sequence) { task.value = summary; rows.value = result.items; total.value = result.total; rememberTask(summary) } }
  catch (e) { if (token === sequence) error.value = errorText(e) }
  finally { if (token === sequence) loading.value = false }
}
watch(() => route.params.id, () => { page.value = 1; total.value = 0; rows.value = []; task.value = null; open.value = false; load() }, { immediate: true })
const initialAction=ref<'detail'|'review'|'correct'>('detail')
function show(row: RowDetail,action:'detail'|'review'|'correct'='detail') { selected.value = row; initialAction.value=action;open.value = true }
</script>
<template>
  <PageHeading v-if="!embedded" title="采购行明细" eyebrow="PURCHASE DETAILS" subtitle="逐行核查客户采购信息、历史匹配结果与新订单，异常行不自动建单。"><el-button :icon="RefreshRight" :disabled="!task || loading" @click="load">刷新明细</el-button></PageHeading>
  <el-button v-if="!embedded" text @click="router.push('/tasks')">浏览历史任务列表</el-button>
  <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="section-alert" />
  <TaskStats v-if="task && !embedded" :task="task" />
  <section class="panel table-panel" v-loading="loading"><div class="panel-heading"><div><h2>{{ task ? `采购行处理结果 · 任务 #${task.task_id}` : '采购行处理结果' }}</h2><p>{{ task ? `客户 ${task.customer_id} · ${task.file_name}` : '请从历史任务列表进入对应文件的采购行。' }}</p></div><el-button v-if="task" text :icon="Tickets" @click="router.push('/tasks/' + task.task_id)">任务信息</el-button></div>
    <el-table :data="rows" row-key="id" class="data-table" :empty-text="error ? '当前数据无法加载' : task ? '该任务暂无采购行' : '请从历史任务列表进入对应文件'" max-height="590"><el-table-column prop="row_number" label="来源行" width="85" fixed /><el-table-column label="客户单号" min-width="145" show-overflow-tooltip><template #default="{ row }">{{ row.normalized_data?.customer_order_no || '—' }}</template></el-table-column><el-table-column label="客户料号" min-width="170" show-overflow-tooltip><template #default="{ row }">{{ row.normalized_data?.customer_material_code || '—' }}</template></el-table-column><el-table-column label="批次 / 机型" min-width="145" show-overflow-tooltip><template #default="{ row }">{{ row.normalized_data?.customer_batch || row.normalized_data?.customer_model || '—' }}</template></el-table-column><el-table-column label="采购数量" width="115" align="right"><template #default="{ row }"><span class="numeric">{{ decimal(row.normalized_data?.quantity) }}</span></template></el-table-column><el-table-column label="处理状态" width="220"><template #default="{ row }"><StatusBadge :status="row.status" :description="row.status_description" :code="row.error_code" /></template></el-table-column><el-table-column label="历史单 ID" width="105"><template #default="{ row }"><el-button v-if="row.matched_order_id" link type="primary" @click="router.push('/orders/' + row.matched_order_id)">#{{ row.matched_order_id }}</el-button><span v-else>—</span></template></el-table-column><el-table-column label="新订单 ID" width="110"><template #default="{ row }"><el-button v-if="row.created_order_id" link type="primary" @click="router.push('/orders/' + row.created_order_id)">#{{ row.created_order_id }}</el-button><el-button v-else-if="row.duplicate_order_id" link type="primary" @click="router.push('/orders/'+row.duplicate_order_id)">已有 #{{row.duplicate_order_id}}</el-button><span v-else>—</span></template></el-table-column><el-table-column prop="error_message" label="异常原因" min-width="260" show-overflow-tooltip><template #default="{ row }"><span :class="{ muted: !row.error_message }">{{ row.error_message || '—' }}</span></template></el-table-column><el-table-column label="操作" width="390" fixed="right"><template #default="{ row }"><el-button v-if="row.created_order_id" link type="primary" @click="router.push('/orders/'+row.created_order_id)">查看销售订单</el-button><el-button v-else-if="row.duplicate_order_id" link type="primary" @click="router.push('/orders/'+row.duplicate_order_id)">查看已处理订单</el-button><el-button v-else-if="row.status==='REVIEW_REQUIRED'" link type="primary" @click="show(row,'review')">确认是否重复采购</el-button><el-button v-else-if="!['SUCCESS','DUPLICATE','PENDING'].includes(row.status)" link type="primary" @click="show(row,'correct')">修正信息</el-button><el-button link @click="show(row)">来源详情</el-button><el-button v-if="row.status==='NOT_MATCHED' && !row.created_order_id" link type="primary" @click="manualRow=row">人工补全新建</el-button><el-button v-if="row.status==='NOT_MATCHED'" link type="primary" @click="explaining=row">检查匹配规则</el-button></template></el-table-column></el-table>
    <div class="table-footer"><span>共 {{ total }} 行采购数据 · 按工作表及 Excel 行号排序</span><el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" :page-sizes="[10,20,50,100]" layout="sizes, prev, pager, next" :disabled="loading || !task" @current-change="load" @size-change="page = 1; load()" /></div>
  </section><MatchExplanation v-if="explaining" :key="explaining.id" :row="explaining" @close="explaining=null"/><OrderOperationDialog v-if="manualRow" :key="manualRow.id" mode="manual" :task-id="manualRow.task_id" :row-id="manualRow.id" @close="manualRow=null" @saved="ElMessage.success('建单成功，金额 '+money($event.order.amount)); load(); router.push('/orders/'+$event.order.id)" /><RowDrawer v-model:open="open" :row="selected" :initial-action="initialAction" @processed="selected = $event; refreshAfterChange()" />
</template>
