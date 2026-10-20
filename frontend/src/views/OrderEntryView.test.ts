import {mount,flushPromises,type VueWrapper} from '@vue/test-utils'
import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {createRouter,createMemoryHistory,type Router} from 'vue-router'
import ElementPlus,{ElUpload,ElSelect,ElMessageBox} from 'element-plus'
import App from '../App.vue'
import {routes} from '../router'
import OrderEntryView from './OrderEntryView.vue'
import OrdersView from './OrdersView.vue'
import AIExtractionView from './AIExtractionView.vue'
import {customerApi} from '../api/customers'
import {api} from '../api/orders'
import {ai,type AIDraft} from '../api/ai'
import {importApi} from '../api/imports'
import {BATCH_STORAGE} from '../composables/useBatchImport'
import {ApiError} from '../api/client'

let w:VueWrapper|undefined
const draft=(id=9)=>({id,customer_id:'D',status:'DRAFT',file_name:'采购文字',revision:0,config_version:1,rule_version:1,required_fields:['quantity'],source_manifest:[],edited_result:{rows:[],warnings:[]},original_result:null,audit:[]}) as unknown as AIDraft
beforeEach(()=>{
 localStorage.clear()
 vi.spyOn(customerApi,'list').mockResolvedValue([{customer_id:'D',customer_name:'测试客户',enabled:true,configured:true,config_version:1}])
 vi.spyOn(api,'health').mockResolvedValue({status:'ok',database:'ok',environment:'test',database_name:'order_test'} as never)
 vi.spyOn(api,'orders').mockResolvedValue({total:0,page:1,page_size:20,items:[]})
 vi.spyOn(ai,'capabilities').mockResolvedValue({provider:'mock',model:null,key_configured:false,external_enabled:false,max_bytes:10485760,max_pdf_pages:5,max_rows:100})
 vi.spyOn(ai,'list').mockResolvedValue({items:[],total:0})
 vi.spyOn(ai,'get').mockImplementation(async id=>draft(id))
 vi.spyOn(ai,'create').mockResolvedValue(draft())
 vi.spyOn(ai,'confirm').mockResolvedValue(draft())
 vi.spyOn(importApi,'batch')
})
afterEach(()=>{w?.unmount();w=undefined;vi.restoreAllMocks();localStorage.clear()})
function routerForTest(){return createRouter({history:createMemoryHistory(),routes:routes.map(r=>r.redirect?r:{path:r.path,name:r.name,meta:r.meta,component:r.name==='order-entry'?OrderEntryView:r.path==='/orders'?OrdersView:{template:'<div>原业务页面</div>'}})})}
async function page(path='/order-entry'){
 const router=routerForTest();await router.push(path);await router.isReady()
 w=mount(App,{global:{plugins:[router,ElementPlus]}});await flushPromises();return router
}
const button=(text:string)=>w!.findAll('button').find(b=>b.text()===text)!
async function mode(router:Router,value:string){await router.push({...router.currentRoute.value,query:{...router.currentRoute.value.query,mode:value}});await flushPromises()}

it('根路径复用销售订单；导航分组、高亮、标题与快捷入口同步',async()=>{
 const router=await page('/')
 expect(router.currentRoute.value.path).toBe('/orders')
 expect(w!.findAll('.nav-label').map(x=>x.text())).toEqual(['业务管理','基础管理'])
 expect(w!.findAll('.nav-item').map(x=>x.text())).toEqual(['销售订单','订单录入','导入任务','客户配置管理'])
 expect(w!.find('.nav-item.selected').text()).toBe('销售订单')
 expect(w!.find('.page-heading').text()).not.toContain('导出 Excel')
 expect(w!.find('.list-actions').text()).toContain('导出 Excel')
 await button('查看历史任务').trigger('click');await flushPromises();expect(router.currentRoute.value.path).toBe('/tasks')
 await router.push('/orders');await flushPromises();await button('新建录单').trigger('click');await flushPromises()
 expect(router.currentRoute.value.path).toBe('/order-entry');expect(w!.find('.nav-item.selected').text()).toBe('订单录入')
 expect(document.title).toBe('订单录入 · OrderFlow · 销管工作台');expect(w!.find('.breadcrumb').text()).toContain('订单录入')
 expect(w!.findAll('.cdou')).toHaveLength(1)
})

