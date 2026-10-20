import {mount,flushPromises} from '@vue/test-utils'
import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import ElementPlus from 'element-plus'
import {api} from '../api/orders'
import {ApiError} from '../api/client'
import OrderOperationDialog from './OrderOperationDialog.vue'
import type {CorrectionContext,OrderDetail,OrderOperationResult} from '../types/api'
const original={customer_id:'A',customer_order_no:'00001',customer_material_code:'MAT',customer_model:null,customer_batch:null,quantity:'12'}
const order={...original,id:88,source_type:'AUTO',status:'CREATED',source_order_id:null,edit_revision:2,product_code:'SIM-MANUAL',product_name:'模拟核对产品',unit_price:'2.5000',amount:'30.00'} as OrderDetail
const context={customer_id:'A',revision:0,row:{id:9,task_id:2,status:'NOT_MATCHED',created_order_id:null,normalized_data:original}} as CorrectionContext
const wrappers:ReturnType<typeof mount>[]=[]
function form(mode:'manual'|'edit'='manual') {const w=mount(OrderOperationDialog,{props:{mode,taskId:2,rowId:9,orderId:88},global:{plugins:[ElementPlus],stubs:{Teleport:true}}});wrappers.push(w);return w}
async function fill(w:ReturnType<typeof form>, withBasis=true) {
 for(const [label,v] of [['我司产品编码','SIM-MANUAL'],['我司产品名称','模拟核对产品'],['适用销售单价','2.5']])await w.find(`input[aria-label="${label}"]`).setValue(v)
 if(withBasis)await w.find('textarea[aria-label="产品与价格核对依据"]').setValue('报价单 TEST-01')
 await w.find('textarea[aria-label="操作说明"]').setValue('核对后保存')
}
beforeEach(()=>{localStorage.clear();vi.spyOn(api,'correction').mockResolvedValue(structuredClone(context));vi.spyOn(api,'editContext').mockResolvedValue({order:structuredClone(order),editable:true,history:[]})})
afterEach(()=>{wrappers.splice(0).forEach(w=>w.unmount());vi.restoreAllMocks();vi.unstubAllGlobals()})
it('手动表单带入采购字段且不会生成产品或价格',async()=>{
 const post=vi.spyOn(api,'manualOrder');const w=form();await flushPromises()
 expect((w.find('input[aria-label="客户采购单号"]').element as HTMLInputElement).value).toBe('00001')
 expect(w.find('input[aria-label="采购数量"]').attributes('disabled')).toBeDefined()
 expect((w.find('input[aria-label="我司产品编码"]').element as HTMLInputElement).value).toBe('')
 expect(post).not.toHaveBeenCalled()
})
it('产品价格和操作说明仍须完整',async()=>{
 const post=vi.spyOn(api,'manualOrder');const w=form();await flushPromises();await w.find('button.el-button--primary').trigger('click');await flushPromises()
 expect(w.text()).toContain('请完整填写产品编码');expect(post).not.toHaveBeenCalled()
})
it('手动建单不提交金额和采购字段，成功返回服务器订单',async()=>{
 const result={order,row:{...context.row,status:'SUCCESS',created_order_id:88},replayed:false} as OrderOperationResult
 const post=vi.spyOn(api,'manualOrder').mockResolvedValue(result);const w=form();await flushPromises();await fill(w);await w.find('button.el-button--primary').trigger('click');await flushPromises()
 expect(post.mock.calls[0]![2]).not.toHaveProperty('amount');expect(post.mock.calls[0]![2]).not.toHaveProperty('values')
 expect(w.emitted('saved')?.[0]).toEqual([result])
})
it('编辑带入当前值并发送订单版本，不发送系统来源',async()=>{
 const post=vi.spyOn(api,'editOrder').mockResolvedValue({order,replayed:false});const w=form('edit');await flushPromises();await fill(w);await w.find('input[aria-label="采购数量"]').setValue('15');await w.find('button.el-button--primary').trigger('click');await flushPromises()
 expect(post.mock.calls[0]![1].expected_revision).toBe(2);expect(post.mock.calls[0]![1].values?.quantity).toBe('15')
 expect(post.mock.calls[0]![1]).not.toHaveProperty('source_order_id')
})
it('历史翻单产品只读，价格可编辑，历史价与当前价分开展示',async()=>{
 vi.mocked(api.editContext).mockResolvedValue({order:{...order,source_order_id:207},historical_unit_price:'12.0000',editable:true,history:[]})
 const w=form('edit');await flushPromises();expect(w.find('input[aria-label="适用销售单价"]').attributes('disabled')).toBeUndefined();expect(w.find('input[aria-label="我司产品编码"]').attributes('disabled')).toBeDefined()
 expect(w.text()).toContain('历史参考单价：12.0000');expect(w.text()).toContain('当前订单单价：2.5000')
})
it('已确认订单不能保存',async()=>{
 const post=vi.spyOn(api,'editOrder');vi.mocked(api.editContext).mockResolvedValue({order:{...order,status:'VALID'},editable:false,history:[]})
 const w=form('edit');await flushPromises();expect(w.find('button.el-button--primary').attributes('disabled')).toBeDefined();expect(post).not.toHaveBeenCalled()
})
it('网络失败重开表单沿用原 UUID 和全部内容',async()=>{
 const post=vi.spyOn(api,'manualOrder').mockRejectedValue(new ApiError('网络失败'));let w=form();await flushPromises();await fill(w);await w.find('button.el-button--primary').trigger('click');await flushPromises()
 const first=structuredClone(post.mock.calls[0]![2]);w.unmount();wrappers.splice(wrappers.indexOf(w),1)
 w=form();await flushPromises();await w.find('button.el-button--primary').trigger('click');await flushPromises();expect(post.mock.calls[1]![2]).toEqual(first)
})
it('422 保留用户输入以便修正，409 刷新服务器版本',async()=>{
 const post=vi.spyOn(api,'manualOrder').mockRejectedValue(new ApiError('价格不合法',422));const w=form();await flushPromises();await fill(w);await w.find('button.el-button--primary').trigger('click');await flushPromises()
 expect((w.find('input[aria-label="我司产品编码"]').element as HTMLInputElement).value).toBe('SIM-MANUAL');expect(w.text()).toContain('价格不合法')
 post.mockRejectedValue(new ApiError('版本冲突',409));await w.find('button.el-button--primary').trigger('click');await flushPromises();expect(api.correction).toHaveBeenCalledTimes(2)
})
it('本地存储失败不发送请求',async()=>{
 const post=vi.spyOn(api,'manualOrder');const w=form();await flushPromises();await fill(w)
 vi.stubGlobal('localStorage',{getItem:()=>null,setItem:()=>{throw new Error('quota')}})
 await w.find('button.el-button--primary').trigger('click');await flushPromises();expect(post).not.toHaveBeenCalled();expect(w.text()).toContain('未提交服务器')
})
it('核对依据无必填标记，没有模拟确认复选框，空依据可提交',async()=>{
 const post=vi.spyOn(api,'manualOrder').mockResolvedValue({order,replayed:false});const w=form();await flushPromises();await fill(w,false)
 const item=w.findAll('.el-form-item').find(x=>x.text().includes('产品与价格核对依据（选填）'))!
 expect(item.classes()).not.toContain('is-required');expect(w.find('input[type="checkbox"]').exists()).toBe(false)
 expect(w.text()).not.toContain('未通过真实 OMS');expect(w.text()).not.toContain('确认模拟')
 await w.find('button.el-button--primary').trigger('click');await flushPromises()
 expect(post.mock.calls[0]![2].verification_basis).toBeNull();expect(post.mock.calls[0]![2]).not.toHaveProperty('simulation_confirmed')
})
it('共用编辑表单的依据也选填',async()=>{
 const post=vi.spyOn(api,'editOrder').mockResolvedValue({order,replayed:false});const w=form('edit');await flushPromises();await fill(w,false)
 await w.find('button.el-button--primary').trigger('click');await flushPromises();expect(post.mock.calls[0]![1].verification_basis).toBeNull()
})
it('旧版本恢复记录保留原确认参数和原请求键',async()=>{
 const old={request_key:'5210a93e-3ff9-475c-a670-5748bdbf9d0f',expected_revision:0,product:{product_code:'SIM-OLD',product_name:'模拟旧产品',unit_price:'2.5'},note:'旧操作',verification_basis:'旧报价单',simulation_confirmed:true}
 localStorage.setItem('cvte:order-operation:manual:88',JSON.stringify(old))
 const post=vi.spyOn(api,'manualOrder').mockRejectedValue(new ApiError('网络失败'));const w=form();await flushPromises()
 expect(w.find('input[type="checkbox"]').exists()).toBe(false);await w.find('button.el-button--primary').trigger('click');await flushPromises()
 expect(post.mock.calls[0]![2]).toEqual(old)
})

