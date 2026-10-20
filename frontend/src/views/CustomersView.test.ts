import {mount,flushPromises} from '@vue/test-utils'
import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import ElementPlus,{ElMessageBox,ElSelect,ElCheckboxGroup,ElUpload,ElInputNumber} from 'element-plus'
import CustomersView from './CustomersView.vue'
import {customerApi,type CustomerDetail} from '../api/customers'
import {ApiError} from '../api/client'
const draft={customer_id:'D',customer_name:'D 客户',config_version:0,enabled:false,configured:false,configuration:null,versions:[]} as CustomerDetail
const saved={...draft,enabled:true,configured:true,config_version:1,configuration:{customer:{sheet_name:'Sheet1',header_row:1,field_mapping:{'单号':'customer_order_no','数量':'quantity'}},rule:{match_fields:['customer_order_no'],validation_config:{consistency_fields:[]},filter_conditions:{historical_quantity:null}}}}
const template={id:'A',name:'采购单号 + 料号一致',match_fields:['customer_order_no','customer_material_code'],historical_quantity:null}
let wrappers:ReturnType<typeof mount>[]=[]
beforeEach(()=>{vi.spyOn(customerApi,'list').mockResolvedValue([draft]);vi.spyOn(customerApi,'get').mockResolvedValue(draft);vi.spyOn(customerApi,'templates').mockResolvedValue({templates:[template]});vi.spyOn(ElMessageBox,'confirm').mockResolvedValue('confirm' as Awaited<ReturnType<typeof ElMessageBox.confirm>>)})
afterEach(()=>{wrappers.forEach(w=>w.unmount());wrappers=[];vi.restoreAllMocks()})
async function mountPage(){const w=mount(CustomersView,{global:{plugins:[ElementPlus]}});wrappers.push(w);await flushPromises();return w}
function button(w:ReturnType<typeof mount>,label:string){return w.findAll('button').find(b=>b.text()===label)!}

it('列表显示新客户并打开首次配置，不自动保存或建单',async()=>{
 const save=vi.spyOn(customerApi,'save');const w=await mountPage();expect(w.text()).toContain('D 客户');expect(w.text()).toContain('待配置')
 await button(w,'首次配置').trigger('click');await flushPromises();expect(w.text()).toContain('上传 Excel 样例');expect(save).not.toHaveBeenCalled()
 expect(button(w,'确认保存正式配置').attributes('disabled')).toBeDefined()
})
it('有把握的映射自动填入，未知列可人工映射，模板按可用字段启用',async()=>{
 vi.spyOn(customerApi,'suggest').mockResolvedValue({sheet_names:['采购'],sheet_name:'采购',header_row:1,templates:[],columns:[{column:'单号',samples:['PO'],recommended_field:'customer_order_no',reason:'别名一致'},{column:'数量',samples:[12],recommended_field:'quantity',reason:'类型一致'},{column:'未知物料',samples:['MAT'],recommended_field:null,reason:'请人工选择'}]})
 const w=await mountPage();await button(w,'首次配置').trigger('click');await flushPromises()
 const file=new File(['xlsx'],'sample.xlsx');await w.findComponent(ElUpload).props('onChange')!({raw:file} as never,[]);await flushPromises()
 expect(button(w,template.name).attributes('disabled')).toBeDefined()
 const selectors=w.findAllComponents(ElSelect).filter(s=>s.props('placeholder')==='不映射 / 人工选择')
 selectors[2].vm.$emit('update:modelValue','customer_material_code');selectors[2].vm.$emit('change','customer_material_code');await flushPromises()
 expect(button(w,template.name).attributes('disabled')).toBeUndefined();await button(w,template.name).trigger('click');await flushPromises()
 expect(w.findComponent(ElCheckboxGroup).props('modelValue')).toEqual(template.match_fields)
 expect(button(w,'确认保存正式配置').attributes('disabled')).toBeUndefined()
})
it('已有配置无需重新上传或试匹配即可保存，携带原版本',async()=>{
 vi.spyOn(customerApi,'list').mockResolvedValue([saved]);vi.spyOn(customerApi,'get').mockResolvedValue(saved)
 const save=vi.spyOn(customerApi,'save').mockResolvedValue({...saved,config_version:2});const preview=vi.spyOn(customerApi,'preview');const w=await mountPage()
 await button(w,'维护配置').trigger('click');await flushPromises();await button(w,'确认保存正式配置').trigger('click');await flushPromises()
 expect(save).toHaveBeenCalledWith('D',expect.objectContaining({expected_version:1,match_fields:['customer_order_no'],field_mapping:{'单号':'customer_order_no','数量':'quantity'}}));expect(preview).not.toHaveBeenCalled()
})
it('版本冲突展示后端提示并保留表单，不覆盖新配置',async()=>{
 vi.spyOn(customerApi,'list').mockResolvedValue([saved]);vi.spyOn(customerApi,'get').mockResolvedValue(saved)
 vi.spyOn(customerApi,'save').mockRejectedValue(new Error('配置已被其他操作修改，请重新打开后核对'));const w=await mountPage()
 await button(w,'维护配置').trigger('click');await flushPromises();await button(w,'确认保存正式配置').trigger('click');await flushPromises()
 expect(w.text()).toContain('配置已被其他操作修改');expect(button(w,'确认保存正式配置').exists()).toBe(true)
})
it('修改配置可启用历史数量 >= 条件',async()=>{
 vi.spyOn(customerApi,'list').mockResolvedValue([saved]);vi.spyOn(customerApi,'get').mockResolvedValue(saved);const save=vi.spyOn(customerApi,'save').mockResolvedValue(saved)
 const w=await mountPage();await button(w,'维护配置').trigger('click');await flushPromises()
 const selects=w.findAllComponents(ElSelect);selects.at(-1)!.vm.$emit('update:modelValue','gte');await flushPromises()
 await button(w,'确认保存正式配置').trigger('click');await flushPromises();expect(save.mock.calls[0][1].historical_quantity).toEqual({operator:'gte',value:'500'})
})
it('新增 E 客户后进入首次配置',async()=>{
 const created={...draft,customer_id:'E',customer_name:'E 客户'};const create=vi.spyOn(customerApi,'create').mockResolvedValue(created);vi.spyOn(customerApi,'get').mockResolvedValue(created)
 const w=await mountPage();await button(w,'新增客户').trigger('click');await flushPromises()
 const inputs=w.findAll('input').filter(i=>!i.attributes('type')||i.attributes('type')==='text');await inputs[0].setValue('E');await inputs[1].setValue('E 客户')
 await button(w,'新增并配置').trigger('click');await flushPromises();expect(create).toHaveBeenCalledWith({customer_id:'E',customer_name:'E 客户'});expect(w.text()).toContain('客户配置 · E 客户')
})

