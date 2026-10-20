import {mount,flushPromises} from '@vue/test-utils'
import {it,expect,vi} from 'vitest'
import {createRouter,createMemoryHistory} from 'vue-router'
import ElementPlus from 'element-plus'
import TaskView from './TaskView.vue'
import {api} from '../api/orders'
import {importApi} from '../api/imports'
import type {TaskSummary} from '../types/api'
it('文件失败详情不虚构行，展示重传关联和重新上传入口',async()=>{
 const failed={task_id:11,customer_id:'D',file_name:'bad.xlsx',status:'FAILED',total_rows:0,success_rows:0,unmatched_rows:0,failed_rows:0,pending_rows:0,error_message:'文件内容损坏',error_summary:'文件内容损坏',config_version:1,rule_version:1} as TaskSummary
 vi.spyOn(api,'task').mockResolvedValue(failed);vi.spyOn(importApi,'related').mockResolvedValue({parent:null,retries:[{...failed,task_id:12}]});const query=vi.spyOn(api,'rows')
 const router=createRouter({history:createMemoryHistory(),routes:[{path:'/tasks/:id',component:TaskView},{path:'/tasks',component:{template:'<div>历史</div>'}},{path:'/import',component:{template:'<div>上传</div>'}}]});await router.push('/tasks/11');await router.isReady();const w=mount(TaskView,{global:{plugins:[router,ElementPlus]}});await flushPromises();expect(w.text()).toContain('文件内容损坏');expect(w.text()).toContain('修复重传任务 #12');expect(query).not.toHaveBeenCalled();await w.findAll('button').find(b=>b.text()==='修复文件并重新上传')!.trigger('click');await flushPromises();expect(router.currentRoute.value.query.retry_of).toBe('11');w.unmount();vi.restoreAllMocks()
})