it('旧地址保留草稿 ID、重复查询参数、修复重传上下文及 hash',async()=>{
 const router=routerForTest()
 await router.push('/ai/17?context=a&context=b#preview')
 expect(router.currentRoute.value.path).toBe('/order-entry/17')
 expect(router.currentRoute.value.query).toEqual({context:['a','b'],mode:'ai'})
 expect(router.currentRoute.value.hash).toBe('#preview')
 await router.push('/import?retry_of=21#files')
 expect(router.currentRoute.value.fullPath).toBe('/order-entry?retry_of=21&mode=excel#files')
 await router.push('/import/single');expect(router.currentRoute.value.meta.activeMenu).toBe('/order-entry')
})

it('默认 Excel 不挂载 AI；模式切换及任务往返保留真实 File、客户和幂等键',async()=>{
 const router=await page()
 expect(ai.capabilities).not.toHaveBeenCalled();expect(w!.find('h1').text()).toBe('订单录入')
 const upload=w!.findComponent(ElUpload)
 for(const name of ['first.xlsx','second.xlsx'])await upload.props('onChange')!({raw:new File([name],name)} as never,[])
 const select=w!.findAllComponents(ElSelect).find(s=>s.props('placeholder')==='手动选择客户')!
 select.vm.$emit('update:modelValue','D');await flushPromises();await button('应用到未提交文件').trigger('click');await flushPromises()
 const before=localStorage.getItem(BATCH_STORAGE)
 await mode(router,'ai');expect(w!.findComponent(AIExtractionView).exists()).toBe(true)
 await w!.find('textarea').setValue('尚未提交的采购文字')
 await mode(router,'excel');await router.push('/tasks/18');await flushPromises();await router.push('/order-entry');await flushPromises()
 expect(button('开始批量导入').attributes('disabled')).toBeUndefined()
 expect(localStorage.getItem(BATCH_STORAGE)).toBe(before)
 expect(w!.text()).toContain('first.xlsx');expect(w!.text()).toContain('second.xlsx')
 await mode(router,'ai');expect((w!.find('textarea').element as HTMLTextAreaElement).value).toBe('尚未提交的采购文字')
 expect(ai.create).not.toHaveBeenCalled();expect(importApi.batch).not.toHaveBeenCalled()
 const unload=new Event('beforeunload',{cancelable:true});window.dispatchEvent(unload);expect(unload.defaultPrevented).toBe(true)
})

it('AI 解析只生成待确认草稿，切换后保留草稿与原确认请求',async()=>{
 const router=await page('/order-entry?mode=ai')
 const select=w!.findComponent(AIExtractionView).findComponent(ElSelect)
 select.vm.$emit('update:modelValue','D');await w!.find('textarea').setValue('模拟采购内容')
 await button('智能解析').trigger('click');await flushPromises()
 expect(router.currentRoute.value.path).toBe('/order-entry/9');expect(ai.confirm).not.toHaveBeenCalled()
 localStorage.setItem('cvte.ai.confirm.9',JSON.stringify({request_key:'original-confirm',confirmed:true,expected_revision:0}))
 await mode(router,'excel');await router.push('/tasks/3');await flushPromises();await router.push('/order-entry?mode=ai');await flushPromises()
 expect(w!.text()).toContain('解析草稿 #9');expect(localStorage.getItem('cvte.ai.confirm.9')).toContain('original-confirm')
 expect(ai.get).not.toHaveBeenCalled()
})

