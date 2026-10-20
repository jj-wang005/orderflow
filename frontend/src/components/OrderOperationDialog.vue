<script setup lang="ts">
import {ref,shallowRef,onMounted,computed} from 'vue'
import {api} from '../api/orders'
import {ApiError,errorText} from '../api/client'
import type {CorrectionField,OrderOperationBody,OrderOperationResult,OrderEditContext} from '../types/api'
const props=defineProps<{mode:'manual'|'edit';taskId?:number;rowId?:number;orderId?:number}>()
const emit=defineEmits<{close:[];saved:[OrderOperationResult]}>()
const busy=ref(false),loading=ref(false),error=ref(''),ready=ref(false),blocked=ref(false),revision=ref(0),customer=ref(''),historical=ref(false)
const values=ref<Partial<Record<CorrectionField,string|null>>>({}),product=ref({product_code:'',product_name:'',unit_price:''}),note=ref(''),basis=ref('')
const history=ref<OrderEditContext['history']>([]),pending=shallowRef<OrderOperationBody|null>(null)
const currentPrice=ref(''),currentAmount=ref(''),referencePrice=ref<string|null>(null)
// Integer decimal arithmetic avoids floating point rounding in the preview.
function priceUnits(value:string):bigint|null{
  if(!/^\d+(\.\d{1,4})?$/.test(value)||value.length>128)return null
  const [whole='',fraction='']=value.split('.')
  const units=BigInt(whole)*10000n+BigInt(fraction.padEnd(4,'0'))
  return units>0n&&units<=999999999999999999n?units:null
}
const priceChanged=computed(()=>props.mode==='edit'&&priceUnits(product.value.unit_price)!==priceUnits(currentPrice.value))
const preview=computed(()=>{
  const units=priceUnits(product.value.unit_price),qty=values.value.quantity||''
  if(units===null||!/^\d+(\.0{1,3})?$/.test(qty)||qty.length>128)return '—'
  const quantity=BigInt(qty.split('.')[0]!)
  if(quantity<=0n||quantity>999999999999999n)return '—'
  const cents=(units*quantity+50n)/100n
  if(cents>=100000000000000000000n)return '金额超出允许范围'
  return `${cents/100n}.${(cents%100n).toString().padStart(2,'0')}`
})
const fields:{field:CorrectionField;label:string}[]=[{field:'customer_order_no',label:'客户采购单号'},{field:'customer_material_code',label:'客户料号'},{field:'customer_model',label:'客户机型'},{field:'customer_batch',label:'客户批次'},{field:'quantity',label:'采购数量'}]
const key=`cvte:order-operation:${props.mode}:${props.orderId || `${props.taskId}:${props.rowId}`}`
async function load(){
  loading.value=true
  try{
    if(props.mode==='manual'){
      const c=await api.correction(props.taskId!,props.rowId!);customer.value=c.customer_id;revision.value=c.revision
      ready.value=c.row.status==='NOT_MATCHED' && !c.row.created_order_id
      for(const f of fields)values.value[f.field]=c.row.normalized_data?.[f.field]??null
    }else{
      const c=await api.editContext(props.orderId!);history.value=c.history;const o=c.order
      ready.value=c.editable;customer.value=o.customer_id;revision.value=o.edit_revision;historical.value=!!o.source_order_id
      currentPrice.value=o.unit_price;currentAmount.value=o.amount;referencePrice.value=c.historical_unit_price??null
      for(const f of fields)values.value[f.field]=o[f.field]
      product.value={product_code:o.product_code,product_name:o.product_name,unit_price:o.unit_price}
    }
    const raw=localStorage.getItem(key)
    if(raw){const p=JSON.parse(raw) as OrderOperationBody;if(typeof p.request_key!=='string'||!p.product||!Number.isSafeInteger(p.expected_revision))throw new Error('invalid recovery');pending.value=p;product.value={...p.product};if(p.values)values.value={...p.values};note.value=p.note;basis.value=p.verification_basis || ''}
  }catch(e){blocked.value=true;error.value=errorText(e)}finally{loading.value=false}
}
async function save(){
  if(busy.value||blocked.value||(!ready.value&&!pending.value))return
  if(!pending.value){
    if(!note.value.trim()||Object.values(product.value).some(v=>!v.trim())){error.value='请完整填写产品编码、产品名称、销售单价和操作说明。';return}
    if(priceUnits(product.value.unit_price)===null){error.value='销售单价须为合法正数，最多14位整数、4位小数。';return}
    if(preview.value==='—'||preview.value==='金额超出允许范围'){error.value='采购数量须为合法正整数，且订单金额不能超出允许范围。';return}
    if(priceChanged.value&&!basis.value.trim()){error.value='修改单价必须填写产品与价格核对依据。';return}
    pending.value={request_key:crypto.randomUUID(),expected_revision:revision.value,product:{...product.value},note:note.value.trim(),verification_basis:basis.value.trim() || null,...(props.mode==='edit'?{values:{...values.value}}:{})}
    try{localStorage.setItem(key,JSON.stringify(pending.value))}catch{pending.value=null;blocked.value=true;error.value='无法保存重试标识，未提交服务器';return}
  }
  busy.value=true;error.value=''
  try{
    const result=props.mode==='manual'?await api.manualOrder(props.taskId!,props.rowId!,pending.value):await api.editOrder(props.orderId!,pending.value)
    localStorage.removeItem(key);pending.value=null;emit('saved',result);emit('close')
  }catch(e){error.value=errorText(e);if(e instanceof ApiError && [404,409,422].includes(e.status||0)){try{localStorage.removeItem(key);pending.value=null;if(e.status!==422){if(props.mode==='edit'){blocked.value=true;error.value+='；输入已保留，请复制所需内容，关闭后重新打开核对最新订单。'}else await load()}}catch{blocked.value=true}}}
  finally{busy.value=false}
}
onMounted(load)
</script>
<template><el-dialog :model-value="true" :title="mode==='manual'?'未匹配采购行 · 手动新增销售订单':'编辑已创建销售订单'" width="min(760px,95vw)" :close-on-click-modal="false" :show-close="!busy" :close-on-press-escape="!busy" @update:model-value="!busy && emit('close')">
  <div v-loading="loading">
  <el-alert v-if="error" :title="error" type="error" :closable="false" /><el-alert v-if="pending" title="结果待确认，请沿用原操作重试；提交内容已锁定。" type="info" :closable="false" />
  <el-alert v-if="!ready && !loading && !pending" title="当前状态不允许此操作，请刷新原页面核对。" type="info" :closable="false" />
  <el-form label-position="top" :disabled="busy||!!pending||!ready||blocked"><p>客户 {{ customer }} · 版本 {{ revision }}</p>
    <el-form-item v-for="f in fields" :key="f.field" :label="f.label"><el-input v-model="values[f.field]" :aria-label="f.label" :disabled="mode==='manual'" maxlength="128" /></el-form-item>
    <el-alert v-if="historical" title="历史翻单：产品和名称只读，客户字段修改后仍须可靠匹配原参考。仅调整本次订单单价，不修改历史订单或其他订单。" type="info" :closable="false" />
    <p v-if="mode==='edit'">历史参考单价：{{referencePrice ?? '无历史参考'}}<br>当前订单单价：{{currentPrice}}</p>
    <el-form-item label="我司产品编码" required><el-input v-model="product.product_code" aria-label="我司产品编码" :disabled="mode==='edit'" maxlength="128" placeholder="请输入完整产品编码" /></el-form-item>
    <el-form-item label="我司产品名称" required><el-input v-model="product.product_name" aria-label="我司产品名称" :disabled="mode==='edit'" maxlength="255" placeholder="请输入完整产品名称" /></el-form-item>
    <el-form-item :label="mode==='edit'?'适用销售单价（本次拟修改）':'适用销售单价'" required><el-input v-model="product.unit_price" type="number" inputmode="decimal" min="0.0001" max="99999999999999.9999" step="0.0001" aria-label="适用销售单价" placeholder="合法正数，最多4位小数" /></el-form-item>
    <el-form-item :label="priceChanged?'产品与价格核对依据（改价必填）':'产品与价格核对依据（选填）'" :required="priceChanged"><el-input v-model="basis" type="textarea" aria-label="产品与价格核对依据" maxlength="1000" placeholder="可填写报价单编号、最新协议或业务核实说明" /></el-form-item>
    <p v-if="mode==='edit'">保存预览：单价 {{currentPrice}} → {{product.unit_price || '—'}}；订单金额 {{currentAmount}} → {{preview}}。核对依据为人工说明，不代表系统已通过真实 ERP/OMS 核验。</p>
    <el-form-item label="操作说明 / 修改原因" required><el-input v-model="note" type="textarea" aria-label="操作说明" maxlength="500" /></el-form-item>
  </el-form><p class="muted">金额按数量和单价计算，保存后以订单详情为准。</p>
  <el-collapse v-if="history.length"><el-collapse-item title="查看订单修改历史"><div v-for="h in history" :key="h.id"><p>{{h.created_at}} · {{h.actor_label}} · {{h.note}}</p><pre class="json-view">{{JSON.stringify({修改前:h.before_data,修改后:h.after_data,核对依据:h.payload.verification_basis},null,2)}}</pre></div></el-collapse-item></el-collapse>
  </div><template #footer><el-button :disabled="busy" @click="emit('close')">关闭</el-button><el-button type="primary" :loading="busy" :disabled="loading||blocked||(!ready&&!pending)" @click="save">{{pending?'使用原操作重试':mode==='manual'?'创建订单':'保存修改'}}</el-button></template>
</el-dialog></template>
<style scoped>.el-alert{margin-bottom:12px}.el-form{margin-top:16px}.muted{margin:16px 0}</style>
