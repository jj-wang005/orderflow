import {mount,flushPromises} from '@vue/test-utils'
import {it,expect,vi} from 'vitest'
import ElementPlus,{ElMessageBox,ElOption} from 'element-plus'
import PurchaseReview from './PurchaseReview.vue'
import {importApi} from '../api/imports'
import {ApiError} from '../api/client'
import type {RowDetail} from '../types/api'
it('新采购可携带真实稳定编号，但仍要求说明、版本且不提供安全开关',async()=>{
 const row={id:21,task_id:10,status:'REVIEW_REQUIRED',manual_revision:2} as RowDetail
 vi.spyOn(importApi,'reviewContext').mockResolvedValue({row,revision:2,candidates:[],history:[]});vi.spyOn(ElMessageBox,'confirm').mockResolvedValue('confirm' as Awaited<ReturnType<typeof ElMessageBox.confirm>>);const post=vi.spyOn(importApi,'review').mockResolvedValue({row:{...row,status:'NOT_MATCHED'},replayed:false})
 const w=mount(PurchaseReview,{props:{row},global:{plugins:[ElementPlus]}});await flushPromises();const submit=()=>w.findAll('button').find(b=>b.text()==='确认采购归属并继续处理')!;expect(submit().attributes('disabled')).toBeDefined();await w.find('input[placeholder*=采购单]').setValue('CONFIRMED-PO-LINE-001');await w.find('textarea').setValue('已与客户核实独立的新采购');await flushPromises();await submit().trigger('click');await flushPromises();expect(post).toHaveBeenCalledWith(10,21,expect.objectContaining({expected_revision:2,decision:'NEW',purchase_reference:'CONFIRMED-PO-LINE-001'}));expect(w.emitted('processed')?.[0][0]).toMatchObject({status:'NOT_MATCHED'});expect(Object.keys(post.mock.calls[0][2])).not.toContain('filter_conditions');w.unmount();vi.restoreAllMocks()
})

it('新采购没有稳定明细编号也可提交，技术重试保留同一个操作键',async()=>{
 const row={id:22,task_id:11,status:'REVIEW_REQUIRED',manual_revision:0} as RowDetail
 vi.spyOn(importApi,'reviewContext').mockResolvedValue({row,revision:0,candidates:[],history:[]});vi.spyOn(ElMessageBox,'confirm').mockResolvedValue('confirm' as Awaited<ReturnType<typeof ElMessageBox.confirm>>)
 const post=vi.spyOn(importApi,'review').mockRejectedValueOnce(new Error('网络超时')).mockResolvedValueOnce({row:{...row,status:'SUCCESS'},replayed:true})
 const w=mount(PurchaseReview,{global:{plugins:[ElementPlus]},props:{row}});await flushPromises()
 const submit=()=>w.findAll('button').find(b=>b.text()==='确认采购归属并继续处理')!
 expect(submit().attributes('disabled')).toBeDefined();await w.find('textarea').setValue('已通过客户确认消息核实此次再次采购');await flushPromises()
 expect(submit().attributes('disabled')).toBeUndefined();expect(w.text()).toContain('稳定采购明细编号（可选')
 await submit().trigger('click');await flushPromises();await submit().trigger('click');await flushPromises()
 expect(post).toHaveBeenCalledTimes(2);expect(post.mock.calls[0][2]).toEqual(post.mock.calls[1][2]);expect(post.mock.calls[0][2]).toMatchObject({decision:'NEW',purchase_reference:null,expected_revision:0})
 expect(w.emitted('processed')?.[0][0]).toMatchObject({status:'SUCCESS'});w.unmount();vi.restoreAllMocks()
})

it('已处理采购需要明确选择订单，候选显示来源任务和行号',async()=>{
 const row={id:23,task_id:12,status:'REVIEW_REQUIRED',manual_revision:1} as RowDetail
 vi.spyOn(importApi,'reviewContext').mockResolvedValue({row,revision:1,candidates:[{id:88,order_no:'AUTO-SIM',customer_order_no:null,customer_material_code:'MAT',customer_batch:'BATCH',quantity:'12',created_at:'2026-10-15',import_task_id:8,source_sheet_name:'采购',source_row_number:2}],history:[]})
 const post=vi.spyOn(importApi,'review');const w=mount(PurchaseReview,{global:{plugins:[ElementPlus]},props:{row}});await flushPromises()
 const {ElRadioGroup,ElSelect}=await import('element-plus');w.findComponent(ElRadioGroup).vm.$emit('update:modelValue','EXISTING');await w.find('textarea').setValue('已对照客户确认记录，核实与此订单一致');await flushPromises()
 const submit=()=>w.findAll('button').find(b=>b.text()==='确认采购归属并继续处理')!;expect(submit().attributes('disabled')).toBeDefined()
 expect(w.findAllComponents(ElOption).some(o=>String(o.props('label')).includes('来源任务 #8 / 采购 第 2 行'))).toBe(true);w.findComponent(ElSelect).vm.$emit('update:modelValue',88);await flushPromises();expect(submit().attributes('disabled')).toBeUndefined();expect(post).not.toHaveBeenCalled();w.unmount();vi.restoreAllMocks()
})

it('明确拒绝的编号冲突允许核对后修改，不把已回滚请求当作未知结果，也不随机换请求键',async()=>{
 const row={id:24,task_id:13,status:'REVIEW_REQUIRED',manual_revision:0} as RowDetail
 vi.spyOn(importApi,'reviewContext').mockResolvedValue({row,revision:0,candidates:[],history:[]});vi.spyOn(ElMessageBox,'confirm').mockResolvedValue('confirm' as Awaited<ReturnType<typeof ElMessageBox.confirm>>)
 const post=vi.spyOn(importApi,'review').mockRejectedValueOnce(new ApiError('该稳定采购明细编号已经建单',409,'PURCHASE_REFERENCE_EXISTS')).mockResolvedValueOnce({row:{...row,status:'SUCCESS'},replayed:false})
 const w=mount(PurchaseReview,{global:{plugins:[ElementPlus]},props:{row}});await flushPromises();const reference=w.find('input[placeholder*=采购单]');await reference.setValue('CONFIRMED-REQUEST-001');await w.find('textarea').setValue('已与客户核实本次独立采购需求');const submit=()=>w.findAll('button').find(b=>b.text()==='确认采购归属并继续处理')!
 await submit().trigger('click');await flushPromises();await reference.setValue('CONFIRMED-REQUEST-002');await submit().trigger('click');await flushPromises()
 expect(post).toHaveBeenCalledTimes(2);expect(post.mock.calls[0][2].request_key).toBe(post.mock.calls[1][2].request_key);expect(post.mock.calls[1][2].purchase_reference).toBe('CONFIRMED-REQUEST-002');w.unmount();vi.restoreAllMocks()
})
