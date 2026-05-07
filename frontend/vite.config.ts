import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 3000,
  },
  // Build directly into the location the FastAPI app serves from, so
  // `npm run build` is a single step (no manual xcopy / cp afterwards)
  // and the packaged bundle picks up the freshest assets.
  build: {
    outDir: "../frontend_static",
    emptyOutDir: true,
  },
});
