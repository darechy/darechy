import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Прокси: /api и /health уходят в бэкенд на :8000, чтобы в dev не возиться с CORS.
// Открывать портал: http://localhost:5173
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
});
