import {afterEach,expect,it,vi} from 'vitest'
import {http} from './client'
import {customerApi,type ConfigBody} from './customers'

afterEach(()=>vi.restoreAllMocks())
it.each(['preview','trial'] as const)('%s 发送原始样例文件和完整草稿，不调用配置保存',async(method)=>{
 const result={total_rows:1};const post=vi.spyOn(http,'post').mockResolvedValue({data:{data:result}})
 const file=new File(['sample'],'sample.xlsx');const body:ConfigBody={expected_version:0,customer_name:'D',sheet_name:'Sheet1',header_row:1,field_mapping:{单号:'customer_order_no',数量:'quantity'},match_fields:['customer_order_no'],historical_quantity:{operator:'gt',value:'500'},enabled:true}
 expect(await customerApi[method]('D',file,body)).toEqual(result)
 expect(post.mock.calls[0][0]).toBe('/api/v1/customers/D/config/'+(method==='trial'?'trial-match':'preview'))
 const form=post.mock.calls[0][1] as FormData;expect(form.get('file')).toBe(file);expect(JSON.parse(String(form.get('configuration')))).toEqual(body);expect(post).toHaveBeenCalledTimes(1)
})
