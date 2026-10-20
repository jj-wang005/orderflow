<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PageHeading from '../components/PageHeading.vue'
import BatchImportView from './BatchImportView.vue'
import AIExtractionView from './AIExtractionView.vue'

defineOptions({ name: 'OrderEntryView' })
const route = useRoute(), router = useRouter()
// Keep both workflows mounted after their first visit, including native file inputs.
const mode = computed(() => route.query.mode === 'ai' ? 'ai' : 'excel')
const visitedAI = ref(mode.value === 'ai')
watch(mode, value => { if (value === 'ai') visitedAI.value = true })
function switchMode(value: string | number) {
  if (value !== mode.value) router.push({ name: 'order-entry', params: route.params, query: { ...route.query, mode: value }, hash: route.hash })
}
</script>
<template>
  <div class="order-entry">
    <PageHeading title="订单录入" eyebrow="ORDER ENTRY" subtitle="支持标准 Excel 批量导入及 AI 智能解析，快速处理客户采购订单。" />
    <el-tabs :model-value="mode" class="entry-tabs" @update:model-value="switchMode">
      <el-tab-pane label="标准 Excel 导入" name="excel" />
      <el-tab-pane label="AI 智能解析" name="ai" />
    </el-tabs>
    <div v-show="mode === 'excel'"><BatchImportView embedded /></div>
    <div v-if="visitedAI" v-show="mode === 'ai'"><AIExtractionView embedded /></div>
  </div>
</template>
<style scoped>
.entry-tabs{margin-top:-10px;margin-bottom:18px}
.order-entry{min-width:0;padding-bottom:100px}
.order-entry :deep(.entry-workflow-actions){display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap;margin-bottom:18px}
.order-entry :deep(.unified),.order-entry :deep(.batch-submit){flex-wrap:wrap}
.order-entry :deep(.el-select){max-width:100%}
.order-entry :deep(input[type=file]){max-width:100%}
</style>
