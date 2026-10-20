<script setup lang="ts">
import { ref, shallowRef, onMounted } from 'vue'
import { api } from '../api/orders'
import { ApiError, errorText } from '../api/client'
import type { CorrectionContext, CorrectionRequest, RowDetail } from '../types/api'
const props = defineProps<{taskId: number; rowId: number; historyOnly?:boolean}>()
const emit = defineEmits<{processed: [RowDetail]}>()
const context = shallowRef<CorrectionContext | null>(null), values = ref<CorrectionRequest['values']>({})
const note = ref(''), busy = ref(false), loading = ref(false), error = ref(''), message = ref('')
const pending = shallowRef<CorrectionRequest | null>(null), storageBlocked = ref(false)
const storageKey = `cvte:correction:${props.taskId}:${props.rowId}`
function recover() {
  try {
    const raw = localStorage.getItem(storageKey)
    if (!raw) return
    const body = JSON.parse(raw) as CorrectionRequest
    if (typeof body.request_key !== 'string' || !/^[0-9a-f-]{36}$/i.test(body.request_key) || !Number.isSafeInteger(body.expected_revision) || body.expected_revision < 0 || !body.values || typeof body.note !== 'string') throw new Error('invalid')
    pending.value = body
  } catch { storageBlocked.value = true; error.value = '本地补录恢复记录无法读取，已阻止提交。请先核对服务器结果，勿盲目重建操作。' }
}
async function load() {
  loading.value = true
  try {
    context.value = await api.correction(props.taskId, props.rowId)
    values.value = {...(pending.value?.values || context.value.values)}
    note.value = pending.value?.note || ''
  } catch (e) { error.value = errorText(e) }
  finally { loading.value = false }
}
async function submit() {
  if (busy.value || storageBlocked.value || !context.value) return
  if (!pending.value) {
    if (!context.value.editable) return
    const missing = context.value.fields.filter(f => f.required && !values.value[f.field]?.trim())
    if (missing.length || !note.value.trim()) { error.value = missing.length ? `请填写：${missing.map(f => f.label).join('、')}` : '请填写修改说明。'; return }
    pending.value = {request_key: crypto.randomUUID(), expected_revision: context.value.revision, values: {...values.value}, note: note.value.trim()}
    try { localStorage.setItem(storageKey, JSON.stringify(pending.value)) }
    catch { pending.value = null; storageBlocked.value = true; error.value = '无法保存补录重试标识，未向服务器提交。'; return }
  }
  busy.value = true; error.value = ''; message.value = ''
  try {
    const result = await api.correct(props.taskId, props.rowId, pending.value)
    message.value = result.row.status_description + (result.row.error_message ? `：${result.row.error_message}` : '')
    emit('processed', result.row)
    localStorage.removeItem(storageKey); pending.value = null
    await load()
  } catch (e) {
    error.value = errorText(e)
    if (e instanceof ApiError && (e.status === 409 || e.status === 422 || e.status === 404)) {
      try { localStorage.removeItem(storageKey); pending.value = null; await load() }
      catch { storageBlocked.value = true }
    }
  } finally { busy.value = false }
}
onMounted(async () => { recover(); await load() })
</script>
<template>
  <section v-loading="loading" class="manual-correction">
    <h3 class="detail-section-heading">{{historyOnly?'采购修正记录':'修正本次采购信息'}}</h3>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <el-alert v-if="message" :title="message" type="info" :closable="false" show-icon />
    <template v-if="context">
      <el-alert v-if="pending" title="上次补录结果待确认。已锁定原内容，请使用原操作重试。" type="warning" :closable="false" />
      <el-alert v-else-if="!context.editable" title="该行已建单或正在处理，不允许修改或重复建单。" type="info" :closable="false" />
      <el-form v-if="!historyOnly&&(context.editable || pending)" label-position="top" :disabled="busy || !!pending || storageBlocked">
        <el-form-item v-for="field in context.fields" :key="field.field" :label="field.label" :required="field.required">
          <el-input v-model="values[field.field]" :maxlength="128" :aria-label="field.label" placeholder="核对原始采购信息后填写" />
        </el-form-item>
        <el-form-item label="修改说明" required><el-input v-model="note" type="textarea" maxlength="500" aria-label="修改说明" /></el-form-item>
      </el-form>
      <p class="muted" v-if="!historyOnly&&(context.editable || pending)">仅修改本次采购字段，不修改历史销售订单或已建订单。重新校验通过后可能创建新订单。</p>
      <el-button v-if="!historyOnly&&(context.editable || pending)" type="primary" :loading="busy" :disabled="loading || storageBlocked" @click="submit">{{ pending ? '使用原补录操作重试' : '重新校验、匹配并尝试建单' }}</el-button>
      <h4>修改记录（共 {{ context.history_total }} 次，展示最近 100 次）</h4>
      <el-empty v-if="!context.history.length" description="尚无人工修改记录" :image-size="50" />
      <el-collapse v-else><el-collapse-item v-for="item in context.history" :key="item.id" :title="`第 ${item.revision} 次 · ${item.result.status_description} · ${item.created_at}`">
        <p>{{ item.actor_label }} · {{ item.note }}</p><p>{{ item.result.error_message || '处理完成' }} · 新订单 {{ item.result.created_order_id || '未创建' }}</p>
        <pre class="json-view">{{ JSON.stringify({提交字段:item.submitted_values,修改前:item.before_data,修改后:item.after_data}, null, 2) }}</pre>
      </el-collapse-item></el-collapse>
    </template>
  </section>
</template>
<style scoped>
.manual-correction { margin-top: 24px; }
.manual-correction > p { margin: 12px 0; line-height: 1.7; }
.manual-correction :deep(.el-form) { margin-top: 16px; }
.manual-correction :deep(.el-alert) { margin-bottom: 12px; }
</style>
