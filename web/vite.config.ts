import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// base "./" lets the same build run at the companion root (http://127.0.0.1:8000/)
// and under a GitHub Pages sub-path (https://<user>.github.io/Shakti/).
export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: {
    outDir: "../rag_pipeline/webui",
    emptyOutDir: true,
  },
  server: { port: 5173, strictPort: true },
});
