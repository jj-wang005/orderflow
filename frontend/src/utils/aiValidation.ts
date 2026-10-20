import type {AIResult,AIField} from '../api/ai'
import {fieldLabels} from '../api/customers'
export interface FieldIssue {row:number;field:AIField|'risk';message:string;kind:'missing'|'format'|'risk'}
export function validateDraft(result:AIResult,required:AIField[]):FieldIssue[]{
 const issues:FieldIssue[]=[]
 result.rows.forEach((row,index)=>{
  for(const field of required){if(!row[field]?.trim())issues.push({row:index,field,kind:'missing',message:`第 ${index+1} 行：${fieldLabels[field]}不能为空`})}
  if(row.quantity?.trim()&&(!/^\d+(?:\.0+)?$/.test(row.quantity.trim())||Number(row.quantity)<=0||Number(row.quantity)>=1e15))issues.push({row:index,field:'quantity',kind:'format',message:`第 ${index+1} 行：数量必须是小于 10^15 的正整数`})
  if(row.uncertain_fields.length)issues.push({row:index,field:'risk',kind:'risk',message:`第 ${index+1} 行：AI识别风险，请对照原文核实 ${row.uncertain_fields.map(f=>fieldLabels[f]).join('、')}`})
 })
 return issues
}