it('改价才要求依据，4398 × 13.5 精确预览为59373.00',async()=>{
 vi.mocked(api.editContext).mockResolvedValue({order:{...order,unit_price:'12.0000',quantity:'4398',amount:'52776.00',source_order_id:207},historical_unit_price:'12.0000',editable:true,history:[]})
 const post=vi.spyOn(api,'editOrder').mockResolvedValue({order,replayed:false});const w=form('edit');await flushPromises()
 await w.find('input[aria-label="适用销售单价"]').setValue('13.5');await w.find('textarea[aria-label="操作说明"]').setValue('最新报价')
 expect(w.text()).toContain('52776.00 → 59373.00')
 await w.find('button.el-button--primary').trigger('click');await flushPromises();expect(post).not.toHaveBeenCalled();expect(w.text()).toContain('修改单价必须填写')
 await w.find('textarea[aria-label="产品与价格核对依据"]').setValue('报价 QUOTE-01');await w.find('button.el-button--primary').trigger('click');await flushPromises()
 expect(post.mock.calls[0]![1].product.unit_price).toBe('13.5');expect(post.mock.calls[0]![1]).not.toHaveProperty('amount')
})

it('改回当前价后依据不必填，历史价不替代当前价',async()=>{
 vi.mocked(api.editContext).mockResolvedValue({order:{...order,unit_price:'13.5000',source_order_id:207},historical_unit_price:'12.0000',editable:true,history:[]})
 const post=vi.spyOn(api,'editOrder').mockResolvedValue({order,replayed:false});const w=form('edit');await flushPromises()
 await w.find('input[aria-label="适用销售单价"]').setValue('12');expect(w.text()).toContain('改价必填')
 await w.find('input[aria-label="适用销售单价"]').setValue('13.5');expect(w.text()).toContain('核对依据（选填）')
 await w.find('textarea[aria-label="操作说明"]').setValue('只修改数量');await w.find('input[aria-label="采购数量"]').setValue('15')
 await w.find('button.el-button--primary').trigger('click');await flushPromises();expect(post.mock.calls[0]![1].verification_basis).toBeNull()
})

