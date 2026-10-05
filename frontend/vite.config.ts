import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const backend = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    fs: { allow: [".."] },
    proxy: Object.fromEntries(
      ["/healthz", "/templates", "/matches", "/replays", "/openapi.json"].map((p) => [p, backend]),
    ),
  },
});
