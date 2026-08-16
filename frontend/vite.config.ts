import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

const noHmr = process.env.VITE_NO_HMR === "1";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    rollupOptions: {
      output: {
        // Split the large, rarely-changing dependencies into their own chunks so
        // they stay cached across deploys instead of being invalidated by any
        // application change. recharts is separate because only the Deep Dive
        // page pulls it in, so most sessions never download it.
        manualChunks: {
          recharts: ["recharts"],
          react: ["react", "react-dom", "react-router-dom"],
          query: ["@tanstack/react-query"],
        },
      },
    },
  },
  server: {
    // Bind IPv4 loopback only — avoids IPv6 localhost issues and plays
    // better with corporate PAC proxies (e.g. Walmart anycast-universal.pac).
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    hmr: noHmr
      ? false
      : {
          host: "127.0.0.1",
          port: 5173,
        },
    proxy: {
      "/api": {
        target: "http://127.0.0.1:9000",
        changeOrigin: true,
      },
      "/health": {
        target: "http://127.0.0.1:9000",
        changeOrigin: true,
      },
    },
  },
});
