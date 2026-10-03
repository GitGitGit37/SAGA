import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  server: {
    port: 5173,
    allowedHosts: ["localhost", "frontend"], // "frontend" = compose service name (used by headless checks)
    // Bind mounts from Windows don't deliver file events into the container.
    watch: process.env.VITE_POLL ? { usePolling: true, interval: 300 } : undefined,
    proxy: { "/api": process.env.BACKEND_URL ?? "http://localhost:8000" },
  },
});
