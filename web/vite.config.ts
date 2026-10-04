import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `npm run build` writes into the Python package, where `iagent ui` serves it from.
// `npm run dev` serves with hot reload and forwards /api to a running `iagent ui --no-browser`.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: fileURLToPath(new URL("../iagent/ui/static", import.meta.url)),
    emptyOutDir: true,
  },
  server: {
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
});
