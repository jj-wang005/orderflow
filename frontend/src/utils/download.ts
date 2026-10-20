import {http} from '../api/client'
export async function download(url:string,name:string,params?:Record<string,unknown>){
 const response=await http.get<Blob>(url,{params,responseType:'blob'})
 const href=URL.createObjectURL(response.data),link=document.createElement('a')
 link.href=href;link.download=name;document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(href),1000)
}
