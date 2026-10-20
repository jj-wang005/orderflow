import {mount,flushPromises} from '@vue/test-utils'
import {it,expect,vi,beforeEach,afterEach} from 'vitest'
import {createRouter,createMemoryHistory} from 'vue-router'
import ElementPlus,{ElUpload,ElSelect} from 'element-plus'
import BatchImportView from './BatchImportView.vue'
import {customerApi} from '../api/customers'
import {importApi,type BatchResult} from '../api/imports'
import type {TaskSummary} from '../types/api'
const task={task_id:90,customer_id:'D',file_name:'same.xlsx',status:'SUCCESS',status_description:'处理完成',total_rows:1,success_rows:1,unmatched_rows:0,failed_rows:0} as TaskSummary
let wrappers:ReturnType<typeof mount>[]=[]
beforeEach(()=>{localStorage.clear();vi.spyOn(customerApi,'list').mockResolvedValue([{customer_id:'D',customer_name:'D 演示',enabled:true,configured:true,config_version:1}]);vi.spyOn(importApi,'duplicate').mockResolvedValue({file_hash:'a'.repeat(64),previous:null})})
afterEach(()=>{wrappers.forEach(w=>w.unmount());wrappers=[];vi.restoreAllMocks()})
async function setup(){const router=createRouter({history:createMemoryHistory(),routes:[{path:'/import',component:BatchImportView},{path:'/tasks/:id',component:{template:'<div>任务详情</div>'}},{path:'/tasks',component:{template:'<div>历史任务</div>'}}]});await router.push('/import');await router.isReady();const w=mount(BatchImportView,{global:{plugins:[router,ElementPlus]}});wrappers.push(w);await flushPromises();return {w,router}}
function button(w:ReturnType<typeof mount>,text:string){return w.findAll('button').find(b=>b.text()===text)!}
async function add(w:ReturnType<typeof mount>){await w.findComponent(ElUpload).props('onChange')!({raw:new File(['bytes'],'same.xlsx')} as never,[]);await flushPromises();const unified=w.findAllComponents(ElSelect).find(s=>s.props('placeholder')==='手动选择客户')!;unified.vm.$emit('update:modelValue','D');await flushPromises();await button(w,'应用到未提交文件').trigger('click');await flushPromises()}
it('多选入口不自动提交；统一客户后结果按钮直达任务',async()=>{
 const post=vi.spyOn(importApi,'batch').mockImplementation(async(id,items)=>({batch_id:id,items:[{slot_id:items[0].meta.slot_id,file_name:'same.xlsx',customer_id:'D',task,error:null}],summary:{}} as BatchResult));const {w,router}=await setup();expect(w.findComponent(ElUpload).props('multiple')).toBe(true);await add(w);expect(post).not.toHaveBeenCalled();await button(w,'开始批量导入').trigger('click');await flushPromises();expect(post).toHaveBeenCalledTimes(1);await button(w,'查看本次结果').trigger('click');await flushPromises();expect(router.currentRoute.value.path).toBe('/tasks/90')
})
it('重复提示可查看上次结果，显示取消和明确再次导入',async()=>{
 vi.spyOn(importApi,'duplicate').mockResolvedValue({file_hash:'a'.repeat(64),previous:task});const post=vi.spyOn(importApi,'batch');const {w,router}=await setup();await add(w);await button(w,'开始批量导入').trigger('click');await flushPromises();expect(button(w,'确认再次导入').exists()).toBe(true);expect(button(w,'取消').exists()).toBe(true);expect(post).not.toHaveBeenCalled();await button(w,'查看上次结果').trigger('click');await flushPromises();expect(router.currentRoute.value.path).toBe('/tasks/90')
})
