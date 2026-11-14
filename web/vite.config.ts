import { fileURLToPath, URL } from "node:url";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { validateAuthConfig } from "./src/lib/auth-config.ts";

// base "./" lets the same build run at the companion root (http://127.0.0.1:8000/)
// and under a GitHub Pages sub-path (https://<user>.github.io/Shakti/).
export default defineConfig({
  base: "./",
  plugins: [
    {
      name: "validate-supabase-config",
      config(_config, { mode }) {
        const environment = loadEnv(mode, fileURLToPath(new URL(".", import.meta.url)), "VITE_SUPABASE_");
        const result = validateAuthConfig(environment.VITE_SUPABASE_URL?.trim(), environment.VITE_SUPABASE_PUBLISHABLE_KEY?.trim());
        if (result.error) throw new Error(result.error);
      },
    },
    react(),
    tailwindcss(),
  ],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: {
    outDir: "../rag_pipeline/webui",
    emptyOutDir: true,
  },
  server: { port: 5173, strictPort: true },
});
