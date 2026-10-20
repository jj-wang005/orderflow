<script setup lang="ts">defineProps<{ title: string; subtitle: string; eyebrow?: string }>()</script>
<template><div class="page-heading"><div><p v-if="eyebrow" class="eyebrow">{{ eyebrow }}</p><h1>{{ title }}</h1><p class="page-subtitle">{{ subtitle }}</p></div><div class="heading-action"><slot /></div></div></template>
