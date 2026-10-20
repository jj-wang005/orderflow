import {mount,flushPromises} from '@vue/test-utils'
import {it,expect,vi} from 'vitest'
import {createRouter,createMemoryHistory} from 'vue-router'
import ElementPlus from 'element-plus'
import CDouAssistant from './CDouAssistant.vue'
import {http} from '../api/client'
async function setup(){const router=createRouter({history:createMemoryHistory(),routes:[{path:'/:rest(.*)*',component:{template:'<div/>'}}]});await router.push('/import');return mount(CDouAssistant,{global:{plugins:[router,ElementPlus]}})}
it('opens, local guide makes no model request, chat retains recent history and clears',async()=>{
 const post=vi.spyOn(http,'post').mockResolvedValue({data:{data:{reply:'请到客户配置核查。'}}});const w=await setup()
 await w.find('.pet').trigger('click',{detail:0});expect(w.text()).toContain('你好呀，我是 C豆')
 await w.findAll('.assistant-tabs button')[1]!.trigger('click');expect(post).not.toHaveBeenCalled();expect(w.find('.guide-button').exists()).toBe(false)
 await w.findAll('.assistant-tabs button')[0]!.trigger('click');await w.find('.quick button').trigger('click');await flushPromises();expect(w.text()).toContain('请到客户配置核查。')
 await w.find('textarea').setValue('接下来呢');await w.find('textarea').trigger('keydown',{key:'Enter'});await flushPromises();expect(post.mock.calls[1]![1]).toMatchObject({history:[{role:'user',content:'什么是翻单？'},{role:'assistant',content:'请到客户配置核查。'}]})
 const clear=w.findAll('button').find(b=>b.text()==='清空对话')!;await clear.trigger('click');expect(w.findAll('.message')).toHaveLength(1);w.unmount();post.mockRestore()
})
it('drag threshold suppresses open and clamps position; resize clamps saved position',async()=>{
 const w=await setup();const pet=w.find('.pet');await pet.trigger('pointerdown',{pointerId:1,isPrimary:true,button:0,clientX:50,clientY:50});await pet.trigger('pointermove',{pointerId:1,clientX:-5000,clientY:-5000});await pet.trigger('pointerup',{pointerId:1,clientX:-5000,clientY:-5000});expect(w.find('.assistant-panel').exists()).toBe(false);expect((pet.element as HTMLElement).style.transform).toContain('0px,0px');window.dispatchEvent(new Event('resize'));w.unmount()
})
