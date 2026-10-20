import axios from 'axios'

export class ApiError extends Error {
  constructor(message: string, public status?: number, public code?: string, public requestId?: string) { super(message) }
}
export const http = axios.create({ baseURL: import.meta.env.VITE_API_BASE_URL || '', timeout: 120_000 })
// A managed frontend must verify the backend identity before every write.
http.interceptors.request.use(async config=>{
  const environment=import.meta.env.VITE_APP_ENV
  if((environment==='demo'||environment==='test')&&!['get','head','options'].includes(config.method||'get')){
    const {data}=await axios.get('/health',{timeout:5000})
    if(data.environment!==environment||data.database_name!=='order_'+environment)throw new ApiError('页面与后端环境不一致，已阻止操作',409,'ENVIRONMENT_MISMATCH')
  }
  return config
})
http.interceptors.response.use(response => response, async (error: unknown) => {
  if(error instanceof ApiError)return Promise.reject(error)
  if (axios.isAxiosError(error)) {
    const status = error.response?.status
    let payload = error.response?.data as { message?: string; code?: string; request_id?: string } | undefined
    if(error.response?.data instanceof Blob){try{payload=JSON.parse(await error.response.data.text())}catch{payload=undefined}}
    const fallback: Record<number, string> = {
      400: '文件内容或格式不符合要求，请检查 Excel。', 404: '查询的任务或订单不存在，请核对编号。',
      409: '该操作与原导入请求冲突，请勿重复新建任务。', 413: '文件超过服务器允许的大小。',
      422: '客户配置或请求参数不合法，请检查后重试。', 500: '服务处理异常，结果可能尚未确认，请沿用原操作重试。',
      502: '无法连接后端服务，请确认 FastAPI 已启动。', 503: '后端或数据库暂不可用，请稍后重试。',
    }
    const message = status
      ? (typeof payload?.message === 'string' ? payload.message : fallback[status] || '服务暂时无法完成请求，请稍后重试。')
      : error.code === 'ECONNABORTED' ? '请求超时，录单结果尚未确认。请使用原操作继续确认，避免重复建单。'
        : '无法连接服务，请检查网络及 FastAPI 启动状态。录单请求请沿用原操作重试。'
    return Promise.reject(new ApiError(message, status, payload?.code, payload?.request_id))
  }
  return Promise.reject(new ApiError('请求未完成，请稍后重试。'))
})
export function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message + (error.requestId ? `（请求编号：${error.requestId}）` : '')
  return error instanceof Error && /[\u4e00-\u9fff]/.test(error.message)
    ? error.message : '操作未完成，请检查文件与浏览器环境后重试。'
}
