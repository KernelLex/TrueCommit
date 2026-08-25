import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // The Python API (api/main.py) never gets CORS middleware — the
      // dashboard talks to it through this dev-server proxy instead, so
      // every fetch in src/api.js is prefixed with /api.
      // PK_API_PORT lets the API move when 8000 is taken (on some Windows
      // machines svchost permanently squats 8000).
      '/api': {
        target: `http://127.0.0.1:${process.env.PK_API_PORT || 8000}`,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
