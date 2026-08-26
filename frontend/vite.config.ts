import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The build lands inside the Python package, and the result is committed.
// Deployment is `git pull` + `pip install` onto a host with no Node on it, so
// anything not in the repository is not on the server. See DECISIONS.md D-016.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "../src/tms/ui/assets",
    emptyOutDir: true,
    // Hashed filenames: the console is served from the same origin as the API
    // with no CDN in front, and a stale cached bundle is the failure mode that
    // costs the most to diagnose.
    assetsDir: "static",
    sourcemap: false,
  },
  server: {
    // `npm run dev` talks to a locally running tms-api. Same-origin in
    // production, so the session cookie needs no special handling either way.
    proxy: {
      "/api": { target: "https://127.0.0.1:8443", changeOrigin: true, secure: false },
    },
  },
});
