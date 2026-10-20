import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

export const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/orders' },
  { path: '/order-entry/:id?', name: 'order-entry', component: () => import('../views/OrderEntryView.vue'), meta: { title: '订单录入', section: 'ORDER ENTRY' } },
  { path: '/ai/:id?', redirect: to => ({ name: 'order-entry', params: to.params, query: { ...to.query, mode: 'ai' }, hash: to.hash }) },
  { path: '/customers', component: () => import('../views/CustomersView.vue'), meta: { title: '客户配置管理', section: 'CUSTOMER CONFIGURATION' } },
  { path: '/import', redirect: to => ({ name: 'order-entry', query: { ...to.query, mode: 'excel' }, hash: to.hash }) },
  { path: '/import/single', component: () => import('../views/ImportView.vue'), meta: { title: '恢复单文件导入', section: 'ORDER ENTRY', activeMenu: '/order-entry' } },
  { path: '/tasks', component: () => import('../views/TaskListView.vue'), meta: { title: '历史导入任务', section: 'IMPORT TASKS' } },
  { path: '/tasks/:id', component: () => import('../views/TaskView.vue'), meta: { title: '导入任务', section: 'IMPORT TASKS' } },
  // Preserve old bookmarks and order trace links, with one task-contained workflow.
  { path: '/rows/:id?', redirect: to => to.params.id ? '/tasks/'+to.params.id : '/tasks' },
  { path: '/orders', component: () => import('../views/OrdersView.vue'), meta: { title: '销售订单', section: 'SALES ORDERS' } },
  { path: '/orders/:id', component: () => import('../views/OrderView.vue'), meta: { title: '订单详情', section: 'ORDER DETAIL' } },
  { path: '/:pathMatch(.*)*', component: () => import('../views/NotFoundView.vue'), meta: { title: '页面不存在' } },
]
export default createRouter({ history: createWebHistory(import.meta.env.BASE_URL), routes, scrollBehavior: () => ({ top: 0 }) })
