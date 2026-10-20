import { http } from './client'
import type { Envelope } from '../types/api'
export const fieldLabels: Record<string, string> = {customer_order_no:'客户采购单号',customer_material_code:'客户料号',customer_batch:'客户批次',customer_model:'客户机型',quantity:'采购数量'}
export interface Customer {customer_id:string;customer_name:string;enabled:boolean;configured:boolean;config_version:number}
export interface QuantityFilter {operator:'gt'|'gte'|'eq';value:string}
export interface SourceColumn {index:number;name:string|null}
export interface ConfigBody {expected_version:number;customer_name:string;sheet_name:string;header_row:number;field_mapping:Record<string,string>;match_fields:string[];historical_quantity:QuantityFilter|null;enabled:boolean;source_columns?:SourceColumn[]}
export interface RuleTemplate {id:string;name:string;match_fields:string[];historical_quantity:QuantityFilter|null;recommended?:boolean}
export interface CustomerDetail extends Customer {header_source?:'saved'|'original_template'|'upload_required';configuration:null|{source_columns?:SourceColumn[];customer:{sheet_name:string;header_row:number;field_mapping:Record<string,string>};rule:{match_fields:string[];validation_config:{consistency_fields:string[]};filter_conditions:{historical_quantity:QuantityFilter|null}}};versions:{config_version:number;rule_version:number;created_at:string;snapshot:unknown;task_count:number}[]}
export interface MappingPreview {sheet_names:string[];sheet_name:string;header_row:number;columns:{index?:number;column:string|null;mappable?:boolean;samples:unknown[];recommended_field:string|null;reason:string}[];templates:RuleTemplate[]}
export interface ConfigPreview {file_error:null|{message:string};total_rows:number;valid_rows:number;invalid_rows:number;rows:{row_number:number;status:string;data:Record<string,unknown>|null;errors:{message:string}[]}[]}
export interface TrialReference {id:number;order_no:string;product_code:string;product_name:string;unit_price:string;source_type:string}
export interface TrialPreview {checked_at:string;file_error:null|{message:string};total_rows:number;evaluated_rows:number;row_limit:number;truncated:boolean;summary:{matched_rows:number;unmatched_rows:number;ambiguous_rows:number;other_rows:number};rows:{row_number:number;sheet_name:string;match_values:Record<string,unknown>;result:{status:string;message:string;matched_order:TrialReference|null;error_fields:string[]};candidates:TrialReference[]}[]}
export const trialStatusLabels:Record<string,string>={MATCHED:'匹配成功',NOT_MATCHED:'未匹配',AMBIGUOUS_MATCH:'产品歧义',INVALID_REFERENCE:'历史参考无效',INVALID_INPUT:'输入异常',CONFIG_ERROR:'配置异常',MATCH_ERROR:'查询异常'}
const data = <T>(response:{data:Envelope<T>}) => response.data.data
export const customerApi = {
  async list(){return data(await http.get<Envelope<Customer[]>>('/api/v1/customers'))},
  async get(id:string){return data(await http.get<Envelope<CustomerDetail>>(`/api/v1/customers/${encodeURIComponent(id)}`))},
  async create(body:{customer_id:string;customer_name:string}){return data(await http.post<Envelope<CustomerDetail>>('/api/v1/customers',body))},
  async edit(id:string,body:{expected_version:number;customer_name:string}){return data(await http.patch<Envelope<CustomerDetail>>(`/api/v1/customers/${encodeURIComponent(id)}`,body))},
  async save(id:string,body:ConfigBody){return data(await http.put<Envelope<CustomerDetail>>(`/api/v1/customers/${encodeURIComponent(id)}/config`,body))},
  async templates(){return data(await http.get<Envelope<{templates:RuleTemplate[]}>>('/api/v1/match-rule-templates'))},
  async suggest(file:File,sheet_name:string,header_row:number){const form=new FormData();form.append('file',file);if(sheet_name)form.append('sheet_name',sheet_name);form.append('header_row',String(header_row));return data(await http.post<Envelope<MappingPreview>>('/api/v1/customers/field-mapping/preview',form))},
  async preview(id:string,file:File,body:ConfigBody){const form=new FormData();form.append('file',file);form.append('configuration',JSON.stringify(body));return data(await http.post<Envelope<ConfigPreview>>(`/api/v1/customers/${encodeURIComponent(id)}/config/preview`,form))},
  async trial(id:string,file:File,body:ConfigBody){const form=new FormData();form.append('file',file);form.append('configuration',JSON.stringify(body));return data(await http.post<Envelope<TrialPreview>>(`/api/v1/customers/${encodeURIComponent(id)}/config/trial-match`,form))},
}
