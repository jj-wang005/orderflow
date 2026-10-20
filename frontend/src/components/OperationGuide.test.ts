import {mount,flushPromises} from '@vue/test-utils'
import {it,expect} from 'vitest'
import {createRouter,createMemoryHistory} from 'vue-router'
import ElementPlus from 'element-plus'
import OperationGuide from './OperationGuide.vue'
it('当前页指南随路由切换，关闭抽屉不丢失业务输入',async()=>{
 const router=createRouter({history:createMemoryHistory(),routes:[{path:'/:rest(.*)*',component:{template:'<div/>'}}]});await router.push('/orders/9');await router.isReady()
 const w=mount({components:{OperationGuide},template:'<input value="尚未保存采购文字"/><OperationGuide/>'},{attachTo:document.body,global:{plugins:[router,ElementPlus]}})
 await w.find('[aria-label="打开操作指南"]').trigger('click');await flushPromises();expect(document.body.textContent).toContain('订单编辑、状态、作废与导出')
 await router.push('/ai/8');await flushPromises();expect(document.body.textContent).toContain('AI解析、修正与确认');expect((w.find('input').element as HTMLInputElement).value).toBe('尚未保存采购文字')
 w.unmount()
})

it('目录可以切换章节并打开完整手册，包含真实安全边界',async()=>{
 const router=createRouter({history:createMemoryHistory(),routes:[{path:'/:rest(.*)*',component:{template:'<div/>'}}]});await router.push('/tasks/5');await router.isReady()
 const w=mount(OperationGuide,{attachTo:document.body,global:{plugins:[router,ElementPlus]}});await w.find('[aria-label="打开操作指南"]').trigger('click');await flushPromises()
 expect(document.body.querySelectorAll('.guide-chapter')).toHaveLength(1);expect(document.body.querySelector('.guide-chapter')?.textContent).toContain('任务查询与失败重传')
 const directory=document.body.querySelector('[aria-label="手册章节目录"]')!;Array.from(directory.querySelectorAll('button')).find(b=>b.textContent?.includes('采购重复核实'))!.click();await flushPromises();expect(document.body.querySelector('.guide-chapter')?.textContent).toContain('真实核实依据')
 Array.from(document.body.querySelectorAll('button')).find(b=>b.textContent==='查看完整手册')!.click();await flushPromises();expect(document.body.querySelectorAll('.guide-chapter')).toHaveLength(9);expect(document.body.textContent).toContain('原任务没有作废重开入口');expect(document.body.textContent).toContain('不含未经许可外传的客户机密');w.unmount()
})