it('编辑恢复全部五列表头，未映射列可追加或取消，保存保留全部位置',async()=>{
 const full={...saved,header_source:'saved' as const,configuration:{...saved.configuration,source_columns:['单号','数量','料号','机型','备注'].map((name,i)=>({index:i+1,name})),customer:{...saved.configuration.customer,field_mapping:{'单号':'customer_order_no','数量':'quantity','料号':'customer_material_code'}}}}
 vi.spyOn(customerApi,'list').mockResolvedValue([full]);vi.spyOn(customerApi,'get').mockResolvedValue(full);const save=vi.spyOn(customerApi,'save').mockResolvedValue(full)
 const w=await mountPage();await button(w,'维护配置').trigger('click');await flushPromises()
 expect(w.text()).toContain('全部原始列 5 列');expect(w.text()).toContain('未映射');const selectors=w.findAllComponents(ElSelect).filter(s=>s.props('placeholder')==='不映射 / 人工选择');expect(selectors).toHaveLength(5)
 await button(w,'预览映射关系').trigger('click');expect(w.find('[data-testid="mapping-preview"]').text()).not.toContain('未映射（保留原始列）')
 selectors[3].vm.$emit('update:modelValue','customer_model');selectors[3].vm.$emit('change','customer_model')
 selectors[2].vm.$emit('update:modelValue','');selectors[2].vm.$emit('change','');await flushPromises()
 await button(w,'确认保存正式配置').trigger('click');await flushPromises()
 expect(save.mock.calls[0][1].source_columns).toEqual(full.configuration.source_columns)
 expect(save.mock.calls[0][1].field_mapping).toEqual({'单号':'customer_order_no','数量':'quantity','机型':'customer_model'})
})

