import { beforeEach, afterEach, describe, it, expect, vi } from 'vitest'
import { ACTIVE_KEY, readOperation, reserveOperation, saveOperation, clearConfirmedOperation, fileHash, rememberTask, recentTasks, clearRecentTasks } from './storage'
import type { TaskSummary } from '../types/api'
const hash = 'a'.repeat(64), file = { name: 'A.xlsx', size: 12 }
beforeEach(() => { localStorage.clear(); vi.restoreAllMocks() })
afterEach(() => vi.unstubAllGlobals())
describe('请求身份及持久化恢复', () => {
  it('清空本地入口不丢弃当前导入键', () => {
    const op=reserveOperation('A',hash,file)
    rememberTask({task_id:1,customer_id:'A',file_name:'A.xlsx'} as TaskSummary)
    expect(recentTasks()).toHaveLength(1); clearRecentTasks()
    expect(recentTasks()).toHaveLength(0); expect(readOperation()?.key).toBe(op.key)
  })
  it('真实 SHA-256 对字节身份计算，与文件名称无关', async () => {
    const a = new File(['abc'], 'one.xlsx'), b = new File(['abc'], 'renamed.xlsx'), c = new File(['abcd'], 'one.xlsx')
    expect(await fileHash(a)).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
    expect(await fileHash(a)).toBe(await fileHash(b)); expect(await fileHash(a)).not.toBe(await fileHash(c))
  })
  it('同客户同字节复用操作，不因重新点击或改名生成新键', () => {
    const first = reserveOperation('A', hash, file)
    expect(reserveOperation('A',hash,{...file,name:'renamed.xlsx'}).key).toBe(first.key)
    expect(readOperation()?.key).toBe(first.key)
  })
  it.each(['customer', 'content'])('不将原键用于不同请求：%s', change => {
    const op = reserveOperation('A',hash,file)
    expect(() => reserveOperation(change === 'customer' ? 'B' : 'A',change === 'content' ? 'b'.repeat(64) : hash,file)).toThrow('不一致')
    expect(readOperation()?.key).toBe(op.key)
  })
  it('刷新后保留未确认键，并阻止直接新建采购', () => {
    const op = reserveOperation('A',hash,file); saveOperation({...op,status:'uncertain'})
    expect(readOperation()?.status).toBe('uncertain'); expect(() => clearConfirmedOperation()).toThrow('尚未确认')
    expect(reserveOperation('A',hash,file).key).toBe(op.key)
  })
  it('确认完成后明确新建才换键', () => {
    const op = reserveOperation('A',hash,file); saveOperation({...op,status:'completed',taskId:1})
    clearConfirmedOperation(); expect(reserveOperation('A',hash,file).key).not.toBe(op.key)
  })
  it('损坏恢复记录不被静默丢弃', () => {
    localStorage.setItem(ACTIVE_KEY,'broken')
    expect(readOperation).toThrow('无法读取'); expect(() => reserveOperation('A',hash,file)).toThrow()
  })
  it.each([undefined,12345,null])('缺失或错误类型的键 %s 不能转成无键上传', key => {
    const op=reserveOperation('A',hash,file)
    localStorage.setItem(ACTIVE_KEY,JSON.stringify({...op,key}))
    expect(readOperation).toThrow('无法读取')
    expect(() => reserveOperation('A',hash,file)).toThrow()
  })
  it('损坏的完成任务 ID 拒绝恢复', () => {
    const op=reserveOperation('A',hash,file)
    localStorage.setItem(ACTIVE_KEY,JSON.stringify({...op,status:'completed',taskId:0}))
    expect(readOperation).toThrow('无法读取')
  })
  it('无法持久保存请求键时拒绝发起新操作', () => {
    vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => { throw new Error('quota') } })
    expect(() => reserveOperation('A',hash,file)).toThrow('尚未发送')
  })
})

it('新增客户的导入恢复记录可读取且保留原幂等键',()=>{const op=reserveOperation('D',hash,file);expect(readOperation()?.customerId).toBe('D');expect(reserveOperation('D',hash,file).key).toBe(op.key)})
