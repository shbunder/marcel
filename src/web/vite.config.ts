import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: { outDir: 'dist' },
  server: {
    port: 5173,
    proxy: {
      '/ws': { target: 'ws://localhost:7421', ws: true },
      '/health': 'http://localhost:7421',
      '/conversations': 'http://localhost:7421',
    },
  },
})
