import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8100',
        changeOrigin: true,
        // SSE support: don't buffer the response, pass chunks through
        // as they arrive. Without these, /api/v1/chat's text/event-stream
        // gets held until the entire response completes (or times out),
        // and the browser's ReadableStream stalls.
        selfHandleResponse: false,
        proxyTimeout: 120_000,
        timeout: 120_000,
      },
    },
  },
})
