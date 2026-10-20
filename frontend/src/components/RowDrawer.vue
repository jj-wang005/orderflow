<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import type { RowDetail } from '../types/api'
import { decimal, valueText } from '../utils/format'
import StatusBadge from './StatusBadge.vue'
import ManualCorrection from './ManualCorrection.vue'
import PurchaseReview from './PurchaseReview.vue'
const props = defineProps<{ row: RowDetail | null; open: boolean; initialAction?:'detail'|'review'|'correct' }>()
const emit = defineEmits<{ 'update:open': [boolean]; processed: [RowDetail] }>(), router = useRouter()
const rawEntries = computed(() => props.row ? Object.entries(props.row.raw_data).map(([field, value]) => ({ field, value: valueText(value), coordinate: props.row?.normalized_data?.raw_cells[field]?.coordinate || '—' })) : [])
const action=ref<'detail'|'review'|'correct'>('detail')
const editable=computed(()=>!!props.row&&!props.row.created_order_id&&!['SUCCESS','DUPLICATE','PENDING'].includes(props.row.status))
const reviewable=computed(()=>editable.value&&['REVIEW_REQUIRED','NOT_MATCHED'].includes(props.row!.status))
const reviewHistory=computed(()=>Array.isArray(props.row?.purchase_review?.history)?props.row!.purchase_review!.history as Record<string,unknown>[]:[])
watch(()=>[props.open,props.row?.id,props.row?.status,props.initialAction],()=>{
  action.value=props.initialAction|| (props.row?.status==='REVIEW_REQUIRED'?'review':editable.value?'correct':'detail')
},{immediate:true})
function order(id: number) { emit('update:open', false); router.push('/orders/' + id) }
</script>
<template><el-drawer :model-value="open" @update:model-value="emit('update:open', $event)" :title="row ? `采购行详情 · Excel 第 ${row.row_number} 行` : '采购行详情'" size="min(700px, 94vw)">
  <template v-if="row"><div class="drawer-summary"><StatusBadge :status="row.status" :description="row.status_description" :code="row.error_code" /><span>{{ row.sheet_name }} · 第 {{ row.row_number }} 行</span></div><el-alert v-if="row.error_message" :title="row.error_message" type="warning" :closable="false" show-icon />
  <section class="next-action"><h3>下一步操作</h3><template v-if="editable"><p class="muted">先选择要处理的问题，每次只展开一个表单。提交后由后端重新检查。</p><div class="action-choices"><el-button v-if="reviewable" :type="action==='review'?'primary':'default'" @click="action='review'">确认是否重复采购</el-button><el-button :type="action==='correct'?'primary':'default'" @click="action='correct'">修正本次采购信息</el-button><el-button text @click="action='detail'">只查看来源</el-button></div></template>
    <el-button v-if="row.created_order_id" type="primary" @click="order(row.created_order_id)">查看销售订单 #{{row.created_order_id}}</el-button><el-button v-else-if="row.duplicate_order_id" type="primary" @click="order(row.duplicate_order_id)">查看已处理订单 #{{row.duplicate_order_id}}</el-button>
    <p v-if="!editable" class="muted">已处理的采购行不能再次建单或修改；来源及记录保留供核查。</p>
    <PurchaseReview v-if="open&&reviewable&&action==='review'" :key="'review-'+row.id+'-'+row.manual_revision" :row="row" @processed="emit('processed',$event)"/>
    <ManualCorrection v-if="open&&editable&&action==='correct'" :key="row.id" :task-id="row.task_id" :row-id="row.id" @processed="emit('processed',$event)"/>
  </section>
  <h3 class="detail-section-heading">处理与来源</h3><el-descriptions :column="2" border><el-descriptions-item label="任务编号">#{{ row.task_id }}</el-descriptions-item><el-descriptions-item label="错误代码">{{ row.error_code || '无' }}</el-descriptions-item><el-descriptions-item label="历史参考"><el-button v-if="row.matched_order_id" link type="primary" @click="order(row.matched_order_id)">#{{ row.matched_order_id }}</el-button><span v-else>未命中可靠参考</span></el-descriptions-item><el-descriptions-item label="新销售订单"><el-button v-if="row.created_order_id" link type="primary" @click="order(row.created_order_id)">#{{ row.created_order_id }}</el-button><span v-else>未创建</span></el-descriptions-item></el-descriptions>
  <h3 class="detail-section-heading">标准化采购信息</h3><el-descriptions v-if="row.normalized_data" :column="2" border><el-descriptions-item label="客户">{{ row.normalized_data.customer_id }}</el-descriptions-item><el-descriptions-item label="采购数量">{{ decimal(row.normalized_data.quantity) }}</el-descriptions-item><el-descriptions-item label="客户单号" :span="2">{{ row.normalized_data.customer_order_no || '—' }}</el-descriptions-item><el-descriptions-item label="客户料号" :span="2">{{ row.normalized_data.customer_material_code || '—' }}</el-descriptions-item><el-descriptions-item label="客户批次">{{ row.normalized_data.customer_batch || '—' }}</el-descriptions-item><el-descriptions-item label="客户机型">{{ row.normalized_data.customer_model || '—' }}</el-descriptions-item></el-descriptions><el-alert v-else title="该行未通过标准化，请查看原始采购数据及错误原因。" type="info" :closable="false" />
  <h3 class="detail-section-heading">Excel 原始采购数据</h3><p class="muted detail-caption">原始数据仅展示一次。单元格定位来自解析结果，不在前端重新推断。</p><el-table :data="rawEntries" max-height="460" border><el-table-column prop="field" label="原始列名" min-width="150" show-overflow-tooltip /><el-table-column prop="value" label="原始值" min-width="230" show-overflow-tooltip /><el-table-column prop="coordinate" label="单元格" width="90" /></el-table><el-collapse v-if="row.normalized_data" class="cell-metadata"><el-collapse-item title="查看原始单元格格式与类型信息" name="cells"><pre class="json-view">{{ JSON.stringify(row.normalized_data.raw_cells, null, 2) }}</pre></el-collapse-item></el-collapse></template>
  <el-collapse v-if="row&&open&&action!=='correct'" class="cell-metadata"><el-collapse-item title="查看采购修正记录" name="audit"><ManualCorrection :key="'audit-'+row.id" :task-id="row.task_id" :row-id="row.id" history-only/></el-collapse-item></el-collapse>
  <el-collapse v-if="reviewHistory.length" class="cell-metadata"><el-collapse-item :title="'查看重复采购核实记录（'+reviewHistory.length+'次）'" name="review-audit"><article v-for="(item,i) in reviewHistory" :key="i" class="review-audit"><strong>{{item.decision==='NEW'?'这是新的采购／加购':'这笔采购已经处理过'}}</strong><p>核实时间：{{item.confirmed_at}} · {{item.actor_label}}</p><p>{{item.note}}</p><p v-if="item.purchase_reference">依据编号：{{item.purchase_reference}}</p><el-button v-if="item.existing_order_id" link type="primary" @click="order(Number(item.existing_order_id))">查看已处理订单 #{{item.existing_order_id}}</el-button></article></el-collapse-item></el-collapse>
</el-drawer></template>
<style scoped>.next-action{margin:20px 0;padding:18px;border:1px solid var(--el-border-color-lighter);border-radius:10px;background:var(--el-fill-color-extra-light)}.next-action h3{margin:0 0 10px}.action-choices{display:flex;flex-wrap:wrap;gap:8px}.action-choices .el-button{margin:0}</style>