it('人工单无历史参考，产品仍只读且降价能保存',async()=>{
 const post=vi.spyOn(api,'editOrder').mockResolvedValue({order,replayed:false});const w=form('edit');await flushPromises()
 expect(w.text()).toContain('无历史参考');expect(w.find('input[aria-label="我司产品名称"]').attributes('disabled')).toBeDefined()
 await w.find('input[aria-label="适用销售单价"]').setValue('1.005');await w.find('textarea[aria-label="产品与价格核对依据"]').setValue('协议降价')
 await w.find('textarea[aria-label="操作说明"]').setValue('降价');await w.find('input[aria-label="采购数量"]').setValue('1')
 expect(w.text()).toContain('30.00 → 1.01')
 await w.find('button.el-button--primary').trigger('click');await flushPromises();expect(post.mock.calls[0]![1].product.unit_price).toBe('1.005')
})

it.each(['0','-1','1.00001','100000000000000'])('前端拒绝非法价格 %s',async(price)=>{
 const post=vi.spyOn(api,'editOrder');const w=form('edit');await flushPromises();await fill(w)
 await w.find('input[aria-label="适用销售单价"]').setValue(price);await w.find('button.el-button--primary').trigger('click');await flushPromises()
 expect(post).not.toHaveBeenCalled();expect(w.text()).toContain('销售单价须为合法正数')
})

it('改价422和409保留所有输入，冲突时阻止覆盖提交',async()=>{
 const post=vi.spyOn(api,'editOrder').mockRejectedValue(new ApiError('依据不合法',422));const w=form('edit');await flushPromises();await fill(w)
 await w.find('input[aria-label="适用销售单价"]').setValue('13.5');await w.find('button.el-button--primary').trigger('click');await flushPromises()
 expect((w.find('input[aria-label="适用销售单价"]').element as HTMLInputElement).value).toBe('13.5');expect(w.text()).toContain('依据不合法')
 post.mockRejectedValue(new ApiError('版本冲突',409,'REVISION_CONFLICT'));await w.find('button.el-button--primary').trigger('click');await flushPromises()
 expect(api.editContext).toHaveBeenCalledTimes(1);expect(w.text()).toContain('输入已保留')
 expect((w.find('textarea[aria-label="产品与价格核对依据"]').element as HTMLTextAreaElement).value).toBe('报价单 TEST-01')
 expect(w.find('button.el-button--primary').attributes('disabled')).toBeDefined()
})

it('请求进行中重复点击只发送一次',async()=>{
 let resolve!:(v:OrderOperationResult)=>void
 const post=vi.spyOn(api,'editOrder').mockImplementation(()=>new Promise(r=>{resolve=r}));const w=form('edit');await flushPromises();await fill(w)
 await w.find('button.el-button--primary').trigger('click');await w.find('button.el-button--primary').trigger('click');expect(post).toHaveBeenCalledTimes(1)
 resolve({order,replayed:false});await flushPromises();expect(w.emitted('saved')).toHaveLength(1)
})
