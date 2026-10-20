import {mount,flushPromises,type VueWrapper} from '@vue/test-utils'
import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {createRouter,createMemoryHistory} from 'vue-router'
import ElementPlus,{ElSelect,ElCheckbox,ElMessageBox} from 'element-plus'
import AIExtractionView from './AIExtractionView.vue'
import {ai,type AIDraft} from '../api/ai'
import {customerApi} from '../api/customers'
import {ApiError} from '../api/client'
let wrapper:VueWrapper|undefined
const base=()=>({id:9,customer_id:'D',file_name:'采购文字.txt',source_kind:'TEXT',status:'DRAFT',provider:'mock',revision:0,error_code:null,error_message:null,import_task_id:null,config_version:1,rule_version:1,created_at:'2026-10-14',metrics:{},source_manifest:[{id:'text:1',location:'粘贴文字',text:'X100 再订 200 台，X200 再订 300 台',visual:false}],original_result:{rows:[],warnings:[]},edited_result:{rows:[{customer_order_no:'PO1',customer_material_code:'MAT1',customer_model:'X100',customer_batch:null,quantity:'200',source_id:'text:1',source_excerpt:'X100 再订 200 台',extraction_warnings:[],uncertain_fields:['customer_model']},{customer_order_no:'PO2',customer_material_code:'MAT2',customer_model:'X200',customer_batch:null,quantity:'300',source_id:'text:1',source_excerpt:'X200 再订 300 台',extraction_warnings:[],uncertain_fields:[]}],warnings:[]},audit:[]}) as AIDraft
beforeEach(()=>{
 localStorage.clear()
 vi.spyOn(ai,'capabilities').mockResolvedValue({provider:'mock',model:null,key_configured:false,external_enabled:false,max_bytes:10485760,max_pdf_pages:5,max_rows:100})
 vi.spyOn(customerApi,'list').mockResolvedValue([{customer_id:'D',customer_name:'D 客户',enabled:true,configured:true,config_version:1}])
 vi.spyOn(ai,'list').mockResolvedValue({items:[],total:0})
 vi.spyOn(ai,'get').mockResolvedValue(base())
 vi.spyOn(ElMessageBox,'confirm').mockResolvedValue('confirm' as Awaited<ReturnType<typeof ElMessageBox.confirm>>)
})
afterEach(()=>{wrapper?.unmount();wrapper=undefined;vi.restoreAllMocks();localStorage.clear()})
async function page(path='/ai/9'){
 const router=createRouter({history:createMemoryHistory(),routes:[{path:'/ai/:id?',component:AIExtractionView},{path:'/tasks/:id',component:{template:'<div>任务详情</div>'}}]})
 await router.push(path);await router.isReady();wrapper=mount(AIExtractionView,{global:{plugins:[router,ElementPlus]}});await flushPromises();return {w:wrapper,router}
}
function button(w:VueWrapper,text:string){return w.findAll('button').find(b=>b.text()===text)!}

