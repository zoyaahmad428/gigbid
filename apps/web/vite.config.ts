import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev the API runs on :8000 (make api); in production FastAPI serves the built files itself.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": { target: "http://localhost:8000", changeOrigin: true } } },
});
