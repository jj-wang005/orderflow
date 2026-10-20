import {http} from './client'
import type {Envelope,Page,TaskSummary,RowDetail} from '../types/api'
export interface BatchMeta {slot_id:string;customer_id:string;request_key:string;duplicate_confirmed:boolean;retry_of_task_id?:number|null}
export interface BatchItemResult {slot_id:string;file_name:string;customer_id:string;task:TaskSummary|null;error:{code:string;message:string}|null}
export interface BatchResult {batch_id:string;items:BatchItemResult[];summary:{file_count:number;completed_files:number;file_error_count:number;success_rows:number;unmatched_rows:number;failed_rows:number;duplicate_rows:number;review_rows:number}}
export interface ReviewCandidate {id:number;order_no:string;customer_order_no:string|null;customer_material_code:string|null;customer_model?:string|null;customer_batch?:string|null;source_sheet_name?:string|null;source_row_number?:number|null;status?:string;quantity:string;created_at:string;import_task_id:number|null}
const unwrap=<T>(r:{data:Envelope<T>})=>r.data.data
export const importApi={
 async duplicate(customer_id:string,file:File){const body=new FormData();body.append('customer_id',customer_id);body.append('file',file);return unwrap(await http.post<Envelope<{file_hash:string;previous:TaskSummary|null}>>('/api/v1/imports/duplicate-check',body))},
 async batch(batch_id:string,entries:{meta:BatchMeta;file:File}[]){const body=new FormData();body.append('batch_id',batch_id);body.append('items',JSON.stringify(entries.map(e=>e.meta)));entries.forEach(e=>body.append('files',e.file));return unwrap(await http.post<Envelope<BatchResult>>('/api/v1/import-batches',body))},
 async tasks(params:{page:number;page_size:number;customer_id?:string;file_name?:string;status?:string;date_from?:string;date_to?:string;batch_id?:string}){return unwrap(await http.get<Envelope<Page<TaskSummary>>>('/api/v1/imports',{params}))},
 async related(id:number){return unwrap(await http.get<Envelope<{parent:TaskSummary|null;retries:TaskSummary[];ai_extraction_id?:number|null}>>(`/api/v1/imports/${id}/related`))},
 async reviewContext(task:number,row:number){return unwrap(await http.get<Envelope<{row:RowDetail;revision:number;candidates:ReviewCandidate[];history:Record<string,unknown>[]}>>(`/api/v1/imports/${task}/rows/${row}/purchase-review`))},
 async review(task:number,row:number,body:{request_key:string;expected_revision:number;decision:'NEW'|'EXISTING';note:string;purchase_reference:string|null;existing_order_id:number|null}){return unwrap(await http.post<Envelope<{row:RowDetail;replayed:boolean}>>(`/api/v1/imports/${task}/rows/${row}/purchase-review`,body))},
}
