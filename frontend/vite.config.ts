import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // The API on the page's own address, as in production (the backend serves the built app): the
    // sign-in cookie then reaches every request, images and tiles included.
    proxy: { "/api": process.env.API_PROXY ?? "http://127.0.0.1:8088" },
  },
})
