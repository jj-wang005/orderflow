import {mount,flushPromises} from '@vue/test-utils'
import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import ElementPlus,{ElSelect,ElOption} from 'element-plus'
import OrderStatusEditor from './OrderStatusEditor.vue'
import {api} from '../api/orders'
import {ApiError} from '../api/client'
import type {OrderDetail} from '../types/api'
const order={id:88,source_type:'AUTO',status:'CREATED',status_description:'已创建',edit_revision:2} as OrderDetail
const wrappers:ReturnType<typeof mount>[]=[]
function form(o=order){const w=mount(OrderStatusEditor,{props:{order:o},global:{plugins:[ElementPlus]}});wrappers.push(w);return w}
async function selectNext(w:ReturnType<typeof form>){w.findComponent(ElSelect).vm.$emit('update:modelValue','VALID');await flushPromises()}
beforeEach(()=>localStorage.clear())
afterEach(()=>{wrappers.splice(0).forEach(w=>w.unmount());vi.restoreAllMocks();vi.unstubAllGlobals()})
it('五种中文正常状态，禁止跳步且不展示 INVALID',async()=>{
 const w=form();await flushPromises();const options=w.findAllComponents(ElOption)
 expect(options.map(o=>o.props('label'))).toEqual(['已创建','已确认','进行中','已发货','已完成'])
 expect(options.map(o=>o.props('disabled'))).toEqual([false,false,true,true,true])
 expect(w.text()).not.toContain('模拟');expect(w.find('input[type="checkbox"]').exists()).toBe(false)
 expect(w.find('button').attributes('disabled')).toBeDefined()
})
it('保存发送当前版本和目标，刷新事件返回服务器结果',async()=>{
 const result={...order,status:'VALID',edit_revision:3};const post=vi.spyOn(api,'statusOrder').mockResolvedValue({order:result,replayed:false})
 const w=form();await flushPromises();await selectNext(w);await w.find('button').trigger('click');await flushPromises()
 expect(post.mock.calls[0]![0]).toBe(88);expect(post.mock.calls[0]![1]).toMatchObject({expected_revision:2,status:'VALID'})
 expect(w.emitted('saved')?.[0]).toEqual([result]);expect(localStorage.getItem('cvte:order-status:88')).toBeNull()
 await w.setProps({order:result});expect(w.findComponent(ElSelect).props('modelValue')).toBe('VALID')
})
it('网络失败重开后使用同一请求重试，避免确认后的重复操作',async()=>{
 const post=vi.spyOn(api,'statusOrder').mockRejectedValue(new ApiError('网络失败'));let w=form();await flushPromises();await selectNext(w);await w.find('button').trigger('click');await flushPromises()
 const first=structuredClone(post.mock.calls[0]![1]);w.unmount();wrappers.splice(wrappers.indexOf(w),1)
 w=form();await flushPromises();expect(w.find('button').text()).toContain('重试');await w.find('button').trigger('click');await flushPromises()
 expect(post.mock.calls[1]![1]).toEqual(first)
})
it.each([409,422])('业务拒绝 %s 显示中文原因并请求刷新',async code=>{
 vi.spyOn(api,'statusOrder').mockRejectedValue(new ApiError('订单版本已变化',code));const w=form();await flushPromises();await selectNext(w);await w.find('button').trigger('click');await flushPromises()
 expect(w.text()).toContain('订单版本已变化');expect(w.emitted('refresh')).toHaveLength(1);expect(localStorage.getItem('cvte:order-status:88')).toBeNull()
})
it('SEED 只读，已完成不能继续流转',async()=>{
 const seed=form({...order,source_type:'SEED'});await flushPromises();expect(seed.find('button').exists()).toBe(false)
 const done=form({...order,status:'COMPLETED'});await flushPromises();expect(done.find('button').attributes('disabled')).toBeDefined()
 expect(done.findAllComponents(ElOption).filter(x=>!x.props('disabled')).map(x=>x.props('value'))).toEqual(['COMPLETED'])
})
it('存储重试标识失败时不提交',async()=>{
 const post=vi.spyOn(api,'statusOrder');const w=form();await flushPromises();await selectNext(w)
 vi.stubGlobal('localStorage',{getItem:()=>null,setItem:()=>{throw new Error('quota')}})
 await w.find('button').trigger('click');await flushPromises();expect(post).not.toHaveBeenCalled();expect(w.text()).toContain('尚未提交')
})
