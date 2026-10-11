import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Builds into the Python package so `photoctl serve` can serve it.
// Dev: `PHOTOCTL_TOKEN=$(uv run photoctl serve --print-token) npm run dev` with `photoctl serve` running.
export default defineConfig({
  plugins: [react()],
  build: { outDir: '../src/photoctl/web/dist', emptyOutDir: true, sourcemap: false },
  server: {
    host: '127.0.0.1',
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        headers: process.env.PHOTOCTL_TOKEN ? { Authorization: `Bearer ${process.env.PHOTOCTL_TOKEN}` } : {},
      },
    },
  },
})
