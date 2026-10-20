import { describe,it,expect } from 'vitest'
import { http, ApiError, errorText } from './client'
import { AxiosError } from 'axios'

describe('服务错误中文化', () => {
  it('未知浏览器技术异常不直接展示给销管人员', () => {
    expect(errorText(new Error('internal TypeError private stack'))).toMatch(/[\u4e00-\u9fff]/)
    expect(errorText(new Error('internal TypeError private stack'))).not.toContain('TypeError')
  })
  it.each([404,409,422,500,502])('HTTP %s 有明确的中文提示', async status => {
    http.defaults.adapter=async config => { throw new AxiosError('secret stack',undefined,config,undefined,{status,statusText:'error',headers:{},config,data:{}}) }
    try { await http.get('/unit-test'); throw new Error('should reject') }
    catch (e) { expect(e).toBeInstanceOf(ApiError); expect(errorText(e)).not.toContain('secret'); expect(errorText(e)).toMatch(/[\u4e00-\u9fff]/) }
  })
  it('后端中文错误与请求编号可用于排查', async () => {
    http.defaults.adapter=async config => { throw new AxiosError('technical',undefined,config,undefined,{status:409,statusText:'error',headers:{},config,data:{message:'同一幂等键已用于不同客户或不同文件内容',code:'IDEMPOTENCY_CONFLICT',request_id:'trace-1'}}) }
    try { await http.get('/unit-test') } catch(e) { expect(errorText(e)).toContain('不同客户'); expect(errorText(e)).toContain('trace-1') }
  })
  it('没有服务响应时不暴露技术堆栈', async () => {
    http.defaults.adapter=async config => { throw new AxiosError('socket details','ECONNABORTED',config) }
    try { await http.get('/unit-test') } catch(e) { expect(errorText(e)).toContain('超时'); expect(errorText(e)).not.toContain('socket') }
  })
})
