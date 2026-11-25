import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // Nginx serves hashed .js as JavaScript on all deployed versions.
        assetFileNames: asset => asset.names.includes('pdf.worker.min.mjs')
          ? 'assets/pdf.worker-[hash].js' : 'assets/[name]-[hash][extname]',
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': process.env.VITE_API_TARGET || 'http://127.0.0.1:8000',
      '/media': process.env.VITE_API_TARGET || 'http://127.0.0.1:8000',
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    globals: true,
    css: true,
    // The Ant Design/jsdom page tests are CPU-heavy and share browser globals.
    // Running files in parallel made the inventory correction test flaky on CI
    // runners even though it passed in isolation.
    fileParallelism: false,
    testTimeout: 15_000,
  },
})
