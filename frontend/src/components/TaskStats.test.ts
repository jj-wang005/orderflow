import { mount } from '@vue/test-utils'
import { it, expect } from 'vitest'
import TaskStats from './TaskStats.vue'
import type { TaskSummary } from '../types/api'

it('统计展示 API 数值，不固定为样例 8/9/5', () => {
  const task = {total_rows:73,success_rows:51,unmatched_rows:16,failed_rows:6} as TaskSummary
  const wrapper=mount(TaskStats,{props:{task},global:{stubs:{ElIcon:true}}})
  expect(wrapper.findAll('strong').map(n=>n.text())).toEqual(['73行','51行','16行','6行'])
})
