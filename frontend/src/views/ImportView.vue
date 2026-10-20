<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useCustomers } from '../composables/useCustomers'
const {customers,customerError}=useCustomers()
import { useRouter } from 'vue-router'
import { UploadFilled, Document, CircleCheck, ArrowRight, RefreshRight, Delete, InfoFilled } from '@element-plus/icons-vue'
import type { UploadFile } from 'element-plus'
import { ElMessageBox } from 'element-plus'
import PageHeading from '../components/PageHeading.vue'
import TaskStats from '../components/TaskStats.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { useImport, UPLOAD_MAX_BYTES } from '../composables/useImport'
import { fileSize } from '../utils/format'
const router = useRouter(), uploadVersion = ref(0)
const { duplicateDetected,previousTask,confirmDuplicate,customer, file, busy, progress, error, result, operation, recoverable, canSubmit, selectFile, restoreResult, execute, startNew } = useImport()
function choose(upload: UploadFile) { if (upload.raw) selectFile(upload.raw) }
async function newImport() {
  try {
    await ElMessageBox.confirm('新建导入会开始一次新的采购操作。即使上传相同文件，也可能再次创建销售订单。是否继续？', '新建一次导入', { confirmButtonText: '确认新建', cancelButtonText: '保留当前结果', type: 'warning' })
    startNew(); uploadVersion.value++
  } catch { /* cancellation retains the operation */ }
}
onMounted(restoreResult)
</script>
<template>
  <PageHeading title="订单导入" eyebrow="IMPORT WORKSPACE" subtitle="接收客户采购 Excel，匹配我司历史销售订单，自动完成可靠采购行的录单。"><el-button v-if="operation && !recoverable" :icon="RefreshRight" :disabled="busy" @click="newImport">新建一次导入</el-button></PageHeading>
  <el-alert v-if="customerError" :title="customerError" type="error" :closable="false" class="section-alert" />
  <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="section-alert" />
  <el-alert v-if="duplicateDetected" title="该客户已导入相同内容文件" type="warning" :closable="false" class="section-alert"><el-button v-if="previousTask" link type="primary" @click="router.push('/tasks/'+previousTask.task_id)">查看上次结果</el-button><el-button :disabled="busy" @click="startNew">取消</el-button><el-button type="warning" :disabled="busy" @click="confirmDuplicate">确认再次导入</el-button></el-alert>
  <el-alert v-if="recoverable" title="上次录单结果待确认" type="warning" :closable="false" show-icon class="section-alert"><p>请重新选择原文件「{{ operation?.fileName }}」，然后继续确认。系统会恢复原操作，不另建一份采购。</p></el-alert>
  <template v-if="result">
    <div class="result-banner"><span class="result-mark"><el-icon><CircleCheck /></el-icon></span><div><h2>{{ result.idempotency_replayed ? '已找回原导入任务' : '采购文件处理完成' }}</h2><p>任务 #{{ result.task_id }} · {{ result.file_name }}<span v-if="result.idempotency_replayed"> · 本次未重复建单</span></p></div><StatusBadge :status="result.status" :description="result.status_description" /></div>
    <TaskStats :task="result" />
    <section class="panel result-detail"><div class="panel-heading"><div><h2>处理结果</h2><p>成功行已创建销售订单；其余采购行保留原因，供我司人员核查。</p></div><span class="muted">客户 {{ result.customer_id }}</span></div><el-alert v-if="result.error_summary" :title="result.error_summary" type="warning" :closable="false" show-icon /><div class="result-actions"><el-button type="primary" :icon="ArrowRight" @click="router.push('/rows/' + result.task_id)">查看采购行明细</el-button><el-button @click="router.push('/tasks/' + result.task_id)">查看任务信息</el-button><el-button text @click="router.push({ path: '/orders', query: { customer: result.customer_id, source: 'AUTO' } })">查看销售订单</el-button></div></section>
  </template>
  <div v-else class="import-grid">
    <section class="panel import-panel">
      <div class="panel-heading"><div><h2>上传客户采购文件</h2><p>一份文件对应一个导入任务，系统逐行处理。</p></div><span class="panel-index">01</span></div>
      <el-steps :active="busy ? 1 : 0" finish-status="success" simple class="import-steps"><el-step title="准备采购文件" /><el-step title="执行自动录单" /><el-step title="核查处理结果" /></el-steps>
      <el-form label-position="top" class="import-form"><el-form-item label="选择客户" required><el-select v-model="customer" placeholder="请选择采购文件所属客户" :disabled="busy || !!operation" style="width:100%" aria-label="选择客户"><el-option v-for="c in customers" :key="c.customer_id" :label="c.customer_name + '（' + c.customer_id + '）'" :value="c.customer_id" :disabled="!c.enabled || !c.configured" /></el-select><p class="field-help">请选择提供 Excel 的客户，系统会使用该客户已保存的模板配置。</p></el-form-item><el-form-item label="客户采购 Excel" required><el-upload :key="uploadVersion" drag :auto-upload="false" :show-file-list="false" accept=".xlsx" :disabled="busy" :on-change="choose" class="excel-upload"><div class="upload-icon"><el-icon><UploadFilled /></el-icon></div><p class="upload-title">将 Excel 拖到此处，或<span>点击选择文件</span></p><p class="upload-caption">仅支持 .xlsx · 当前页面上限 {{ fileSize(UPLOAD_MAX_BYTES) }}</p></el-upload></el-form-item></el-form>
      <div v-if="file" class="selected-file"><span class="file-icon"><el-icon><Document /></el-icon></span><div><strong>{{ file.name }}</strong><small>{{ fileSize(file.size) }} · 已选择，尚未自动提交</small></div><el-button v-if="!busy" text :icon="Delete" aria-label="移除所选文件" @click="file = null" /></div>
      <div v-else-if="operation" class="pending-file">待重新选择原文件：{{ operation.fileName }}</div>
      <div v-if="busy" class="upload-progress"><el-progress :percentage="progress" :stroke-width="5" :show-text="false" /><p>{{ progress === 100 ? '文件已发送，正在处理采购行，请勿新建重复任务…' : '正在准备或发送采购文件，请稍候…' }}</p></div>
      <div class="submit-strip"><div class="write-note"><el-icon><InfoFilled /></el-icon><span>点击后将向 MySQL 写入任务、明细及成功匹配的新订单。</span></div><el-button type="primary" size="large" :icon="recoverable ? RefreshRight : UploadFilled" :disabled="!canSubmit" :loading="busy" @click="execute">{{ recoverable ? '继续确认录单结果' : '开始自动录单' }}</el-button></div>
    </section>
    <aside class="import-aside"><section class="panel guide-panel"><span class="eyebrow">HOW IT WORKS</span><h2>从客户采购到我司销售</h2><div class="guide-step"><span>1</span><div><strong>选择对应客户模板</strong><p>我司销管接收客户文件，按所属客户选择配置。</p></div></div><div class="guide-step"><span>2</span><div><strong>可靠历史参考才建单</strong><p>产品与单价取自符合规则的历史单，数量使用本次采购值。</p></div></div><div class="guide-step"><span>3</span><div><strong>异常采购行保留原因</strong><p>无历史匹配、产品歧义和参考异常不会自动创建订单。</p></div></div><div class="guide-footnote"><el-icon><CircleCheck /></el-icon> 上传选择不会立即建单</div></section><section class="local-tip"><span class="tip-label">订单处理提示</span><h3>确认与后续采购</h3><p>新订单创建后需在销售订单中确认。已确认、进行中、已发货和已完成的可靠订单可用于后续采购匹配。</p></section></aside>
  </div>
</template>