it('旧配置缺完整表头会提示上传，不编造列，工作表位置改变需重新识别',async()=>{
 vi.spyOn(customerApi,'list').mockResolvedValue([saved]);vi.spyOn(customerApi,'get').mockResolvedValue({...saved,header_source:'upload_required'})
 const w=await mountPage();await button(w,'维护配置').trigger('click');await flushPromises()
 expect(w.text()).toContain('旧配置未保存完整表头');expect(w.findAllComponents(ElSelect).filter(s=>s.props('placeholder')==='不映射 / 人工选择')).toHaveLength(2)
 w.findComponent(ElInputNumber).vm.$emit('update:modelValue',2);await flushPromises();expect(button(w,'确认保存正式配置').attributes('disabled')).toBeDefined();expect(w.text()).toContain('工作表或表头行已改变')
})

it('上传样例复用已有映射，实际预览展示必填错误且不保存配置',async()=>{
 vi.spyOn(customerApi,'list').mockResolvedValue([saved]);vi.spyOn(customerApi,'get').mockResolvedValue(saved)
 vi.spyOn(customerApi,'suggest').mockResolvedValue({sheet_names:['Sheet1'],sheet_name:'Sheet1',header_row:1,templates:[],columns:[{index:1,column:'单号',mappable:true,samples:['P'],recommended_field:null,reason:'人工核对'},{index:2,column:'数量',mappable:true,samples:[12],recommended_field:'quantity',reason:'别名'},{index:3,column:'备注',mappable:true,samples:['n'],recommended_field:null,reason:'未映射'}]})
 const preview=vi.spyOn(customerApi,'preview').mockResolvedValue({file_error:null,total_rows:1,valid_rows:0,invalid_rows:1,rows:[{row_number:2,status:'INVALID_INPUT',data:null,errors:[{message:'客户采购单号不能为空'}]}]});const save=vi.spyOn(customerApi,'save')
 const w=await mountPage();await button(w,'维护配置').trigger('click');await flushPromises();const file=new File(['sample'],'preview.xlsx');await w.findComponent(ElUpload).props('onChange')!({raw:file} as never,[]);await flushPromises()
 await button(w,'预览采购数据').trigger('click');await flushPromises();expect(w.text()).toContain('客户采购单号不能为空');expect(preview).toHaveBeenCalledWith('D',file,expect.objectContaining({source_columns:[{index:1,name:'单号'},{index:2,name:'数量'},{index:3,name:'备注'}],field_mapping:{'单号':'customer_order_no','数量':'quantity'}}));expect(save).not.toHaveBeenCalled()
})

it.each(['A','B','C'])('%s 客户可以改为单字段并取消预设历史数量，不强制旧组合',async(cid)=>{
 const original={...saved,customer_id:cid,customer_name:cid+' 客户',configuration:{...saved.configuration,customer:{...saved.configuration.customer,field_mapping:{'单号':'customer_order_no','料号':'customer_material_code','机型':'customer_model','数量':'quantity'}},rule:{match_fields:['customer_order_no'],validation_config:{consistency_fields:['customer_material_code']},filter_conditions:{historical_quantity:{operator:'gt' as const,value:'500'}}}}}
 vi.spyOn(customerApi,'list').mockResolvedValue([original]);vi.spyOn(customerApi,'get').mockResolvedValue(original)
 const save=vi.spyOn(customerApi,'save').mockResolvedValue(original);const w=await mountPage()
 await button(w,'维护配置').trigger('click');await flushPromises()
 expect(w.findComponent(ElCheckboxGroup).props('modelValue')).toEqual(['customer_order_no','customer_material_code'])
 expect(w.text()).toContain('不代表业务上绝对准确');expect(w.text()).not.toContain('不能删减')
 w.findComponent(ElCheckboxGroup).vm.$emit('update:modelValue',['customer_model'])
 w.findAllComponents(ElSelect).at(-1)!.vm.$emit('update:modelValue','none');await flushPromises()
 expect(button(w,'确认保存正式配置').attributes('disabled')).toBeUndefined()
 await button(w,'确认保存正式配置').trigger('click');await flushPromises()
 expect(save).toHaveBeenCalledWith(cid,expect.objectContaining({match_fields:['customer_model'],historical_quantity:null}))
})

