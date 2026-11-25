import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Le build est servi par FastAPI sur la même origine que l'API (pas de CORS).
// En développement, Vite relaie /api vers l'API locale.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "127.0.0.1",
    proxy: { "/api": `http://127.0.0.1:${process.env.IDR_API_PORT ?? "8000"}` },
  },
  // AG Grid Community complet (~1,5 Mo) : acceptable sur le réseau local, à découper plus tard.
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 2000 },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
