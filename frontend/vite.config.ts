import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const managed=mode==='demo'||mode==='test'
  const port=Number(env.CVTE_BACKEND_PORT||(mode==='test'?'8001':'8010'))
  if(managed&&(!Number.isInteger(port)||port<1024||port>65535))throw new Error('Invalid backend port')
  const expected=`http://127.0.0.1:${port}`
  if(managed&&((env.API_PROXY_TARGET&&env.API_PROXY_TARGET!==expected)||env.VITE_API_BASE_URL))throw new Error('Managed environment proxy mismatch; refusing fallback')
  const target = managed?expected:(env.API_PROXY_TARGET || 'http://127.0.0.1:8000')
  return {
    define:managed?{'import.meta.env.VITE_APP_ENV':JSON.stringify(mode),'import.meta.env.VITE_API_BASE_URL':JSON.stringify('')}:{},
    plugins: [vue()],
    server: { port: 5173, strictPort: true, proxy: { '/api': { target, changeOrigin: true }, '/health': { target, changeOrigin: true } } },
    preview: { port: 4173, strictPort: true, proxy: { '/api': { target, changeOrigin: true }, '/health': { target, changeOrigin: true } } },
  }
})
