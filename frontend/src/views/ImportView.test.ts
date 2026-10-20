import {customerApi} from '../api/customers'
import { mount, flushPromises } from '@vue/test-utils'
import { it, expect, vi, beforeEach, afterEach } from 'vitest'
beforeEach(()=>{vi.spyOn(customerApi,'list').mockResolvedValue([{customer_id:'D',customer_name:'D 客户',enabled:true,configured:true,config_version:1}])})
afterEach(()=>vi.restoreAllMocks())
import { createRouter, createMemoryHistory } from 'vue-router'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import ImportView from './ImportView.vue'
import { api } from '../api/orders'

it('选文件不重挂载文件输入节点，也不自动 POST 建单', async () => {
  localStorage.clear()
  const post = vi.spyOn(api,'upload').mockRejectedValue(new Error('unexpected POST'))
  const router=createRouter({history:createMemoryHistory(),routes:[{path:'/import',component:ImportView}]})
  await router.push('/import'); await router.isReady()
  const wrapper=mount(ImportView,{global:{plugins:[router,[ElementPlus,{locale:zhCn}]]}})
  const input=wrapper.find('input[type="file"]').element
  const file=new File(['abc'],'A.xlsx')
  Object.defineProperty(input,'files',{value:[file],configurable:true})
  await wrapper.find('input[type="file"]').trigger('change')
  await flushPromises()
  expect(wrapper.find('input[type="file"]').element).toBe(input)
  expect(wrapper.text()).toContain('A.xlsx'); expect(post).not.toHaveBeenCalled()
  wrapper.unmount(); post.mockRestore()
})
