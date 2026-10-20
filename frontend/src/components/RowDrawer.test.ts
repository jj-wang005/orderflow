import {mount,flushPromises} from '@vue/test-utils'
import {it,expect} from 'vitest'
import {createRouter,createMemoryHistory} from 'vue-router'
import ElementPlus from 'element-plus'
import RowDrawer from './RowDrawer.vue'
import type {RowDetail} from '../types/api'
const row:RowDetail={id:1,task_id:3,row_number:2,sheet_name:'采购',status:'REVIEW_REQUIRED',status_description:'待确认是否重复采购',raw_data:{料号:'MAT'},normalized_data:null,matched_order_id:null,created_order_id:null,error_code:'REVIEW_REQUIRED',manual_revision:0,error_message:'发现同客户疑似重复采购'}
it('待核实行优先展示核实，切换修正后不同时显示多个处理表单',async()=>{
 const router=createRouter({history:createMemoryHistory(),routes:[{path:'/:rest(.*)*',component:{template:'<div/>'}}]});await router.push('/tasks/3');await router.isReady()
 const w=mount(RowDrawer,{props:{row,open:true},attachTo:document.body,global:{plugins:[router,ElementPlus],stubs:{PurchaseReview:{template:'<form data-kind="review"/>'},ManualCorrection:{props:{historyOnly:Boolean},template:'<form v-if="!historyOnly" data-kind="correct"/><p v-else>修正记录</p>'}}}});await flushPromises()
 expect(document.body.querySelectorAll('form[data-kind]')).toHaveLength(1);expect(document.body.querySelector('form')?.getAttribute('data-kind')).toBe('review')
 const correct=Array.from(document.body.querySelectorAll('button')).find(b=>b.textContent==='修正本次采购信息')!;correct.click();await flushPromises()
 expect(document.body.querySelectorAll('form[data-kind]')).toHaveLength(1);expect(document.body.querySelector('form')?.getAttribute('data-kind')).toBe('correct')
 await w.setProps({row:{...row,status:'SUCCESS',created_order_id:88,purchase_review:{history:[{decision:'NEW',note:'客户确认第二次采购的记录',purchase_reference:'CONFIRMED-NEW-002',confirmed_at:'2026-10-14',actor_label:'本地核实'}]}}});await flushPromises();expect(document.body.querySelectorAll('form[data-kind]')).toHaveLength(0);expect(document.body.textContent).toContain('查看销售订单 #88');expect(document.body.textContent).toContain('查看重复采购核实记录（1次）');w.unmount()
})