it('从任务页打开其他草稿前保护未保存输入，取消后原输入仍在',async()=>{
 const router=await page('/order-entry?mode=ai');await w!.find('textarea').setValue('保留这份输入')
 await router.push('/tasks');await flushPromises()
 const confirm=vi.spyOn(ElMessageBox,'confirm').mockRejectedValue('cancel')
 await router.push('/ai/22');await flushPromises()
 expect(confirm).toHaveBeenCalled();expect(router.currentRoute.value.path).toBe('/tasks');expect(ai.get).not.toHaveBeenCalled()
 await router.push('/order-entry?mode=ai');await flushPromises();expect((w!.find('textarea').element as HTMLTextAreaElement).value).toBe('保留这份输入')
})

it('缓存批次收到修复重传链接时不清空未提交文件',async()=>{
 const router=await page();await w!.findComponent(ElUpload).props('onChange')!({raw:new File(['x'],'pending.xlsx')} as never,[])
 vi.spyOn(api,'task').mockResolvedValue({task_id:21,customer_id:'D',status:'FAILED',total_rows:0,error_message:'文件级失败'} as never)
 await router.push('/tasks');await flushPromises();await router.push('/import?retry_of=21');await flushPromises()
 expect(w!.text()).toContain('当前批次仍有未完成文件');expect(w!.text()).toContain('pending.xlsx')
})

it('确认响应丢失后跨模式及页面仍使用原确认键和正文',async()=>{
 const value=draft();value.edited_result={warnings:[],rows:[{customer_order_no:null,customer_material_code:null,customer_model:'X100',customer_batch:null,quantity:'200',source_id:'text:1',source_excerpt:'X100 再订 200 台',extraction_warnings:[],uncertain_fields:[]}]}
 vi.mocked(ai.get).mockResolvedValue(value)
 vi.spyOn(ElMessageBox,'confirm').mockResolvedValue('confirm' as Awaited<ReturnType<typeof ElMessageBox.confirm>>)
 const confirm=vi.mocked(ai.confirm).mockRejectedValueOnce(new ApiError('响应丢失')).mockResolvedValueOnce({...value,status:'CONFIRMED',import_task_id:31})
 const router=await page('/ai/9')
 const checkbox=w!.findComponent(AIExtractionView).find('input[type=checkbox]');await checkbox.setValue(true)
 await button('确认导入').trigger('click');await flushPromises();const original=localStorage.getItem('cvte.ai.confirm.9')
 await mode(router,'excel');await router.push('/tasks/31');await flushPromises();await router.push('/order-entry?mode=ai');await flushPromises()
 expect(localStorage.getItem('cvte.ai.confirm.9')).toBe(original)
 await button('使用原请求继续确认').trigger('click');await flushPromises()
 expect(confirm).toHaveBeenCalledTimes(2);expect(confirm.mock.calls[1]![1]).toEqual(confirm.mock.calls[0]![1])
 expect(w!.text()).toContain('已确认导入');expect(localStorage.getItem('cvte.ai.confirm.9')).toBeNull()
})

it('解析进行中切换模式或离开再返回不会重复提交或丢失解析结果',async()=>{
 let finish!:(value:AIDraft)=>void
 vi.mocked(ai.create).mockImplementation(()=>new Promise(resolve=>{finish=resolve}))
 const router=await page('/order-entry?mode=ai')
 w!.findComponent(AIExtractionView).findComponent(ElSelect).vm.$emit('update:modelValue','D')
 await w!.find('textarea').setValue('X100 再订 200 台');await button('智能解析').trigger('click');await flushPromises()
 const operation=localStorage.getItem('cvte.ai.parse');expect(operation).toBeTruthy()
 await mode(router,'excel');await router.push('/tasks');await flushPromises()
 expect(localStorage.getItem('cvte.ai.parse')).toBe(operation);expect(ai.create).toHaveBeenCalledTimes(1)
 finish(draft());await flushPromises()
 expect(router.currentRoute.value.fullPath).toBe('/order-entry/9?mode=ai')
 expect(w!.text()).toContain('解析草稿 #9');expect(ai.create).toHaveBeenCalledTimes(1);expect(ai.confirm).not.toHaveBeenCalled()
})
