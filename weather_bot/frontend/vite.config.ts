import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const base = process.env.VITE_BASE_PATH || '/'
const outDir = process.env.VITE_OUT_DIR || 'dist'

export default defineConfig({
  plugins: [react()],
  base,
  build: { outDir },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
