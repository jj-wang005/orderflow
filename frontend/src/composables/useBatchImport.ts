import {computed,ref,shallowRef} from 'vue'
import {importApi,type BatchItemResult} from '../api/imports'
import {fileHash} from '../utils/storage'
import {errorText} from '../api/client'
import {api} from '../api/orders'
import type {TaskSummary} from '../types/api'
export const BATCH_STORAGE='cvte-order.batch.v2'
export interface UploadEntry {slot_id:string;request_key:string;customer_id:string;file_name:string;size:number;hash:string;file:File|null;duplicate_confirmed:boolean;previous:TaskSummary|null;task:TaskSummary|null;error:BatchItemResult['error'];sent:boolean;cancelled:boolean;retry_of_task_id:number|null}
export function useBatchImport(){
 const batchId=ref(crypto.randomUUID()),entries=shallowRef<UploadEntry[]>([]),busy=ref(false),error=ref(''),blocked=ref(false)
 function persist(){try{localStorage.setItem(BATCH_STORAGE,JSON.stringify({version:1,batch_id:batchId.value,entries:entries.value.map(({file,...e})=>e)}))}catch{throw new Error('无法保存批量导入恢复记录，本次请求未发送。请检查浏览器存储权限。')}}
 try{const raw=localStorage.getItem(BATCH_STORAGE);if(raw){const saved=JSON.parse(raw);if(saved.version!==1||typeof saved.batch_id!=='string'||!Array.isArray(saved.entries)||saved.entries.length>20||saved.entries.some((e:UploadEntry)=>typeof e.slot_id!=='string'||typeof e.request_key!=='string'||typeof e.hash!=='string'||!/^[a-f0-9]{64}$/.test(e.hash)||typeof e.customer_id!=='string'||typeof e.sent!=='boolean'))throw new Error();batchId.value=saved.batch_id;entries.value=saved.entries.map((e:UploadEntry)=>({...e,file:null}))}}
 catch{error.value='批量恢复记录无法读取，请先核对历史任务，勿重复新建采购。';blocked.value=true}
 function update(entry:UploadEntry,changes:Partial<UploadEntry>){entries.value=entries.value.map(e=>e.slot_id===entry.slot_id?{...e,...changes}:e)}
 async function addFiles(files:File[],retry?:{task_id:number;customer_id:string}){
  if(busy.value||blocked.value)return;error.value=''
  try{for(const file of files){if(!/\.xlsx$/i.test(file.name)||file.size>10485760)throw new Error('仅支持不超过 10 MiB 的 .xlsx 文件。');const hash=await fileHash(file)
   const restored=entries.value.find(e=>!e.file&&!e.task&&!e.cancelled&&e.hash===hash&&e.file_name===file.name)
   if(restored){update(restored,{file});continue}
   if(entries.value.some(e=>e.sent&&!e.task&&!e.cancelled))throw new Error('有结果待确认的文件，请重新选择其原始文件并沿用原操作重试。')
   if(entries.value.length>=20||(retry&&entries.value.length))throw new Error(retry?'修复重传请每次选择一份文件。':'每批最多 20 份文件。')
   entries.value=[...entries.value,{slot_id:crypto.randomUUID(),request_key:'CVTE-B-'+crypto.randomUUID(),customer_id:retry?.customer_id||'',file_name:file.name,size:file.size,hash,file,duplicate_confirmed:false,previous:null,task:null,error:null,sent:false,cancelled:false,retry_of_task_id:retry?.task_id||null}]
  }persist()}catch(e){error.value=errorText(e)}
 }
 function assign(customer:string){if(busy.value)return;entries.value=entries.value.map(e=>e.sent||e.task||e.retry_of_task_id?e:{...e,customer_id:customer,previous:null,duplicate_confirmed:false});try{persist()}catch(e){error.value=errorText(e)}}
 function setCustomer(entry:UploadEntry,customer:string){if(entry.sent||busy.value)return;update(entry,{customer_id:customer,previous:null,duplicate_confirmed:false});try{persist()}catch(e){error.value=errorText(e)}}
 function cancel(entry:UploadEntry){if(busy.value||entry.task||(entry.sent&&entry.error?.code!=='DUPLICATE_FILE'))return;update(entry,{cancelled:true});try{persist()}catch(e){error.value=errorText(e)}}
 const pending=computed(()=>entries.value.filter(e=>!e.task&&!e.cancelled))
 const canSubmit=computed(()=>!busy.value&&!blocked.value&&pending.value.length>0&&pending.value.every(e=>e.customer_id&&e.file))
 async function execute(){if(!canSubmit.value)return;busy.value=true;error.value='';try{
  const send:UploadEntry[]=[]
  for(const entry of pending.value){
   if(!entry.duplicate_confirmed&&!entry.sent){const checked=await importApi.duplicate(entry.customer_id,entry.file!);if(checked.previous){update(entry,{previous:checked.previous,error:{code:'DUPLICATE_FILE',message:'相同客户及内容已存在，请查看上次结果、取消或确认再次导入。'}});continue}}
   send.push(entry)
  }
  if(!send.length){persist();return}
  send.forEach(e=>update(e,{sent:true,error:null}));persist() // Stable keys are saved BEFORE the POST.
  const result=await importApi.batch(batchId.value,send.map(e=>({file:e.file!,meta:{slot_id:e.slot_id,customer_id:e.customer_id,request_key:e.request_key,duplicate_confirmed:e.duplicate_confirmed,retry_of_task_id:e.retry_of_task_id}})))
  for(const item of result.items){const entry=entries.value.find(e=>e.slot_id===item.slot_id);if(!entry)continue;update(entry,{task:item.task,error:item.error,...(item.error&&['CONFIG_ERROR','INVALID_RETRY_SOURCE','INVALID_FILE','FILE_TOO_LARGE'].includes(item.error.code)?{sent:false}:{})})
   if(item.error?.code==='DUPLICATE_FILE'){const checked=await importApi.duplicate(entry.customer_id,entry.file!);update(entry,{previous:checked.previous})}
  }
  persist()
 }catch(e){error.value=errorText(e)+' 请保留原文件和当前批次，沿用原操作重试。'}finally{busy.value=false}}
 async function confirmAgain(entry:UploadEntry){update(entry,{duplicate_confirmed:true});await execute()}
 async function refreshResults(){if(busy.value)return;busy.value=true;try{for(const entry of entries.value){if(entry.task){const current=await api.task(entry.task.task_id);if(current.customer_id!==entry.customer_id)throw new Error('任务客户与原批次不一致，请核对环境');update(entry,{task:current})}}persist()}catch(e){error.value=errorText(e)}finally{busy.value=false}}
 function startNew(){if(busy.value||blocked.value)return;if(entries.value.some(e=>e.sent&&!e.task&&!e.cancelled)){error.value='当前批次仍有结果待确认的操作，请先沿用原键恢复。';return}try{localStorage.removeItem(BATCH_STORAGE);batchId.value=crypto.randomUUID();entries.value=[];error.value=''}catch(e){error.value=errorText(e)}}
 const summary=computed(()=>{const tasks=entries.value.flatMap(e=>e.task?[e.task]:[]);return {files:entries.value.length,completed:tasks.length,cancelled:entries.value.filter(e=>e.cancelled).length,success:tasks.reduce((s,t)=>s+t.success_rows,0),unmatched:tasks.reduce((s,t)=>s+t.unmatched_rows,0),failed:tasks.reduce((s,t)=>s+t.failed_rows,0),duplicate:tasks.reduce((s,t)=>s+(t.duplicate_rows||0),0),review:tasks.reduce((s,t)=>s+(t.review_rows||0),0),fileErrors:tasks.filter(t=>t.error_message&&t.total_rows===0).length+entries.value.filter(e=>!e.task&&!e.cancelled&&e.error&&e.error.code!=='DUPLICATE_FILE').length}})
 return {batchId,entries,busy,error,blocked,canSubmit,summary,addFiles,assign,setCustomer,cancel,execute,confirmAgain,startNew,refreshResults}
}
