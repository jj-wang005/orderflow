<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { UploadFilled, Tickets, ShoppingBag, Connection, RefreshRight, ArrowRight, Warning } from '@element-plus/icons-vue'
import { api } from '../api/orders'
import CDouAssistant from '../components/CDouAssistant.vue'
const route = useRoute()
const connected = ref<boolean | null>(null), checking = ref(false)
const environmentLabel=ref('环境待检查'),databaseName=ref('')
const navGroups = [
  {title:'业务管理',items:[{path:'/orders',title:'销售订单',icon:ShoppingBag},{path:'/order-entry',title:'订单录入',icon:UploadFilled},{path:'/tasks',title:'导入任务',icon:Tickets}]},
  {title:'基础管理',items:[{path:'/customers',title:'客户配置管理',icon:Connection}]},
]
const activeMenu = computed(()=>String(route.meta.activeMenu||route.path))
const current = computed(() => String(route.meta.title || '销管工作台'))
watch(current, title => { document.title = title + ' · OrderFlow · 销管工作台' }, {immediate:true})
async function check() {
  if (checking.value) return
  checking.value = true
  try { const health = await api.health();const expected=import.meta.env.VITE_APP_ENV;const correct=!expected||health.environment===expected;connected.value = correct&&health.status === 'ok' && health.database === 'ok';environmentLabel.value=correct?(health.environment_label||'环境未标识'):'环境不一致，已阻止写入';databaseName.value=health.database_name||'' }
  catch { connected.value = false } finally { checking.value = false }
}
onMounted(check)
</script>
<template>
  <div class="workspace">
    <aside class="sidebar">
      <router-link to="/orders" class="brand"><span class="brand-symbol">单</span><span><strong>OrderFlow</strong><small>销管工作台</small></span></router-link>
      <nav aria-label="主导航"><div v-for="group in navGroups" :key="group.title" class="nav-group"><div class="nav-label">{{group.title}}</div><router-link v-for="item in group.items" :key="item.path" :to="item.path" class="nav-item" :class="{selected:activeMenu.startsWith(item.path)}" :aria-current="activeMenu.startsWith(item.path)?'page':undefined"><el-icon><component :is="item.icon" /></el-icon><span>{{item.title}}</span><el-icon class="nav-arrow"><ArrowRight /></el-icon></router-link></div></nav>
      <div class="sidebar-bottom"><span class="side-dot"></span> 客户采购 · 自动录单<small>V2.0-C · 销售运营</small></div>
    </aside>
    <div class="workspace-main">
      <header class="topbar"><div class="topbar-title">OrderFlow<span class="topbar-divider"></span><span class="topbar-caption">销售运营</span></div><div class="topbar-actions"><button class="health-button" @click="check" :disabled="checking" :title="'点击检查后端与数据库连接'"><span class="health-dot" :class="{ online: connected, offline: connected === false }"></span>{{ checking ? '检查连接中' : connected ? '后端连接正常' : connected === false ? '后端未连接' : '连接待检查' }}<el-icon><RefreshRight /></el-icon></button><span class="environment-pill">{{environmentLabel}} · {{databaseName}}</span><span class="role-label"><span class="avatar">销</span> 我司销管</span></div></header>
      <main class="page"><div class="breadcrumb">销售运营 <el-icon><ArrowRight /></el-icon> 自动录单 <el-icon><ArrowRight /></el-icon><span>{{ current }}</span></div><div class="environment-note"><el-icon><Warning /></el-icon><span>请核对客户采购信息，订单金额与处理结果以系统保存记录为准。</span></div><slot /></main>
      <footer class="workspace-footer">OrderFlow · 销管工作台<span>可靠匹配 · 按行建单 · 来源可追溯</span></footer>
    </div>
    <CDouAssistant/>
  </div>
</template>
