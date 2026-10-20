import {mount,flushPromises} from '@vue/test-utils'
import {it,expect,vi,afterEach} from 'vitest'
import ElementPlus,{ElMessageBox} from 'element-plus'
import VoidOrderDialog from './VoidOrderDialog.vue'
import {api} from '../api/orders'
import type {OrderDetail} from '../types/api'
afterEach(()=>{vi.restoreAllMocks();localStorage.clear()})
it('空原因不提交，填写原因后二次确认并带版本提交',async()=>{
 const order={id:1,order_no:'TEST',status:'CREATED',edit_revision:3} as OrderDetail
 const send=vi.spyOn(api,'voidOrder').mockResolvedValue({order:{...order,status:'VOIDED'},replayed:false});const ask=vi.spyOn(ElMessageBox,'confirm').mockResolvedValue('confirm' as never)
 const w=mount(VoidOrderDialog,{props:{order},global:{plugins:[ElementPlus],stubs:{Teleport:true}}});await flushPromises()
 await w.find('button.el-button--danger').trigger('click');await flushPromises();expect(send).not.toHaveBeenCalled()
 await w.find('textarea').setValue('采购取消');await flushPromises()
 await w.find('button.el-button--danger').trigger('click');await flushPromises()
 expect(ask).toHaveBeenCalledTimes(1);expect(send).toHaveBeenCalledWith(1,expect.objectContaining({expected_revision:3,note:'采购取消',confirmed:true}));expect(w.emitted('saved')).toHaveLength(1);w.unmount()
})