it('空选项、未映射字段、重复映射及超限数量阈值会阻止保存',async()=>{
 vi.spyOn(customerApi,'list').mockResolvedValue([saved]);vi.spyOn(customerApi,'get').mockResolvedValue(saved)
 const w=await mountPage();await button(w,'维护配置').trigger('click');await flushPromises()
 const group=w.findComponent(ElCheckboxGroup)
 for(const fields of [[],['customer_model'],['customer_order_no','customer_order_no'],['product_code']]){
  group.vm.$emit('update:modelValue',fields);await flushPromises();expect(button(w,'确认保存正式配置').attributes('disabled')).toBeDefined()
 }
 group.vm.$emit('update:modelValue',['customer_order_no']);await flushPromises()
 const mappings=w.findAllComponents(ElSelect).filter(s=>s.props('placeholder')==='不映射 / 人工选择')
 mappings[1].vm.$emit('update:modelValue','customer_order_no');mappings[1].vm.$emit('change','customer_order_no');await flushPromises()
 expect(button(w,'确认保存正式配置').attributes('disabled')).toBeDefined()
 mappings[1].vm.$emit('update:modelValue','quantity');mappings[1].vm.$emit('change','quantity')
 w.findAllComponents(ElSelect).at(-1)!.vm.$emit('update:modelValue','gte');await flushPromises()
 await w.find('input[inputmode="numeric"]').setValue('1000000000000000000')
 expect(button(w,'确认保存正式配置').attributes('disabled')).toBeDefined()
})

const sampleColumns=['单号','数量','料号','机型','备注'].map((column,i)=>({index:i+1,column,mappable:true,samples:['sample'],recommended_field:({'单号':'customer_order_no','数量':'quantity'} as Record<string,string>)[column]||null,reason:'核对'}))
const trialResult={checked_at:'2026-10-15T04:00:00Z',file_error:null,total_rows:2,evaluated_rows:2,row_limit:100,truncated:false,summary:{matched_rows:1,unmatched_rows:0,ambiguous_rows:1,other_rows:0},rows:[
 {row_number:2,sheet_name:'Sheet1',match_values:{customer_order_no:'PO'},result:{status:'MATCHED',message:'历史参考已校验',error_fields:[],matched_order:{id:1,order_no:'SO-1',product_code:'P1',product_name:'产品1',unit_price:'2.5000',source_type:'SEED'}},candidates:[]},
 {row_number:3,sheet_name:'Sheet1',match_values:{customer_order_no:'PO2'},result:{status:'AMBIGUOUS_MATCH',message:'多个不同产品',error_fields:[],matched_order:null},candidates:[{id:2,order_no:'SO-2',product_code:'P2',product_name:'产品2',unit_price:'3.0000',source_type:'SEED'}]},
]}
async function openWithSample(){
 vi.spyOn(customerApi,'list').mockResolvedValue([saved]);vi.spyOn(customerApi,'get').mockResolvedValue(saved)
 const suggest=vi.spyOn(customerApi,'suggest').mockResolvedValue({sheet_names:['Sheet1'],sheet_name:'Sheet1',header_row:1,templates:[],columns:sampleColumns})
 const w=await mountPage();await button(w,'维护配置').trigger('click');await flushPromises()
 const file=new File(['actual sample'],'reuse.xlsx');await w.findComponent(ElUpload).props('onChange')!({raw:file} as never,[]);await flushPromises()
 return {w,file,suggest}
}
function mappingSelectors(w:ReturnType<typeof mount>){return w.findAllComponents(ElSelect).filter(s=>s.props('placeholder')==='不映射 / 人工选择')}

it('映射预览仅展示已映射列并立即跟随草稿，全部表头仍保留，不发出预览请求',async()=>{
 const preview=vi.spyOn(customerApi,'preview');const trial=vi.spyOn(customerApi,'trial');const {w,suggest}=await openWithSample()
 await button(w,'预览映射关系').trigger('click');await flushPromises()
 const previewTable=()=>w.findAllComponents({name:'ElTable'}).find(t=>t.element.closest('[data-testid="mapping-preview"]'))!
 expect(previewTable().props('data').map((r:{column:string})=>r.column)).toEqual(['单号','数量'])
 const selectors=mappingSelectors(w);expect(selectors).toHaveLength(5)
 selectors[2].vm.$emit('update:modelValue','customer_material_code');selectors[2].vm.$emit('change');await flushPromises()
 expect(previewTable().props('data').map((r:{column:string})=>r.column)).toEqual(['单号','数量','料号'])
 selectors[0].vm.$emit('update:modelValue','');selectors[0].vm.$emit('change');await flushPromises()
 expect(previewTable().props('data').map((r:{column:string})=>r.column)).toEqual(['数量','料号'])
 expect(mappingSelectors(w)).toHaveLength(5);expect(suggest).toHaveBeenCalledTimes(1);expect(preview).not.toHaveBeenCalled();expect(trial).not.toHaveBeenCalled()
})

