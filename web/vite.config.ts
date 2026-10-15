import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Builds into the Python package so `shoebox serve` can serve it.
// Dev: `SHOEBOX_TOKEN=$(uv run shoebox serve --print-token) npm run dev` with `shoebox serve` running.
export default defineConfig({
  plugins: [react()],
  // Relative asset paths: the same build is served by the Mac at / and bundled in the phone app under /browse/.
  base: './',
  optimizeDeps: { exclude: ['@sqlite.org/sqlite-wasm'] },
  worker: { format: 'es' },
  build: { outDir: '../src/shoebox/web/dist', emptyOutDir: true, sourcemap: false },
  server: {
    host: '127.0.0.1',
    fs: { allow: ['..'] },
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        headers: process.env.SHOEBOX_TOKEN ? { Authorization: `Bearer ${process.env.SHOEBOX_TOKEN}` } : {},
      },
    },
  },
})
