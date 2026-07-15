import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    rollupOptions: {
      output: {
        // zxing is only needed when the scanner opens — keep it out of the main bundle
        manualChunks: { zxing: ['@zxing/browser', '@zxing/library'] },
      },
    },
  },
})