it('未确认仅显示两行预览，不能自动调用确认',async()=>{
 const confirm=vi.spyOn(ai,'confirm');const {w}=await page()
 expect(w.findAll('.ai-table tbody tr')).toHaveLength(2)
 expect(w.text()).toContain('待核对确认');expect(w.text()).toContain('Mock 演示')
 expect(confirm).not.toHaveBeenCalled();expect(button(w,'确认导入').attributes('disabled')).toBeDefined()
})
it('修改并逐行核实后保存，仍不自动确认',async()=>{
 const save=vi.spyOn(ai,'save').mockImplementation(async(_,body)=>({...base(),edited_result:(body as {result:AIDraft['edited_result']}).result,revision:1}))
 const confirm=vi.spyOn(ai,'confirm');const {w}=await page()
 await w.findAll('.ai-table input')[4].setValue('250')
 await button(w,'已对照原文核实此行').trigger('click');await button(w,'保存预览修正').trigger('click');await flushPromises()
 const submitted=save.mock.calls[0][1] as {result:{rows:{quantity:string;uncertain_fields:string[]}[]}}
 expect(submitted.result.rows[0].quantity).toBe('250');expect(submitted.result.rows[0].uncertain_fields).toEqual([]);expect(confirm).not.toHaveBeenCalled()
})
it('必须勾选并明确确认，成功后导航到对应任务',async()=>{
 const d=base();d.edited_result!.rows[0].uncertain_fields=[];vi.mocked(ai.get).mockResolvedValue(d)
 const confirm=vi.spyOn(ai,'confirm').mockResolvedValue({...d,status:'CONFIRMED',import_task_id:31})
 const {w,router}=await page();w.findAllComponents(ElCheckbox).at(-1)!.vm.$emit('update:modelValue',true);await flushPromises()
 await button(w,'确认导入').trigger('click');await flushPromises();expect(confirm).toHaveBeenCalledTimes(1)
 expect(vi.mocked(ElMessageBox.confirm)).toHaveBeenCalled();expect(w.text()).toContain('已确认导入')
 await button(w,'查看导入结果 · 任务 #31').trigger('click');await flushPromises();expect(router.currentRoute.value.path).toBe('/tasks/31')
})
it('确认响应丢失保持原键，重试复用相同正文',async()=>{
 const d=base();d.edited_result!.rows[0].uncertain_fields=[];vi.mocked(ai.get).mockResolvedValue(d)
 const confirm=vi.spyOn(ai,'confirm').mockRejectedValueOnce(new ApiError('网络响应丢失')).mockResolvedValueOnce({...base(),status:'CONFIRMED',import_task_id:31})
 const {w}=await page();w.findAllComponents(ElCheckbox).at(-1)!.vm.$emit('update:modelValue',true);await flushPromises()
 await button(w,'确认导入').trigger('click');await flushPromises();expect(button(w,'使用原请求继续确认').exists()).toBe(true)
 await button(w,'使用原请求继续确认').trigger('click');await flushPromises();expect(confirm.mock.calls[0][1]).toEqual(confirm.mock.calls[1][1])
})
it('确定的422拒绝后解除确认锁，允许继续修改',async()=>{
 const d=base();d.edited_result!.rows[0].uncertain_fields=[];vi.mocked(ai.get).mockResolvedValue(d)
 vi.spyOn(ai,'confirm').mockRejectedValue(new ApiError('缺少必填字段',422,'INCOMPLETE_PURCHASE'))
 const {w}=await page();w.findAllComponents(ElCheckbox).at(-1)!.vm.$emit('update:modelValue',true);await flushPromises()
 await button(w,'确认导入').trigger('click');await flushPromises();expect(w.text()).toContain('缺少必填字段')
 expect(w.find('.ai-table input').attributes('disabled')).toBeUndefined();expect(localStorage.getItem('cvte.ai.confirm.9')).toBeNull()
})
it('失败草稿可重试，不能直接确认',async()=>{
 vi.mocked(ai.get).mockResolvedValue({...base(),status:'FAILED',error_message:'解析超时',edited_result:null})
 const retry=vi.spyOn(ai,'retry').mockResolvedValue(base());const {w}=await page();expect(w.text()).toContain('解析超时');expect(w.text()).not.toContain('采购明细预览')
 await button(w,'重试原草稿解析').trigger('click');await flushPromises();expect(retry).toHaveBeenCalledWith(9,{expected_revision:0,external_authorized:false});expect(w.text()).toContain('采购明细预览')
})
it('新解析必须手动选择客户；解析成功只跳到草稿',async()=>{
 const create=vi.spyOn(ai,'create').mockResolvedValue(base());const confirm=vi.spyOn(ai,'confirm');const {w,router}=await page('/ai')
 w.findComponent(ElSelect).vm.$emit('update:modelValue','D');await w.find('textarea').setValue('X100 再订 200 台，X200 再订 300 台。')
 await button(w,'智能解析').trigger('click');await flushPromises();expect(create).toHaveBeenCalledTimes(1);expect(create.mock.calls[0][0].get('customer_id')).toBe('D');expect(confirm).not.toHaveBeenCalled();expect(router.currentRoute.value.path).toBe('/ai/9')
})
it('外部模式显示逐输入授权，默认不勾选',async()=>{
 vi.mocked(ai.capabilities).mockResolvedValue({provider:'mimo',model:'mimo-v2.6-flash',key_configured:true,external_enabled:true,max_bytes:10485760,max_pdf_pages:5,max_rows:100})
 const create=vi.spyOn(ai,'create');const {w}=await page('/ai');expect(w.text()).toContain('数据使用确认');expect(w.text()).toContain('不包含未经授权对外传输的客户保密信息');expect(w.findComponent(ElCheckbox).props('modelValue')).toBe(false)
 w.findComponent(ElSelect).vm.$emit('update:modelValue','D');await w.find('textarea').setValue('X100 再订 200 台')
 await button(w,'智能解析').trigger('click');await flushPromises();expect(create).not.toHaveBeenCalled();expect(localStorage.getItem('cvte.ai.parse')).toBeNull()
})

