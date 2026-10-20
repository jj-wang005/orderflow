import {http} from './client'
export type AIField='customer_order_no'|'customer_material_code'|'customer_model'|'customer_batch'|'quantity'
export interface AIRow {
  customer_order_no:string|null;customer_material_code:string|null;customer_model:string|null;customer_batch:string|null;quantity:string|null
  source_id:string;source_excerpt:string;extraction_warnings:string[];uncertain_fields:AIField[]
}
export interface AIResult {rows:AIRow[];warnings:string[];removed_rows?:AIRow[]}
export interface AIDraft {
  required_fields?:AIField[];
  id:number;customer_id:string;file_name:string;source_kind:string;status:'PARSING'|'FAILED'|'DRAFT'|'CONFIRMED';provider:string;revision:number
  error_code:string|null;error_message:string|null;import_task_id:number|null;config_version:number;rule_version:number;created_at:string
  original_result:AIResult|null;edited_result:AIResult|null;source_manifest:{id:string;location:string;text:string;visual:boolean}[]
  audit:{action:string;at:string;note?:string;revision?:number}[];metrics:Record<string,unknown>
}
export interface AICapabilities {provider:string;model:string|null;key_configured:boolean;external_enabled:boolean;max_bytes:number;max_pdf_pages:number;max_rows:number}
export const ai={
  capabilities:async()=> (await http.get<{data:AICapabilities}>('/api/v1/ai-extractions/capabilities')).data.data,
  list:async(page=1,customer_id?:string)=>(await http.get<{data:{total:number;items:AIDraft[]}}>('/api/v1/ai-extractions',{params:{page,customer_id}})).data.data,
  get:async(id:number)=>(await http.get<{data:AIDraft}>('/api/v1/ai-extractions/'+id)).data.data,
  byTask:async(id:number)=>(await http.get<{data:AIDraft|null}>('/api/v1/ai-extractions/by-task/'+id)).data.data,
  create:async(form:FormData)=>(await http.post<{data:AIDraft}>('/api/v1/ai-extractions',form)).data.data,
  save:async(id:number,body:unknown)=>(await http.put<{data:AIDraft}>('/api/v1/ai-extractions/'+id,body)).data.data,
  confirm:async(id:number,body:unknown)=>(await http.post<{data:AIDraft}>('/api/v1/ai-extractions/'+id+'/confirm',body)).data.data,
  retry:async(id:number,body:unknown)=>(await http.post<{data:AIDraft}>('/api/v1/ai-extractions/'+id+'/retry',body)).data.data,
}
