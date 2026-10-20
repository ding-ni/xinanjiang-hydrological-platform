<template>
  <div class="app">
    <AppTopbar />
    <TemporaryResultsDialog />
    <div class="shell">
      <AppSidebar />
      <main id="main-workspace" class="workspace">
        <router-view v-slot="{ Component }">
          <component :is="Component" />
        </router-view>
      </main>
    </div>

    <div v-if="app.reportOpen && app.reportData" class="report-mask" @click.self="app.closeReport()">
      <ForecastReport :data="app.reportData" @close="app.closeReport()" />
    </div>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import AppTopbar from '@/components/layout/AppTopbar.vue'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import ForecastReport from '@/components/report/ForecastReport.vue'
import TemporaryResultsDialog from '@/components/model/TemporaryResultsDialog.vue'
import { useAppStore } from '@/stores/app'

const app = useAppStore()

onMounted(() => {
  app.syncDomainWorkspaces(app.activeContext)
  // Register cases from the portable catalog before loading the active case's
  // data.  The store already contains an offline fallback, so the shell stays
  // usable even when a copied deployment is missing catalog.json temporarily.
  Promise.resolve(app.loadProjectCatalog()).finally(() => app.loadActiveProjectData())
})
</script>

<style scoped>
.app {
  width: 100%;
  height: 100vh;
  display: grid;
  grid-template-rows: var(--topbar) 1fr;
  background: var(--bg);
}

.shell {
  display: grid;
  grid-template-columns: var(--nav) 1fr;
  min-height: 0;
}

.workspace {
  position: relative;
  min-width: 0;
  min-height: 0;
  padding: 8px;
  overflow: auto;
}

@media (max-width: 1100px) {
  .app {
    height: auto;
    min-height: 100vh;
    grid-template-rows: auto 1fr;
  }

  .shell {
    grid-template-columns: 1fr;
    grid-template-rows: auto 1fr;
  }

  .workspace {
    overflow: visible;
  }

}

.report-mask {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(16, 32, 58, 0.45);
  overflow: auto;
}

@media print {
  .shell,
  .topbar {
    display: none !important;
  }
  .report-mask {
    position: static;
    background: #fff;
    padding: 0;
  }
}
</style>
