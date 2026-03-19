import { defineConfig, loadEnv } from "vite"
import react from "@vitejs/plugin-react"

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "")
  const localApiBase = (env.VITE_API_BASE || "http://localhost:8000").trim()

  return {
    plugins: [react()],
    server: {
      port: 3000,
      proxy: {
        "/api": { target: localApiBase, changeOrigin: true },
        "/ws": { target: localApiBase.replace(/^http/, "ws"), ws: true },
      },
    },
  }
})
