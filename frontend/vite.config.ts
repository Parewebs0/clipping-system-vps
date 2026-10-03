import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Built into ../app/static/mission-control, served by FastAPI StaticFiles at
// /mission-control (hash router, so no server-side fallback is needed).
// `npm run dev` proxies the API to MC_API_TARGET (default local uvicorn).
const apiTarget = process.env.MC_API_TARGET ?? 'http://127.0.0.1:8080'

export default defineConfig({
  base: '/mission-control/',
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(import.meta.dirname, './src') } },
  build: {
    outDir: '../app/static/mission-control',
    emptyOutDir: true,
    chunkSizeWarningLimit: 800,
  },
  server: {
    proxy: {
      '^/(mission-control/(overview|campaigns|jobs|pipeline|videos|clips)|campaigns|clips|jobs|candidates|health|worker)': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
})
