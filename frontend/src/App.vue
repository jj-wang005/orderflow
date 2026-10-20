<script setup lang="ts">
import WorkspaceLayout from './layouts/WorkspaceLayout.vue'
</script>
<template><WorkspaceLayout><router-view v-slot="{ Component }"><KeepAlive include="OrderEntryView"><component :is="Component" /></KeepAlive></router-view></WorkspaceLayout></template>
