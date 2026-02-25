import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
      "@shared": path.resolve(__dirname, "src/shared")
    }
  },
  server: {
    host: "0.0.0.0",
    port: 3000,
    proxy: {
      "/api": {
        target: process.env.VITE_API_BASE_URL || "http://127.0.0.1:8000",
        changeOrigin: true
      },
      "/ws": {
        target: process.env.VITE_API_BASE_URL || "http://127.0.0.1:8000",
        changeOrigin: true,
        ws: true
      }
    }
  },
  preview: {
    host: "0.0.0.0",
    port: 3000
  }
});