it('采购预览复用同一个文件，修改映射后直接重算而不重新识别或保存',async()=>{
 const preview=vi.spyOn(customerApi,'preview').mockResolvedValue({file_error:null,total_rows:1,valid_rows:1,invalid_rows:0,rows:[]});const save=vi.spyOn(customerApi,'save')
 const {w,file,suggest}=await openWithSample();await button(w,'预览采购数据').trigger('click');await flushPromises()
 expect(w.text()).toContain('样例共 1 行')
 const selectors=mappingSelectors(w);selectors[2].vm.$emit('update:modelValue','customer_material_code');selectors[2].vm.$emit('change');await flushPromises()
 expect(w.text()).not.toContain('样例共 1 行');await button(w,'预览采购数据').trigger('click');await flushPromises()
 expect(preview).toHaveBeenCalledTimes(2);expect(preview.mock.calls.every(call=>call[1]===file)).toBe(true)
 expect(preview.mock.calls[1][2].field_mapping['料号']).toBe('customer_material_code');expect(suggest).toHaveBeenCalledTimes(1);expect(save).not.toHaveBeenCalled()
})

it('试匹配提交未保存的字段和数量草稿，同一文件每次主动点击都发请求，变化后清除旧结果',async()=>{
 const trial=vi.spyOn(customerApi,'trial').mockResolvedValue(trialResult);const save=vi.spyOn(customerApi,'save');const {w,file,suggest}=await openWithSample()
 const selectors=mappingSelectors(w)
 selectors[2].vm.$emit('update:modelValue','customer_material_code');selectors[2].vm.$emit('change')
 selectors[3].vm.$emit('update:modelValue','customer_model');selectors[3].vm.$emit('change');await flushPromises()
 w.findComponent(ElCheckboxGroup).vm.$emit('update:modelValue',['customer_order_no','customer_model']);await flushPromises()
 await button(w,'历史订单试匹配').trigger('click');await flushPromises()
 expect(w.find('[data-testid="trial-preview"]').text()).toContain('唯一产品匹配成功 1 行')
 expect(w.text()).toContain('产品歧义');expect(w.text()).toContain('SO-1');expect(w.text()).toContain('P2 / 产品2');expect(w.text()).toContain('模拟历史')
 w.findComponent(ElCheckboxGroup).vm.$emit('update:modelValue',['customer_order_no','customer_model','customer_material_code']);await flushPromises()
 expect(w.find('[data-testid="trial-preview"]').exists()).toBe(false);expect(w.find('[data-testid="trial-stale"]').text()).toContain('原试匹配结果已过期')
 w.findAllComponents(ElSelect).at(-1)!.vm.$emit('update:modelValue','gte');await flushPromises()
 await button(w,'历史订单试匹配').trigger('click');await flushPromises();await button(w,'历史订单试匹配').trigger('click');await flushPromises()
 expect(trial).toHaveBeenCalledTimes(3);expect(trial.mock.calls.every(call=>call[1]===file)).toBe(true)
 expect(trial.mock.calls[0][2].match_fields).toEqual(['customer_order_no','customer_model'])
 expect(trial.mock.calls[1][2]).toMatchObject({expected_version:1,match_fields:['customer_order_no','customer_model','customer_material_code'],historical_quantity:{operator:'gte',value:'500'}})
 await w.find('input[inputmode="numeric"]').setValue('800');expect(w.find('[data-testid="trial-preview"]').exists()).toBe(false)
 expect(suggest).toHaveBeenCalledTimes(1);expect(save).not.toHaveBeenCalled()
})

it('历史配置无实际文件时明确提示上传一次，不虚构采购行',async()=>{
 vi.spyOn(customerApi,'list').mockResolvedValue([saved]);vi.spyOn(customerApi,'get').mockResolvedValue(saved)
 const preview=vi.spyOn(customerApi,'preview');const trial=vi.spyOn(customerApi,'trial');const w=await mountPage();await button(w,'维护配置').trigger('click');await flushPromises()
 expect(w.text()).toContain('已保存表头不能代替实际采购行');expect(button(w,'预览采购数据').attributes('disabled')).toBeUndefined();expect(button(w,'历史订单试匹配').attributes('disabled')).toBeUndefined()
 await button(w,'历史订单试匹配').trigger('click');await flushPromises();expect(w.find('[data-testid="preview-error"]').text()).toContain('请先上传一次 Excel 样例')
 expect(w.find('[data-testid="trial-preview"]').exists()).toBe(false);expect(preview).not.toHaveBeenCalled();expect(trial).not.toHaveBeenCalled()
})

