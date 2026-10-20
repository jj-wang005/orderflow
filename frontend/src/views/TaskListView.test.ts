import {mount,flushPromises} from '@vue/test-utils'
import {it,expect,vi,beforeEach,afterEach} from 'vitest'
import {createRouter,createMemoryHistory} from 'vue-router'
import ElementPlus from 'element-plus'
import TaskListView from './TaskListView.vue'
import {customerApi} from '../api/customers'
import {importApi} from '../api/imports'
import type {TaskSummary} from '../types/api'
const task={task_id:31,customer_id:'D',file_name:'broken.xlsx',status:'FAILED',total_rows:0,success_rows:0,unmatched_rows:0,failed_rows:0,error_message:'损坏文件',config_version:1} as TaskSummary
let wrappers:ReturnType<typeof mount>[]=[]
beforeEach(()=>{vi.spyOn(customerApi,'list').mockResolvedValue([]);vi.spyOn(importApi,'tasks').mockResolvedValue({total:40,page:1,page_size:20,items:[task]})})
afterEach(()=>{wrappers.forEach(w=>w.unmount());wrappers=[];vi.restoreAllMocks()})
async function setup(){const router=createRouter({history:createMemoryHistory(),routes:[{path:'/tasks',component:TaskListView},{path:'/tasks/:id',component:{template:'<div>详情</div>'}},{path:'/import',component:{template:'<div>导入</div>'}}]});await router.push('/tasks');await router.isReady();const w=mount(TaskListView,{global:{plugins:[router,ElementPlus]}});wrappers.push(w);await flushPromises();return {w,router}}
it('默认浏览数据库历史，不要求任务 ID，查看按钮直接导航',async()=>{const {w,router}=await setup();expect(w.text()).toContain('broken.xlsx');expect(w.find('input[placeholder*=任务编号]').exists()).toBe(false);await w.findAll('button').find(b=>b.text()==='查看详情')!.trigger('click');await flushPromises();expect(router.currentRoute.value.path).toBe('/tasks/31')})
it('文件名筛选和分页送到后端',async()=>{const {w}=await setup();await w.find('input[aria-label="文件名筛选"]').setValue('采购');await w.find('form').trigger('submit');await flushPromises();expect(importApi.tasks).toHaveBeenLastCalledWith(expect.objectContaining({file_name:'采购',page:1}));await w.find('button.btn-next').trigger('click');await flushPromises();expect(importApi.tasks).toHaveBeenLastCalledWith(expect.objectContaining({page:2}))})
it('文件级失败的重新上传链接保留原任务',async()=>{const {w,router}=await setup();await w.findAll('button').find(b=>b.text()==='重新上传')!.trigger('click');await flushPromises();expect(router.currentRoute.value.query.retry_of).toBe('31')})
