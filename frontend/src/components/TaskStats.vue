<script setup lang="ts">
import type { TaskSummary } from '../types/api'
import { Files, CircleCheck, Search, Warning } from '@element-plus/icons-vue'
defineProps<{ task: TaskSummary }>()
const cards = [{ key: 'total_rows', label: '采购数据行', icon: Files, tone: 'blue', hint: '本次输入实际采购行' },
  { key: 'success_rows', label: '成功建单', icon: CircleCheck, tone: 'green', hint: '已创建新销售订单' },
  { key: 'unmatched_rows', label: '待人工补全', icon: Search, tone: 'amber', hint: '未找到符合规则的历史单' },
  { key: 'failed_rows', label: '其他失败', icon: Warning, tone: 'red', hint: '参考无效、歧义或处理异常' }] as const
</script>
<template><div class="stats-grid"><div v-for="card in cards" :key="card.key" class="stat-card"><div class="stat-title">{{ card.label }}<span class="stat-icon" :class="card.tone"><el-icon><component :is="card.icon" /></el-icon></span></div><strong>{{ task[card.key].toLocaleString('zh-CN') }}<small>行</small></strong><p>{{ card.hint }}</p></div></div><div v-if="task.duplicate_rows || task.review_rows" class="section-alert"><el-alert :title="'已关联重复采购 '+(task.duplicate_rows||0)+' 行；待确认是否重复采购 '+(task.review_rows||0)+' 行。以上均未新建订单，请进入明细处理。'" type="warning" :closable="false"/></div></template>