it('重新查询失败不保留上次成功结果，限制范围和文件异常明确显示',async()=>{
 const trial=vi.spyOn(customerApi,'trial').mockResolvedValueOnce({...trialResult,total_rows:102,evaluated_rows:100,truncated:true}).mockRejectedValueOnce(new Error('历史查询暂时失败')).mockResolvedValueOnce({...trialResult,file_error:{message:'没有采购数据行'},total_rows:0,evaluated_rows:0,rows:[]})
 const {w}=await openWithSample();await button(w,'历史订单试匹配').trigger('click');await flushPromises();expect(w.text()).toContain('本次仅试匹配前 100 行')
 await button(w,'历史订单试匹配').trigger('click');await flushPromises();expect(w.text()).toContain('历史查询暂时失败');expect(w.find('[data-testid="trial-preview"]').exists()).toBe(false)
 await button(w,'历史订单试匹配').trigger('click');await flushPromises();expect(w.find('[data-testid="trial-preview"]').text()).toContain('没有采购数据行');expect(trial).toHaveBeenCalledTimes(3)
})


it('三个预览按钮初始样式中性，没有样例时明确提示而不发请求',async()=>{
 const trial=vi.spyOn(customerApi,'trial');const preview=vi.spyOn(customerApi,'preview');const w=await mountPage()
 await button(w,'首次配置').trigger('click');await flushPromises()
 for(const name of ['预览映射关系','预览采购数据','历史订单试匹配']){
  expect(button(w,name).classes()).not.toContain('el-button--primary');await button(w,name).trigger('click');await flushPromises()
  expect(w.find('[data-testid="preview-error"]').text()).toContain('请先上传')
 }
 expect(trial).not.toHaveBeenCalled();expect(preview).not.toHaveBeenCalled()
})

it.each(['trial','preview'] as const)('%s 查询 loading、阻止重复点击并定位到可见结果',async(method)=>{
 let resolveRequest!:(value:never)=>void
 const request=vi.spyOn(customerApi,method).mockImplementation(()=>new Promise(resolve=>{resolveRequest=resolve}) as never)
 const scroll=vi.spyOn(HTMLElement.prototype,'scrollIntoView');const {w}=await openWithSample()
 const clicked=method==='trial'?'历史订单试匹配':'预览采购数据'
 await button(w,clicked).trigger('click');await flushPromises()
 expect(button(w,clicked).classes()).toContain('is-loading');expect(w.find('[role="status"]').text()).toContain('请稍候')
 for(const name of ['预览映射关系','预览采购数据','历史订单试匹配'])expect(button(w,name).attributes('disabled')).toBeDefined()
 await button(w,clicked).trigger('click');expect(request).toHaveBeenCalledTimes(1)
 resolveRequest((method==='trial'?trialResult:{file_error:null,total_rows:1,valid_rows:1,invalid_rows:0,rows:[]}) as never);await flushPromises()
 expect(button(w,clicked).classes()).not.toContain('is-loading');expect(button(w,clicked).attributes('disabled')).toBeUndefined()
 expect(w.find(method==='trial'?'[data-testid="trial-preview"]':'[data-testid="purchase-preview"]').exists()).toBe(true)
 expect(scroll).toHaveBeenCalledWith({behavior:'smooth',block:'start'})
})

it('旧后端缺失接口时在按钮旁展示重启提示，请求失败后可重试',async()=>{
 vi.spyOn(customerApi,'trial').mockRejectedValue(new ApiError('请求资源不存在',404));const {w}=await openWithSample()
 await button(w,'历史订单试匹配').trigger('click');await flushPromises()
 expect(w.find('[data-testid="preview-error"]').text()).toContain('当前后端未提供此预览接口')
 expect(w.find('[data-testid="preview-error"]').text()).toContain('重启当前环境后端')
 expect(button(w,'历史订单试匹配').classes()).not.toContain('is-loading');expect(button(w,'历史订单试匹配').attributes('disabled')).toBeUndefined()
})

it('草稿不合法时说明所需条件，不静默返回，不请求历史',async()=>{
 const request=vi.spyOn(customerApi,'trial');const {w}=await openWithSample()
 w.findComponent(ElCheckboxGroup).vm.$emit('update:modelValue',[]);await flushPromises()
 await button(w,'历史订单试匹配').trigger('click');await flushPromises()
 expect(w.find('[data-testid="preview-error"]').text()).toContain('至少选择一个已映射的匹配字段');expect(request).not.toHaveBeenCalled()
})
