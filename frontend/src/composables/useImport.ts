import { computed, ref, shallowRef } from 'vue'
import { api } from '../api/orders'
import {importApi} from '../api/imports'
import { ApiError, errorText } from '../api/client'
import type { CustomerId, TaskSummary } from '../types/api'
import { readOperation, reserveOperation, saveOperation, clearConfirmedOperation, fileHash, rememberTask } from '../utils/storage'

export const UPLOAD_MAX_BYTES = Number(import.meta.env.VITE_UPLOAD_MAX_BYTES || 10485760)
export function useImport() {
  const customer = ref<CustomerId | ''>(''), file = shallowRef<File | null>(null), busy = ref(false)
  const duplicateDetected=ref(false), duplicateConfirmed=ref(false), previousTask=shallowRef<TaskSummary|null>(null)
  const progress = ref(0), error = ref(''), result = shallowRef<TaskSummary | null>(null)
  const operation = shallowRef<ReturnType<typeof readOperation>>(null)
  try { operation.value = readOperation(); if (operation.value) customer.value = operation.value.customerId }
  catch (e) { error.value = errorText(e) }
  const recoverable = computed(() => !!operation.value && ['sending', 'uncertain'].includes(operation.value.status))
  const canSubmit = computed(() => !!customer.value && !!file.value && !busy.value && !result.value)

  function selectFile(selected: File) {
    if (busy.value || result.value) return false
    if (!/\.xlsx$/i.test(selected.name)) { file.value = null; error.value = '仅支持 .xlsx 格式的 Excel 文件。'; return false }
    if (!selected.size) { file.value = null; error.value = '文件为空，请重新选择。'; return false }
    if (!Number.isSafeInteger(UPLOAD_MAX_BYTES) || UPLOAD_MAX_BYTES < 1 || selected.size > UPLOAD_MAX_BYTES) {
      file.value = null; error.value = '文件超过当前页面允许的上传大小，请检查文件或联系维护人员。'; return false
    }
    file.value = selected; error.value = ''; return true
  }
  async function restoreResult() {
    if (operation.value?.status === 'completed' && operation.value.taskId) {
      try {
        const restored = await api.task(operation.value.taskId)
        if (restored.customer_id !== operation.value.customerId || restored.file_hash !== operation.value.fileHash) {
          throw new Error('查询到的任务与原导入记录不一致，请核查服务环境，勿重复发起采购。')
        }
        result.value = restored; rememberTask(restored)
      }
      catch (e) { error.value = errorText(e) }
    }
  }
  async function execute() {
    if (!canSubmit.value || busy.value) return
    busy.value = true; error.value = ''; progress.value = 0
    const selected = file.value!, cid = customer.value as CustomerId
    const run = async () => {
      const op = reserveOperation(cid, await fileHash(selected), selected)
      operation.value = op
      if (op.status === 'completed' && op.taskId) {
        const restored = await api.task(op.taskId)
        if (restored.customer_id !== op.customerId || restored.file_hash !== op.fileHash) {
          throw new Error('查询到的任务与原导入记录不一致，请核查服务环境，勿重复发起采购。')
        }
        result.value = restored; rememberTask(restored); return
      }
      const sending = { ...op, status: 'sending' as const }
      saveOperation(sending); operation.value = sending // Persist BEFORE sending any request.
      try {
        const task = await api.upload(cid, selected, op.key, p => { progress.value = p }, {duplicate_confirmed:duplicateConfirmed.value})
        result.value = task; rememberTask(task)
        const done = { ...op, status: 'completed' as const, taskId: task.task_id }
        operation.value = done
        try { saveOperation(done) } catch { error.value = '导入已确认完成，但浏览器记录更新失败。请保存任务编号，勿重复新建。' }
      } catch (e) {
        const duplicate=e instanceof ApiError && e.code==='DUPLICATE_FILE'
        if(duplicate){duplicateDetected.value=true;try{previousTask.value=(await importApi.duplicate(cid,selected)).previous}catch{/* keep duplicate warning */}}
        const rejected = duplicate || e instanceof ApiError && [400, 413, 422].includes(e.status || 0)
        const next = { ...op, status: rejected ? 'rejected' as const : 'uncertain' as const }
        operation.value = next
        // The original persisted sending key is already sufficient for recovery
        // if this final metadata update fails.
        try { saveOperation(next) } catch { /* retain original persisted key */ }
        throw e
      }
    }
    try {
      if (navigator.locks) {
        await navigator.locks.request('cvte-order-import', { ifAvailable: true }, async lock => {
          if (!lock) throw new Error('另一个浏览器页面正在录单，请等待该页面完成后再确认。')
          await run()
        })
      } else await run()
    } catch (e) { error.value = errorText(e) }
    finally { busy.value = false }
  }
  async function confirmDuplicate(){duplicateConfirmed.value=true;duplicateDetected.value=false;await execute()}
  function startNew() {
    if (busy.value) return
    try { clearConfirmedOperation(); operation.value = null; result.value = null; file.value = null; customer.value = ''; error.value = ''; progress.value = 0; duplicateDetected.value=false;duplicateConfirmed.value=false;previousTask.value=null }
    catch (e) { error.value = errorText(e) }
  }
  return { duplicateDetected,previousTask,confirmDuplicate,customer, file, busy, progress, error, result, operation, recoverable, canSubmit, selectFile, restoreResult, execute, startNew }
}
