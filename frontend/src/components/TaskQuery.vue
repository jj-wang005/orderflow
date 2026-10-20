<script setup lang="ts">
import { ref } from 'vue'
import { Search } from '@element-plus/icons-vue'
import { positiveId } from '../utils/format'
import { ElMessage } from 'element-plus'
const props = defineProps<{ value?: string; loading?: boolean }>()
const emit = defineEmits<{ query: [id: number] }>()
const input = ref(props.value || '')
function query() { const id = positiveId(input.value.trim()); if (!id) { ElMessage.warning('请输入有效的正整数任务编号'); return } emit('query', id) }
</script>
<template><form class="query-bar" @submit.prevent="query"><label for="task-id-input">导入任务编号</label><el-input id="task-id-input" v-model="input" placeholder="输入任务 ID，例如上传结果中的编号" clearable :disabled="loading" style="max-width:380px" /><el-button type="primary" :icon="Search" native-type="submit" :loading="loading">查询任务</el-button><span class="query-hint">任务 ID 可从导入结果中获取</span></form></template>
