import { beforeEach, afterEach, describe, it, expect, vi } from 'vitest'
import { api } from '../api/orders'
import { ApiError } from '../api/client'
import { useImport } from './useImport'
import { readOperation, saveOperation, reserveOperation } from '../utils/storage'
import type { TaskSummary } from '../types/api'

const mockTask: TaskSummary = {
  task_id: 900, customer_id:'A',file_name:'A.xlsx',file_hash:'a'.repeat(64),status:'PARTIAL_SUCCESS',
  status_description:'部分建单成功',total_rows:4,success_rows:1,unmatched_rows:2,failed_rows:1,pending_rows:0,
  config_version:1,rule_version:1,started_at:null,finished_at:null,error_message:null,error_summary:'请核查',idempotency_replayed:false,
}
function ready() { const state=useImport(); state.customer.value='A'; state.selectFile(new File(['valid-bytes'],'A.xlsx')); return state }
beforeEach(() => { localStorage.clear(); vi.restoreAllMocks() })
afterEach(() => vi.unstubAllGlobals())
describe('导入交互与安全重试', () => {
  it('缺客户、缺文件不提交；选文件不会自动建单', async () => {
    const request=vi.spyOn(api,'upload'), state=useImport()
    expect(state.canSubmit.value).toBe(false); await state.execute(); state.customer.value='A'
    await state.execute(); expect(request).not.toHaveBeenCalled()
    state.selectFile(new File(['x'],'a.xlsx')); expect(state.canSubmit.value).toBe(true); expect(request).not.toHaveBeenCalled()
  })
  it.each([new File(['x'],'a.csv'),new File([],'empty.xlsx')])('拒绝非法文件', file => {
    const state=ready(); state.selectFile(file); expect(state.canSubmit.value).toBe(false); expect(state.error.value).toBeTruthy()
  })
  it('上传前持久化原键，连击只产生一个请求', async () => {
    let finish!: (task: TaskSummary) => void
    const request=vi.spyOn(api,'upload').mockImplementation((_cid,_file,key) => {
      expect(readOperation()?.key).toBe(key); expect(readOperation()?.status).toBe('sending')
      return new Promise(resolve => { finish=resolve })
    })
    const state=ready(), first=state.execute(); await state.execute()
    await vi.waitFor(() => expect(request).toHaveBeenCalledTimes(1))
    expect(state.busy.value).toBe(true); expect(state.canSubmit.value).toBe(false)
    finish(mockTask); await first
    expect(state.result.value?.success_rows).toBe(1); expect(readOperation()?.taskId).toBe(900)
    expect(state.canSubmit.value).toBe(false)
  })
  it('超时后刷新恢复，重新选择同字节文件时复用键', async () => {
    const request=vi.spyOn(api,'upload').mockRejectedValueOnce(new ApiError('请求超时'))
    const first=ready(); await first.execute(); const key=readOperation()?.key
    expect(readOperation()?.status).toBe('uncertain'); expect(first.error.value).toContain('超时')
    const restored=useImport(); expect(restored.customer.value).toBe('A'); expect(restored.file.value).toBeNull()
    restored.selectFile(new File(['valid-bytes'],'renamed.xlsx')); request.mockResolvedValue({...mockTask,idempotency_replayed:true})
    await restored.execute(); expect(request.mock.calls[1]?.[2]).toBe(key); expect(restored.result.value?.idempotency_replayed).toBe(true)
  })
  it('恢复操作选择不同文件时不会向服务器发送第二次请求', async () => {
    const request=vi.spyOn(api,'upload').mockRejectedValue(new ApiError('网络中断'))
    const state=ready(); await state.execute(); state.selectFile(new File(['DIFFERENT'],'A.xlsx')); await state.execute()
    expect(request).toHaveBeenCalledTimes(1); expect(state.error.value).toContain('不一致')
  })
  it.each([409,500])('HTTP %s 保留原键和未确认状态，禁止直接新建', async status => {
    vi.spyOn(api,'upload').mockRejectedValue(new ApiError('中文错误',status))
    const state=ready(); await state.execute(); const key=readOperation()?.key
    state.startNew(); expect(readOperation()?.key).toBe(key); expect(state.recoverable.value).toBe(true)
  })
  it.each([400,413,422])('HTTP %s 拒绝后保留错误，可明确新建选择正确文件', async status => {
    vi.spyOn(api,'upload').mockRejectedValue(new ApiError('文件或配置错误',status))
    const state=ready(); await state.execute(); expect(readOperation()?.status).toBe('rejected')
    state.startNew(); expect(readOperation()).toBeNull()
  })
  it('本地存储不可写时没有 POST', async () => {
    const request=vi.spyOn(api,'upload').mockRejectedValue(new ApiError('unexpected request'))
    vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => { throw new Error('quota') } })
    const state=ready(); await state.execute(); expect(request).not.toHaveBeenCalled(); expect(state.error.value).toContain('尚未发送')
  })
  it('完成任务刷新只查询 GET，不重复 POST，并核对请求身份', async () => {
    const op=reserveOperation('A',mockTask.file_hash,{name:'A.xlsx',size:1}); saveOperation({...op,status:'completed',taskId:900})
    vi.spyOn(api,'task').mockResolvedValue(mockTask); const post=vi.spyOn(api,'upload')
    const state=useImport(); await state.restoreResult(); expect(state.result.value?.task_id).toBe(900); expect(post).not.toHaveBeenCalled()
    vi.mocked(api.task).mockResolvedValue({...mockTask,customer_id:'B'})
    const mismatched=useImport(); await mismatched.restoreResult(); expect(mismatched.result.value).toBeNull(); expect(mismatched.error.value).toContain('不一致')
  })
})
