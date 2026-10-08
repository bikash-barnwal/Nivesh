import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// The API runs on 127.0.0.1:15000 (`uv run nivesh serve`); proxying keeps the browser same-origin, so no CORS in dev.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  test: { include: ['src/**/*.test.ts'], environment: 'node' },
  server: { port: 15001, strictPort: true, proxy: { '/api': 'http://127.0.0.1:15000', '/health': 'http://127.0.0.1:15000' } },
  preview: { port: 15002, strictPort: true },
})
