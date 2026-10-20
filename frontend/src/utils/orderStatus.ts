import type {OrderStatus} from '../types/api'
export const orderStates:{value:OrderStatus;label:string}[]=[{value:'CREATED',label:'已创建'},{value:'VALID',label:'已确认'},{value:'PROCESSING',label:'进行中'},{value:'SHIPPED',label:'已发货'},{value:'COMPLETED',label:'已完成'}]
export const nextState:Partial<Record<OrderStatus,OrderStatus>>={CREATED:'VALID',VALID:'PROCESSING',PROCESSING:'SHIPPED',SHIPPED:'COMPLETED'}
export const orderFilterStates=[...orderStates,{value:'VOIDED',label:'已作废'},{value:'INVALID',label:'无效历史订单'}]
export function orderReadOnlyReason(order:{status:string;source_type:string;import_task_id:number|null}){
 if(order.status==='VOIDED')return '已作废，不可恢复、编辑或流转；采购来源仍保留'
 if(order.source_type!=='AUTO'||!order.import_task_id)return '历史参考或无采购来源订单只读'
 if(order.status!=='CREATED')return '仅已创建订单可编辑或作废'
 return ''
}
