import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import axios from 'axios'
import {http} from './client'
const previous=http.defaults.adapter
beforeEach(()=>vi.stubEnv('VITE_APP_ENV','test'))
afterEach(()=>{http.defaults.adapter=previous;vi.restoreAllMocks();vi.unstubAllEnvs()})
it('测试页面连接到演示后端时在提交前拒绝',async()=>{
 vi.spyOn(axios,'get').mockResolvedValue({data:{environment:'demo',database_name:'order_demo'}})
 const send=vi.fn();http.defaults.adapter=send
 await expect(http.post('/api/v1/customers',{customer_id:'D'})).rejects.toMatchObject({code:'ENVIRONMENT_MISMATCH'})
 expect(send).not.toHaveBeenCalled()
})
it('测试页面只有通过测试库身份检查才提交',async()=>{
 vi.spyOn(axios,'get').mockResolvedValue({data:{environment:'test',database_name:'order_test'}})
 const send=vi.fn(async config=>({data:{ok:true},status:200,statusText:'OK',headers:{},config}));http.defaults.adapter=send
 await http.post('/api/v1/customers',{customer_id:'D'});expect(send).toHaveBeenCalledTimes(1)
})
it('测试后端不可用时不改用其他后端',async()=>{
 vi.spyOn(axios,'get').mockRejectedValue(new Error('offline'));const send=vi.fn();http.defaults.adapter=send
 await expect(http.post('/api/v1/customers',{})).rejects.toThrow();expect(send).not.toHaveBeenCalled()
})
