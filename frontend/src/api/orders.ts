import { http, ApiError } from './client'
import type {OrderStatus,StatusRequest} from '../types/api'
import type { OrderOperationBody,OrderOperationResult,OrderEditContext } from '../types/api'
import type { CustomerId, Envelope, TaskSummary, Page, RowDetail, OrderDetail, CorrectionContext, CorrectionRequest, CorrectionResult } from '../types/api'

function unwrap<T>(body: Envelope<T>): T {
  if (body.code !== 'OK' || body.data === undefined) throw new ApiError('服务返回了无法识别的结果，请沿用原操作确认。')
  return body.data
}
export const api = {
  async voidOrder(id:number,body:{request_key:string;expected_revision:number;confirmed:true;note:string}) {return unwrap((await http.post<Envelope<OrderOperationResult>>(`/api/v1/orders/${id}/void`,body)).data)},
  async statusOrder(id:number,body:StatusRequest) {return unwrap((await http.patch<Envelope<OrderOperationResult>>(`/api/v1/orders/${id}/status`,body)).data)},
  async manualOrder(task:number,row:number,body:OrderOperationBody) { return unwrap((await http.post<Envelope<OrderOperationResult>>(`/api/v1/imports/${task}/rows/${row}/manual-order`,body)).data) },
  async editContext(id:number) { return unwrap((await http.get<Envelope<OrderEditContext>>(`/api/v1/orders/${id}/edit`)).data) },
  async editOrder(id:number,body:OrderOperationBody) { return unwrap((await http.patch<Envelope<OrderOperationResult>>(`/api/v1/orders/${id}`,body)).data) },
  async correction(task: number, row: number) { return unwrap((await http.get<Envelope<CorrectionContext>>(`/api/v1/imports/${task}/rows/${row}/correction`)).data) },
  async correct(task: number, row: number, body: CorrectionRequest) { return unwrap((await http.post<Envelope<CorrectionResult>>(`/api/v1/imports/${task}/rows/${row}/correction`, body)).data) },
  async health() { return (await http.get<{ status: string; database: string;environment?:string;database_name?:string;environment_label?:string }>('/health', { timeout: 8_000 })).data },
  async upload(customer: CustomerId, file: File, key: string, onProgress?: (value: number) => void, options?: {duplicate_confirmed?:boolean}) {
    const body = new FormData(); body.append('customer_id', customer); body.append('file', file); if(options?.duplicate_confirmed) body.append('duplicate_confirmed','true')
    const response = await http.post<Envelope<TaskSummary>>('/api/v1/imports', body, {
      headers: { 'Idempotency-Key': key },
      onUploadProgress: e => { if (e.total) onProgress?.(Math.round(e.loaded / e.total * 100)) },
    })
    return unwrap(response.data)
  },
  async task(id: number) { return unwrap((await http.get<Envelope<TaskSummary>>(`/api/v1/imports/${id}`)).data) },
  async rows(id: number, page = 1, pageSize = 20) { return unwrap((await http.get<Envelope<Page<RowDetail>>>(`/api/v1/imports/${id}/rows`, { params: { page, page_size: pageSize } })).data) },
  async orders(params: { page: number; page_size: number; customer_id?: CustomerId; source_type?: 'SEED' | 'AUTO';status?:string;date_from?:string;date_to?:string }) {
    return unwrap((await http.get<Envelope<Page<OrderDetail>>>('/api/v1/orders', { params })).data)
  },
  async order(id: number) { return unwrap((await http.get<Envelope<OrderDetail>>(`/api/v1/orders/${id}`)).data) },
}
