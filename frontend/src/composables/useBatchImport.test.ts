import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {useBatchImport,BATCH_STORAGE} from './useBatchImport'
import {importApi,type BatchResult} from '../api/imports'
import {api} from '../api/orders'
import type {TaskSummary} from '../types/api'
const file=(name='purchase.xlsx',content='workbook')=>new File([content],name)
const task={task_id:12,customer_id:'D',file_name:'purchase.xlsx',status:'SUCCESS',status_description:'处理完成',total_rows:1,success_rows:1,unmatched_rows:0,failed_rows:0,duplicate_rows:0,review_rows:0,idempotency_replayed:false} as TaskSummary
beforeEach(()=>{localStorage.clear();vi.spyOn(importApi,'duplicate').mockResolvedValue({file_hash:'a'.repeat(64),previous:null})})
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals()})
function successful(){return vi.spyOn(importApi,'batch').mockImplementation(async(id,items)=>({batch_id:id,items:items.map(e=>({slot_id:e.meta.slot_id,file_name:e.file.name,customer_id:e.meta.customer_id,task:{...task,customer_id:e.meta.customer_id},error:null})),summary:{}} as BatchResult))}
it('多文件不猜客户，必须每份指定；统一客户只影响未提交文件',async()=>{
 const post=successful(),s=useBatchImport();await s.addFiles([file('A.xlsx'),file('D.xlsx','other')]);expect(s.entries.value.map(e=>e.customer_id)).toEqual(['','']);expect(s.canSubmit.value).toBe(false)
 await s.execute();expect(post).not.toHaveBeenCalled();s.assign('D');expect(s.canSubmit.value).toBe(true);await s.execute();expect(post.mock.calls[0][1].map(e=>e.meta.customer_id)).toEqual(['D','D']);s.assign('E');expect(s.entries.value[0].customer_id).toBe('D')
})
it('重复提示提供上次结果，明确确认后才能再次提交',async()=>{
 vi.spyOn(importApi,'duplicate').mockResolvedValue({file_hash:'a'.repeat(64),previous:task});const post=successful(),s=useBatchImport();await s.addFiles([file()]);s.assign('D');await s.execute();expect(post).not.toHaveBeenCalled();expect(s.entries.value[0].previous?.task_id).toBe(12)
 await s.confirmAgain(s.entries.value[0]);expect(post.mock.calls[0][1][0].meta.duplicate_confirmed).toBe(true)
})
it('取消重复文件不会创建任务',async()=>{
 vi.spyOn(importApi,'duplicate').mockResolvedValue({file_hash:'a'.repeat(64),previous:task});const post=successful(),s=useBatchImport();await s.addFiles([file()]);s.assign('D');await s.execute();s.cancel(s.entries.value[0]);expect(s.canSubmit.value).toBe(false);expect(post).not.toHaveBeenCalled()
})
it('请求发送前保存每个稳定键，连击不产生第二批请求',async()=>{
 let finish!:(r:BatchResult)=>void;const post=vi.spyOn(importApi,'batch').mockImplementation(async(id,items)=>{const persisted=JSON.parse(localStorage.getItem(BATCH_STORAGE)!);expect(persisted.entries[0].sent).toBe(true);expect(persisted.entries[0].request_key).toBe(items[0].meta.request_key);return new Promise(resolve=>{finish=resolve})})
 const s=useBatchImport();await s.addFiles([file()]);s.assign('D');const first=s.execute();await s.execute();await vi.waitFor(()=>expect(post).toHaveBeenCalledTimes(1));finish({batch_id:s.batchId.value,items:[{slot_id:s.entries.value[0].slot_id,file_name:'purchase.xlsx',customer_id:'D',task,error:null}],summary:{}} as BatchResult);await first
})
it('网络失败刷新后恢复原文件及键，不把网络重试误当主动重传',async()=>{
 const post=vi.spyOn(importApi,'batch').mockRejectedValueOnce(new Error('网络结果待确认'));const first=useBatchImport();await first.addFiles([file()]);first.assign('D');await first.execute();const key=first.entries.value[0].request_key,bid=first.batchId.value
 const restored=useBatchImport();expect(restored.entries.value[0].file).toBeNull();await restored.addFiles([file()]);successful();await restored.execute();expect(post.mock.calls.at(-1)?.[0]).toBe(bid);expect(post.mock.calls.at(-1)?.[1][0].meta.request_key).toBe(key);expect(importApi.duplicate).toHaveBeenCalledTimes(1)
})
it('存储失败阻止发送；损坏恢复记录阻止新建',async()=>{
 const post=successful(),s=useBatchImport();await s.addFiles([file()]);s.assign('D');vi.stubGlobal('localStorage',{getItem:()=>null,setItem:()=>{throw new Error('denied')}});await s.execute();expect(post).not.toHaveBeenCalled();expect(s.error.value).toContain('恢复记录');vi.unstubAllGlobals();localStorage.setItem(BATCH_STORAGE,'broken');const broken=useBatchImport();await broken.addFiles([file()]);expect(broken.blocked.value).toBe(true);expect(broken.canSubmit.value).toBe(false)
})
it('文件失败结果有真实任务，正常文件结果保留，汇总不虚构行',async()=>{
 const s=useBatchImport();await s.addFiles([file(),file('bad.xlsx','bad')]);s.assign('D');vi.spyOn(importApi,'batch').mockImplementation(async(id,items)=>({batch_id:id,items:items.map((e,i)=>({slot_id:e.meta.slot_id,file_name:e.file.name,customer_id:'D',task:i?{...task,task_id:13,status:'FAILED',total_rows:0,success_rows:0,error_message:'文件损坏'}:task,error:null})),summary:{}} as BatchResult));await s.execute();expect(s.summary.value).toMatchObject({files:2,completed:2,success:1,fileErrors:1});expect(s.entries.value[1].task?.total_rows).toBe(0)
})
it('修复重传固定原客户和关联任务，不根据文件名改变',async()=>{
 const post=successful(),s=useBatchImport();await s.addFiles([file('E.xlsx')],{task_id:50,customer_id:'D'});s.assign('E');await s.execute();expect(post.mock.calls[0][1][0].meta).toMatchObject({customer_id:'D',retry_of_task_id:50})
})

it('核实或补录后刷新批次汇总读取任务最新结果',async()=>{successful();const s=useBatchImport();await s.addFiles([file()]);s.assign('D');await s.execute();vi.spyOn(api,'task').mockResolvedValue({...task,success_rows:2});await s.refreshResults();expect(s.summary.value.success).toBe(2)})