it('按草稿快照就近显示缺失字段并定位首个错误，不提交后端',async()=>{
 const d=base();d.required_fields=['customer_order_no','customer_material_code','quantity'];d.edited_result!.rows[0].customer_order_no=null;d.edited_result!.rows[0].uncertain_fields=[];vi.mocked(ai.get).mockResolvedValue(d)
 const scroll=vi.fn();Object.defineProperty(HTMLElement.prototype,'scrollIntoView',{value:scroll,configurable:true})
 const confirm=vi.spyOn(ai,'confirm');const {w}=await page();w.findAllComponents(ElCheckbox).at(-1)!.vm.$emit('update:modelValue',true);await flushPromises()
 await button(w,'确认导入').trigger('click');await flushPromises()
 expect(w.find('.ai-field .field-error').text()).toContain('客户采购单号不能为空');expect(scroll).toHaveBeenCalled();expect(confirm).not.toHaveBeenCalled()
 delete (HTMLElement.prototype as Partial<HTMLElement>).scrollIntoView
})

it('非法数量可保存草稿，确认时显示格式错误',async()=>{
 const d=base();d.edited_result!.rows[0].uncertain_fields=[];vi.mocked(ai.get).mockResolvedValue(d)
 const save=vi.spyOn(ai,'save').mockImplementation(async(_,body)=>({...d,edited_result:(body as {result:AIDraft['edited_result']}).result,revision:1}))
 const confirm=vi.spyOn(ai,'confirm');const {w}=await page();await w.findAll('.ai-table input')[4].setValue('1.5');await button(w,'保存预览修正').trigger('click');await flushPromises()
 expect(save).toHaveBeenCalled();w.findAllComponents(ElCheckbox).at(-1)!.vm.$emit('update:modelValue',true);await flushPromises();await button(w,'确认导入').trigger('click');await flushPromises()
 expect(w.find('.field-error').text()).toContain('正整数');expect(confirm).not.toHaveBeenCalled()
})

it('移除的草稿行可恢复且随修正保存，未创建订单',async()=>{
 const save=vi.spyOn(ai,'save').mockImplementation(async(_,body)=>({...base(),edited_result:(body as {result:AIDraft['edited_result']}).result,revision:1}));const confirm=vi.spyOn(ai,'confirm');const {w}=await page()
 await w.findAll('button').find(b=>b.text()==='移除此行')!.trigger('click');await flushPromises();expect(w.findAll('.ai-table tbody tr')).toHaveLength(1)
 await button(w,'保存预览修正').trigger('click');await flushPromises();expect((save.mock.calls[0][1] as {result:{removed_rows:unknown[]}}).result.removed_rows).toHaveLength(1)
 await button(w,'恢复此行').trigger('click');await flushPromises();expect(w.findAll('.ai-table tbody tr')).toHaveLength(2);expect(confirm).not.toHaveBeenCalled()
})

it('AI风险在行内提示并阻止确认',async()=>{
 const confirm=vi.spyOn(ai,'confirm');const {w}=await page();w.findAllComponents(ElCheckbox).at(-1)!.vm.$emit('update:modelValue',true);await flushPromises();await button(w,'确认导入').trigger('click');await flushPromises()
 expect(w.find('.field-error').text()).toContain('AI识别风险');expect(confirm).not.toHaveBeenCalled()
})
