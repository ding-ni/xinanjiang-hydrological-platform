import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],
  // Relative asset URLs support relocating the frontend and subdirectory hosting.
  base: './',
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    open: 'http://127.0.0.1:5173/',
    strictPort: false,
    proxy: { '/api/model': { target: 'http://127.0.0.1:8878', changeOrigin: false } }
  },
  build: {
    // Keep existing build files: the user explicitly prohibited deletion.
    // The new index references new assets; old unreferenced files are retained locally.
    // The release packager uses this build's manifest to omit those old chunks.
    emptyOutDir: false,
    manifest: true,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules/maplibre-gl')) return 'maplibre'
          if (id.includes('node_modules/echarts') || id.includes('node_modules/vue-echarts')) return 'echarts'
          if (id.includes('node_modules/element-plus')) return 'element-plus'
          if (id.includes('node_modules/vue') || id.includes('node_modules/vue-router') || id.includes('node_modules/pinia')) {
            return 'vue-vendor'
          }
        }
      }
    }
  }
})
