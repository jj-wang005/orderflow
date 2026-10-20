import { ref, onMounted } from 'vue'
import { customerApi, type Customer } from '../api/customers'
import { errorText } from '../api/client'
export function useCustomers() {
  const customers=ref<Customer[]>([]), customerError=ref('')
  async function loadCustomers(){try{customers.value=await customerApi.list();customerError.value=''}catch(e){customerError.value=errorText(e)}}
  onMounted(loadCustomers)
  return {customers,customerError,loadCustomers}
}
