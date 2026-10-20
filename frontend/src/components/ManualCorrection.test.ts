import { mount, flushPromises } from '@vue/test-utils'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import ElementPlus from 'element-plus'
import ManualCorrection from './ManualCorrection.vue'
import { api } from '../api/orders'
import { ApiError } from '../api/client'
import type { CorrectionContext, CorrectionResult, RowDetail } from '../types/api'

const row = {id:9,task_id:2,status:'INVALID_DATA',status_description:'采购数据无效',created_order_id:null} as RowDetail
const context = {row,revision:0,customer_id:'A',values:{customer_order_no:'001',customer_material_code:'MAT',quantity:'-1'},fields:[{field:'customer_order_no',label:'采购订单号',required:true},{field:'customer_material_code',label:'物料/品项编码',required:true},{field:'quantity',label:'订单数量',required:true}],editable:true,history:[],history_total:0} as CorrectionContext
function mountForm() { return mount(ManualCorrection,{props:{taskId:2,rowId:9},global:{plugins:[ElementPlus]}}) }
async function fill(wrapper: ReturnType<typeof mountForm>) {
  await wrapper.find('input[aria-label="订单数量"]').setValue('12')
  await wrapper.find('textarea').setValue('核对后修正')
}
beforeEach(()=>{ localStorage.clear(); vi.spyOn(api,'correction').mockResolvedValue(structuredClone(context)) })
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals()})
describe('人工补录',()=>{
  it('带入客户配置及原始值，查看不自动提交', async()=>{
    const post=vi.spyOn(api,'correct'); const w=mountForm();await flushPromises()
    expect((w.find('input[aria-label="采购订单号"]').element as HTMLInputElement).value).toBe('001')
    expect((w.find('input[aria-label="订单数量"]').element as HTMLInputElement).value).toBe('-1')
    expect(post).not.toHaveBeenCalled();w.unmount()
  })
  it('成功行无补录提交按钮',async()=>{
    vi.mocked(api.correction).mockResolvedValue({...context,editable:false,row:{...row,status:'SUCCESS',created_order_id:88}})
    const w=mountForm();await flushPromises();expect(w.text()).toContain('不允许修改或重复建单')
    expect(w.find('input').exists()).toBe(false);w.unmount()
  })
  it('必填及说明缺失在前端提示',async()=>{
    const post=vi.spyOn(api,'correct');const w=mountForm();await flushPromises()
    await w.find('input[aria-label="采购订单号"]').setValue('')
    await w.find('button.el-button--primary').trigger('click');await flushPromises()
    expect(w.text()).toContain('请填写：采购订单号');expect(post).not.toHaveBeenCalled();w.unmount()
  })
  it('超时及组件重开沿用完整原键原内容',async()=>{
    const post=vi.spyOn(api,'correct').mockRejectedValue(new ApiError('请求超时'))
    let w=mountForm();await flushPromises();await fill(w)
    await w.find('button.el-button--primary').trigger('click');await flushPromises()
    const first=structuredClone(post.mock.calls[0]![2]);expect(first.values.quantity).toBe('12')
    expect(w.text()).toContain('使用原补录操作重试');w.unmount()
    w=mountForm();await flushPromises();await w.find('button.el-button--primary').trigger('click');await flushPromises()
    expect(post.mock.calls[1]![2]).toEqual(first);w.unmount()
  })
  it('版本冲突刷新上下文，不沿用过期输入提交',async()=>{
    vi.spyOn(api,'correct').mockRejectedValue(new ApiError('明细已修改',409,'REVISION_CONFLICT'))
    const w=mountForm();await flushPromises();await fill(w);await w.find('button.el-button--primary').trigger('click');await flushPromises()
    expect(w.text()).toContain('明细已修改');expect(localStorage.getItem('cvte:correction:2:9')).toBeNull();w.unmount()
  })
  it('正常业务失败保留中文结果并触发统计刷新',async()=>{
    const failed={...row,status:'NOT_MATCHED',status_description:'未找到符合规则的历史销售订单',error_message:'无合格历史'} as RowDetail
    vi.spyOn(api,'correct').mockResolvedValue({row:failed,revision:1,replayed:false,attempt:{}} as CorrectionResult)
    const w=mountForm();await flushPromises();await fill(w);await w.find('button.el-button--primary').trigger('click');await flushPromises()
    expect(w.text()).toContain('无合格历史');expect(w.emitted('processed')?.[0]).toEqual([failed]);expect(localStorage.getItem('cvte:correction:2:9')).toBeNull();w.unmount()
  })
  it('保存重试标识失败阻止 POST',async()=>{
    const post=vi.spyOn(api,'correct');const w=mountForm();await flushPromises();await fill(w)
    vi.stubGlobal('localStorage',{getItem:()=>null,setItem:()=>{throw new Error('quota')}})
    await w.find('button.el-button--primary').trigger('click');await flushPromises()
    expect(post).not.toHaveBeenCalled();expect(w.text()).toContain('未向服务器提交');w.unmount()
  })
})
