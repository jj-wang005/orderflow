import {customerApi} from '../api/customers'
import { mount, flushPromises } from '@vue/test-utils'
import { it, expect, vi, beforeEach, afterEach } from 'vitest'
beforeEach(()=>{vi.spyOn(customerApi,'list').mockResolvedValue([{customer_id:'D',customer_name:'D 客户',enabled:true,configured:true,config_version:1}])})
afterEach(()=>vi.restoreAllMocks())
import { createRouter, createMemoryHistory } from 'vue-router'
import ElementPlus,{ElSelect} from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import OrdersView from './OrdersView.vue'
import { api } from '../api/orders'
import OrderStatusEditor from '../components/OrderStatusEditor.vue'
import type {OrderDetail} from '../types/api'
import * as downloads from '../utils/download'

it('翻页加载期间保留 total，不能把页码错误重置回第 1 页', async () => {
  const request = vi.spyOn(api,'orders').mockImplementation(async params => ({total:54,page:params.page,page_size:20,items:[]}))
  const router = createRouter({history:createMemoryHistory(),routes:[{path:'/orders',component:OrdersView}]})
  await router.push('/orders'); await router.isReady()
  const wrapper = mount(OrdersView,{global:{plugins:[router,[ElementPlus,{locale:zhCn}]]}})
  await flushPromises()
  await wrapper.find('button[aria-label="下一页"]').trigger('click')
  await flushPromises()
  expect(request.mock.calls.at(-1)?.[0].page).toBe(2)
  expect(wrapper.find('.el-pager .is-active').text()).toBe('2')
  wrapper.unmount(); request.mockRestore()
})

it('行内状态保存成功后重新加载列表，刷新后保留服务器状态',async()=>{
 localStorage.clear()
 let order={id:88,source_type:'AUTO',status:'CREATED',status_description:'已创建',edit_revision:0,order_no:'TEST',customer_id:'A',quantity:'12',unit_price:'2.5000',amount:'30.00',created_at:'2026-10-13T12:00:00'} as OrderDetail
 const request=vi.spyOn(api,'orders').mockImplementation(async p=>({total:1,page:p.page,page_size:20,items:[structuredClone(order)]}))
 const save=vi.spyOn(api,'statusOrder').mockImplementation(async()=>{order={...order,status:'VALID',status_description:'已确认',edit_revision:1};return {order,replayed:false}})
 const router=createRouter({history:createMemoryHistory(),routes:[{path:'/orders',component:OrdersView}]})
 await router.push('/orders');await router.isReady();const w=mount(OrdersView,{global:{plugins:[router,ElementPlus]}});await flushPromises()
 const editor=w.findComponent(OrderStatusEditor);editor.findComponent(ElSelect).vm.$emit('update:modelValue','VALID');await flushPromises()
 await editor.find('button').trigger('click');await flushPromises();expect(request).toHaveBeenCalledTimes(2)
 expect(w.findComponent(OrderStatusEditor).props('order').status).toBe('VALID')
 await w.findAll('button').find(x=>x.text().includes('刷新列表'))!.trigger('click');await flushPromises()
 expect(w.findComponent(OrderStatusEditor).props('order').status).toBe('VALID');w.unmount();request.mockRestore();save.mockRestore()
})

it('中文状态筛选进入查询参数并保留于路由，重置后清除',async()=>{
 const request=vi.spyOn(api,'orders').mockImplementation(async p=>({total:0,page:p.page,page_size:20,items:[]}))
 const router=createRouter({history:createMemoryHistory(),routes:[{path:'/orders',component:OrdersView}]})
 await router.push('/orders?status=VALID');await router.isReady()
 const w=mount(OrdersView,{global:{plugins:[router,ElementPlus]}});await flushPromises()
 expect(request.mock.calls.at(-1)?.[0].status).toBe('VALID')
 const selector=w.findAllComponents(ElSelect).find(x=>x.props('placeholder')==='全部状态')!
 expect(selector.exists()).toBe(true);selector.vm.$emit('update:modelValue','SHIPPED');await flushPromises()
 await w.find('form').trigger('submit');await flushPromises()
 expect(request.mock.calls.at(-1)?.[0].status).toBe('SHIPPED');expect(router.currentRoute.value.query.status).toBe('SHIPPED')
 await w.findAll('button').find(x=>x.text().includes('重置'))!.trigger('click');await flushPromises()
 expect(request.mock.calls.at(-1)?.[0].status).toBeUndefined();w.unmount();request.mockRestore()
})

it('导出使用已查询的完整筛选且不携带分页，不跟随尚未提交的字段',async()=>{
 const query=vi.spyOn(api,'orders').mockImplementation(async p=>({total:123,page:p.page,page_size:p.page_size,items:[]}))
 const download=vi.spyOn(downloads,'download').mockResolvedValue()
 const router=createRouter({history:createMemoryHistory(),routes:[{path:'/orders',component:OrdersView}]});await router.push('/orders?status=VOIDED&from=2026-01-01&to=2026-01-31');await router.isReady()
 const w=mount(OrdersView,{global:{plugins:[router,ElementPlus]}});await flushPromises()
 expect(query.mock.calls.at(-1)?.[0].status).toBe('VOIDED')
 const selector=w.findAllComponents(ElSelect).find(x=>x.props('placeholder')==='全部状态')!;selector.vm.$emit('update:modelValue','CREATED');await flushPromises()
 await w.findAll('button').find(b=>b.text()==='导出 Excel')!.trigger('click');await flushPromises()
 expect(download).toHaveBeenCalledWith('/api/v1/orders/export.xlsx','销售订单.xlsx',expect.objectContaining({status:'VOIDED',date_from:'2026-01-01',date_to:'2026-01-31'}))
 expect(download.mock.calls[0]?.[2]).not.toHaveProperty('page');w.unmount()
})
