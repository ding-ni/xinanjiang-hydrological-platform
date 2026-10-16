<template><LegacyArchiveView v-if="route.query.legacy === '1' || !app.usesModelBackend || app.basinId !== 'nananxi-xufan'" /><ResultWorkspace v-else /></template>
<script setup>
import { useRoute } from 'vue-router'
import { useAppStore } from '@/stores/app'
import ResultWorkspace from './ResultWorkspace.vue'
import LegacyArchiveView from './LegacyArchiveView.vue'
const app = useAppStore(), route = useRoute()
</script>
