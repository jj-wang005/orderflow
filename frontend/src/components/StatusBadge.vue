<script setup lang="ts">
import { statusTone, statusLabel } from '../utils/status'
defineProps<{ status: string; description?: string; code?: string | null }>()
</script>
<template><el-tag :type="statusTone(status)" effect="light" class="status-badge"><span class="tag-dot"></span>{{ statusLabel(status, description, code) }}</el-tag></template>
