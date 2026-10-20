import type { CustomerId, TaskSummary } from '../types/api'

export const ACTIVE_KEY = 'cvte-order.active.v1'
const RECENT_KEY = 'cvte-order.recent.v1'
export interface ImportOperation {
  version: 1; key: string; customerId: CustomerId; fileHash: string; fileName: string; fileSize: number;
  createdAt: string; status: 'ready' | 'sending' | 'uncertain' | 'completed' | 'rejected'; taskId?: number;
}
const operationStates = ['ready', 'sending', 'uncertain', 'completed', 'rejected']
export function readOperation(): ImportOperation | null {
  try {
    const raw = localStorage.getItem(ACTIVE_KEY)
    if (!raw) return null
    const op = JSON.parse(raw) as ImportOperation
    if (op.version !== 1 || (typeof op.customerId !== 'string' || !op.customerId.trim() || op.customerId.length > 64)
      || typeof op.fileHash !== 'string' || !/^[a-f0-9]{64}$/.test(op.fileHash)
      || typeof op.key !== 'string' || !/^[!-~]{1,128}$/.test(op.key) || !operationStates.includes(op.status)
      || typeof op.fileName !== 'string' || !Number.isSafeInteger(op.fileSize) || op.fileSize < 1
      || (op.status === 'completed' && (!Number.isSafeInteger(op.taskId) || Number(op.taskId) < 1))) {
      throw new Error('invalid operation')
    }
    return op
  } catch {
    // Never silently discard an unreadable pending key and then send a new purchase.
    throw new Error('浏览器中的导入恢复记录无法读取，请先通过任务编号核查，勿重复发起采购。')
  }
}
export function saveOperation(op: ImportOperation): void {
  try { localStorage.setItem(ACTIVE_KEY, JSON.stringify(op)) }
  catch { throw new Error('无法保存导入恢复记录。为避免重复建单，本次请求尚未发送；请检查浏览器存储权限。') }
}
export function reserveOperation(customerId: CustomerId, fileHash: string, file: Pick<File, 'name' | 'size'>): ImportOperation {
  const existing = readOperation()
  if (existing) {
    if (existing.customerId !== customerId || existing.fileHash !== fileHash) {
      throw new Error('当前操作的客户或文件与恢复记录不一致。请重新选择原文件，或在确认结果后明确新建一次导入。')
    }
    return existing
  }
  const operation: ImportOperation = { version: 1, key: 'CVTE-UI-' + crypto.randomUUID(), customerId, fileHash,
    fileName: file.name, fileSize: file.size, createdAt: new Date().toISOString(), status: 'ready' }
  saveOperation(operation)
  return operation
}
export function clearConfirmedOperation(): void {
  const op = readOperation()
  if (op && !['completed', 'rejected', 'ready'].includes(op.status)) {
    throw new Error('上一次导入结果尚未确认，请先沿用原操作继续确认，不能直接新建。')
  }
  try { localStorage.removeItem(ACTIVE_KEY) }
  catch { throw new Error('浏览器存储无法更新，请稍后重试。') }
}
export async function fileHash(file: File): Promise<string> {
  if (!globalThis.crypto?.subtle) throw new Error('当前页面无法安全校验文件，请通过本机 localhost 或 HTTPS 地址访问。')
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
  return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('')
}
export interface RecentTask { task_id: number; customer_id: string; file_name: string; visited_at: string }
export function recentTasks(): RecentTask[] {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]')
    if (!Array.isArray(raw)) return []
    return raw.filter((r): r is RecentTask => r && Number.isSafeInteger(r.task_id) && r.task_id > 0
      && typeof r.customer_id === 'string' && typeof r.file_name === 'string' && typeof r.visited_at === 'string').slice(0, 12)
  } catch { return [] }
}
export function rememberTask(task: TaskSummary): void {
  const recent = recentTasks().filter(r => r.task_id !== task.task_id)
  try { localStorage.setItem(RECENT_KEY, JSON.stringify([{ task_id: task.task_id, customer_id: task.customer_id,
    file_name: task.file_name, visited_at: new Date().toISOString() }, ...recent].slice(0, 12))) }
  catch { /* Navigation/query still works when optional recent-history storage is unavailable. */ }
}
export function clearRecentTasks(): void {
  try { localStorage.removeItem(RECENT_KEY) }
  catch { throw new Error('本地记录无法清除，请检查浏览器存储权限。') }
}
